"""WakaTime Insights — SSH to staging, read daily coding-time archives, build weekly stats.

Reads WakaTime daily archive JSON files from the Hetzner staging server and
converts a week of coding activity into ScrapedArticle format so the pipeline
can generate "building in public" posts grounded in real numbers — hours coded,
languages, projects, and AI usage (tokens, sessions, cost).

Second "real work" source after git_insights.py. Same SSH-to-staging pattern
(public IP, Docker-safe — not Tailscale).
"""

import glob
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field

from src.observability import get_client, observe
from src.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

# Default SSH + archive location (see CLAUDE.md / plan Phase 2.75)
DEFAULT_HOST = "root@178.156.214.8"
DEFAULT_ARCHIVE_DIR = "/srv/lubot-staging/.wakatime-archive"
DEFAULT_DAYS_BACK = 7

# Same as git_insights: when the worker runs on the staging box (archive dir
# mounted in), read the JSON files directly instead of SSHing to ourselves.
STAGING_LOCAL = os.getenv("STAGING_LOCAL", "").lower() in ("1", "true", "yes")

# Markers used to delimit the SSH stdout (archives, then optional weekly notes)
FILE_MARKER = "===WAKA-FILE==="
NOTES_MARKER = "===WAKA-NOTES==="


def _f(value, default: float = 0.0) -> float:
    """Cast a WakaTime string value to float, tolerating None/garbage."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default: int = 0) -> int:
    """Cast a WakaTime string value to int, tolerating None/garbage/decimals."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass
class DayStats:
    """One day of coding activity parsed from a WakaTime daily archive."""

    date: str
    total_seconds: float = 0.0
    by_language: dict[str, float] = field(default_factory=dict)
    by_project: dict[str, float] = field(default_factory=dict)
    ai_input_tokens: int = 0
    ai_output_tokens: int = 0
    ai_sessions: int = 0
    ai_prompt_events: int = 0
    ai_cost: float = 0.0


@dataclass
class WeeklyStats:
    """Aggregated coding activity for a week — the writer's source of truth."""

    days_active: int = 0
    total_seconds: float = 0.0
    by_language: dict[str, float] = field(default_factory=dict)
    by_project: dict[str, float] = field(default_factory=dict)
    ai_input_tokens: int = 0
    ai_output_tokens: int = 0
    ai_sessions: int = 0
    ai_prompt_events: int = 0
    ai_cost: float = 0.0
    start_date: str = ""
    end_date: str = ""
    prev_total_seconds: float = 0.0  # prior week's total, for the momentum delta

    @property
    def total_hours(self) -> float:
        return self.total_seconds / 3600

    @property
    def total_delta_pct(self) -> float | None:
        """Percent change in coding time vs the prior week. None if no prior data."""
        if self.prev_total_seconds <= 0:
            return None
        return (self.total_seconds - self.prev_total_seconds) / self.prev_total_seconds * 100

    @property
    def top_language(self) -> str | None:
        return max(self.by_language, key=self.by_language.get) if self.by_language else None

    @property
    def top_project(self) -> str | None:
        return max(self.by_project, key=self.by_project.get) if self.by_project else None


def _fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xh Ym'."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


def _parse_day(archive: dict) -> DayStats | None:
    """Parse one daily archive dict (data[0]) into a DayStats. None if no data."""
    data = archive.get("data")
    if not data:
        return None
    day = data[0]
    gt = day.get("grand_total", {})

    by_language: dict[str, float] = {}
    for lang in day.get("languages", []):
        name = lang.get("name")
        if name:
            by_language[name] = _f(lang.get("total_seconds"))

    by_project: dict[str, float] = {}
    for proj in day.get("projects", []):
        name = proj.get("name")
        if name:
            by_project[name] = _f(proj.get("total_seconds"))

    return DayStats(
        date=day.get("range", {}).get("date", ""),
        total_seconds=_f(gt.get("total_seconds")),
        by_language=by_language,
        by_project=by_project,
        ai_input_tokens=_i(gt.get("ai_input_tokens")),
        ai_output_tokens=_i(gt.get("ai_output_tokens")),
        ai_sessions=_i(gt.get("ai_sessions")),
        ai_prompt_events=_i(gt.get("ai_prompt_events_total")),
        ai_cost=_f(gt.get("ai_agent_total_cost")),
    )


def _aggregate_week(days: list[DayStats]) -> WeeklyStats:
    """Sum a list of DayStats into a single WeeklyStats."""
    stats = WeeklyStats()
    stats.days_active = sum(1 for d in days if d.total_seconds > 0)

    for d in days:
        stats.total_seconds += d.total_seconds
        stats.ai_input_tokens += d.ai_input_tokens
        stats.ai_output_tokens += d.ai_output_tokens
        stats.ai_sessions += d.ai_sessions
        stats.ai_prompt_events += d.ai_prompt_events
        stats.ai_cost += d.ai_cost
        for name, secs in d.by_language.items():
            stats.by_language[name] = stats.by_language.get(name, 0.0) + secs
        for name, secs in d.by_project.items():
            stats.by_project[name] = stats.by_project.get(name, 0.0) + secs

    dates = sorted(d.date for d in days if d.date)
    if dates:
        stats.start_date = dates[0]
        stats.end_date = dates[-1]
    return stats


def _split_weeks(days: list[DayStats], days_back: int) -> tuple[list[DayStats], list[DayStats]]:
    """Partition days into (current_week, prior_week) by recency, most recent first."""
    ordered = sorted(days, key=lambda d: d.date, reverse=True)
    return ordered[:days_back], ordered[days_back : days_back * 2]


def _split_archives(raw: str) -> list[dict]:
    """Split raw SSH stdout (FILE_MARKER-delimited) into archive dicts. Skips malformed."""
    out: list[dict] = []
    for part in raw.split(FILE_MARKER):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(json.loads(part))
        except json.JSONDecodeError:
            logger.debug("Skipping malformed WakaTime archive segment")
            continue
    return out


def _top_n(mapping: dict[str, float], total: float, n: int = 3) -> list[tuple[str, float, float]]:
    """Top-n (name, seconds, percent_of_total) entries, descending by seconds."""
    items = sorted(mapping.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [(name, secs, (secs / total * 100) if total else 0.0) for name, secs in items]


def _build_title(stats: WeeklyStats) -> str:
    lang = stats.top_language or "code"
    return f"Building in public: {_fmt_duration(stats.total_seconds)} coded this week, mostly {lang}"


def _build_summary(stats: WeeklyStats, include_costs: bool = True, notes: str = "") -> str:
    """Build a structured, real-numbers summary for the writer (anti-hallucination)."""
    parts = [
        f"MY CODING WEEK ({stats.start_date} to {stats.end_date}):",
        f"Total time coding: {_fmt_duration(stats.total_seconds)} across {stats.days_active} active days",
    ]
    delta = stats.total_delta_pct
    if delta is not None:
        direction = "up" if delta >= 0 else "down"
        parts.append(f"Momentum: {direction} {abs(delta):.0f}% vs the week before")
    parts += ["", "TOP LANGUAGES:"]
    for name, secs, pct in _top_n(stats.by_language, stats.total_seconds):
        parts.append(f"  - {name}: {_fmt_duration(secs)} ({pct:.0f}%)")

    parts += ["", "TOP PROJECTS:"]
    for name, secs, pct in _top_n(stats.by_project, stats.total_seconds):
        parts.append(f"  - {name}: {_fmt_duration(secs)} ({pct:.0f}%)")

    parts += [
        "",
        "AI PAIR-PROGRAMMING (I build with AI coding agents):",
        f"  - AI coding sessions: {stats.ai_sessions}",
        f"  - Prompts sent: {stats.ai_prompt_events}",
        f"  - Input tokens: {stats.ai_input_tokens:,}",
        f"  - Output tokens: {stats.ai_output_tokens:,}",
    ]
    if include_costs:
        parts.append(f"  - AI agent cost: ${stats.ai_cost:,.2f}")

    if notes:
        parts += ["", "MY NOTES THIS WEEK:", notes]

    parts += [
        "",
        "IMPORTANT: These are REAL numbers from my WakaTime tracker. "
        "Use them exactly as given. Do NOT invent or inflate any figure. "
        "Write a building-in-public post — honest, specific, no hype, no ad copy.",
    ]
    return "\n".join(parts)


def build_screenshot_fields(stats: WeeklyStats, include_costs: bool = True) -> dict:
    """Adapt a WeeklyStats into kwargs for take_wakatime_screenshot (keeps formatting DRY)."""
    languages = [(n, _fmt_duration(s), p) for n, s, p in _top_n(stats.by_language, stats.total_seconds)]
    projects = [(n, _fmt_duration(s), p) for n, s, p in _top_n(stats.by_project, stats.total_seconds)]

    delta = stats.total_delta_pct
    if delta is None:
        momentum = ""
    elif delta >= 0:
        momentum = f"up {delta:.0f}%"
    else:
        momentum = f"down {abs(delta):.0f}%"

    return {
        "total_time": _fmt_duration(stats.total_seconds),
        "days_active": stats.days_active,
        "languages": languages,
        "projects": projects,
        "ai_sessions": stats.ai_sessions,
        "ai_prompts": stats.ai_prompt_events,
        "ai_tokens": stats.ai_input_tokens,
        "ai_cost": stats.ai_cost if include_costs else None,
        "momentum": momentum,
        "date_range": f"{stats.start_date} to {stats.end_date}",
    }


class WakaTimeInsights:
    """Reads WakaTime daily archives from staging and builds a weekly stats article."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        archive_dir: str = DEFAULT_ARCHIVE_DIR,
        days_back: int = DEFAULT_DAYS_BACK,
        include_costs: bool = True,
        local: bool | None = None,
    ):
        self.host = host
        self.archive_dir = archive_dir
        self.days_back = days_back
        self.include_costs = include_costs
        self.local = STAGING_LOCAL if local is None else local
        self.weekly_stats: WeeklyStats | None = None

    @observe()
    def get_weekly_stats(self) -> ScrapedArticle | None:
        """SSH to staging, read last N daily archives, return weekly stats as ScrapedArticle.

        Returns None if SSH fails or no coding activity was archived.
        """
        try:
            raw = self._fetch_archives()
        except (subprocess.CalledProcessError, OSError) as e:
            logger.warning("SSH to staging for WakaTime archives failed: %s", e)
            return None

        archives_raw, _, notes_raw = raw.partition(NOTES_MARKER)
        days = [d for d in (_parse_day(a) for a in _split_archives(archives_raw)) if d]
        if not days:
            logger.info("No WakaTime archives found in last %d days", self.days_back)
            return None

        current_days, prior_days = _split_weeks(days, self.days_back)
        stats = _aggregate_week(current_days)
        if stats.total_seconds <= 0:
            logger.info("WakaTime week had zero coding time")
            return None
        stats.prev_total_seconds = _aggregate_week(prior_days).total_seconds
        self.weekly_stats = stats

        title = _build_title(stats)
        summary = _build_summary(stats, self.include_costs, notes_raw.strip())

        # Score the insight quality in Langfuse (more active days = richer story)
        try:
            get_client().score_current_trace(
                name="wakatime_insight_quality",
                value=min(1.0, stats.days_active / 7),
                data_type="NUMERIC",
                comment=f"{stats.days_active} active days, {_fmt_duration(stats.total_seconds)}",
            )
        except Exception:
            logger.debug("Langfuse wakatime_insight_quality scoring failed", exc_info=True)

        return ScrapedArticle(
            title=title,
            url="https://wakatime.com/dashboard",
            summary=summary,
            source="wakatime:lubot",
            published_at=None,
            source_priority=0,  # own work = top priority
        )

    def _read_local_archives(self) -> str:
        """Read the last 2*N daily archives + latest notes from the mounted dir.

        Produces the same FILE_MARKER/NOTES_MARKER-delimited string the SSH path
        returns, so the parser is identical for both modes.
        """
        paths = sorted(
            glob.glob(os.path.join(self.archive_dir, "wakatime-*.json")),
            key=os.path.getmtime,
            reverse=True,
        )[: self.days_back * 2]

        parts: list[str] = []
        for path in paths:
            try:
                with open(path, encoding="utf-8") as fh:
                    parts.append(FILE_MARKER)
                    parts.append(fh.read())
            except OSError:
                logger.debug("Could not read WakaTime archive %s", path)

        parts.append(NOTES_MARKER)
        notes = sorted(
            glob.glob(os.path.join(self.archive_dir, "notes", "*.md")),
            key=os.path.getmtime,
            reverse=True,
        )
        if notes:
            try:
                with open(notes[0], encoding="utf-8") as fh:
                    parts.append(fh.read())
            except OSError:
                logger.debug("Could not read WakaTime notes %s", notes[0])

        return "\n".join(parts)

    def _fetch_archives(self) -> str:
        """Read WakaTime archives from staging — locally if mounted, else via SSH.

        SSH path: cat the last 2*N daily archives + latest weekly notes.

        Pulls two weeks so we can compute the week-over-week momentum delta.
        Output: FILE_MARKER + json per archive, then NOTES_MARKER + notes text.
        Trailing `true` keeps the remote exit code 0 when the host is reachable,
        so a missing notes file never looks like an SSH failure.
        """
        if self.local:
            return self._read_local_archives()

        remote = (
            f"cd {self.archive_dir} && "
            f"for f in $(ls -t wakatime-*.json 2>/dev/null | head -{self.days_back * 2}); do "
            f'echo "{FILE_MARKER}"; cat "$f"; done; '
            f'echo "{NOTES_MARKER}"; '
            f"notes=$(ls -t notes/*.md 2>/dev/null | head -1); "
            f'[ -n "$notes" ] && cat "$notes"; true'
        )
        cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.host} '{remote}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return result.stdout
