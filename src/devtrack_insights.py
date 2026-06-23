"""DevTrack Insights — read Lubo's weekly DevTrack-AI build report (Phase 2.11).

DevTrack-AI generates a rich weekly report (the one Lubo actually gets) as clean
markdown at /srv/lubot-staging/.wakatime-archive/reports/wakatime-weekly-YYYY-Www.md.
The publisher worker mounts that path read-only. This module parses the latest report
into typed metrics for the Building in Public post + luxury stat-card — far richer and
more accurate than the raw WakaTime read (total/code hours, commits, lines, tests, AI
orchestration). Parsing is pure; the file read is the only boundary.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.observability import get_client, observe
from src.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

DEFAULT_REPORTS_DIR = Path("/srv/lubot-staging/.wakatime-archive/reports")


@dataclass
class DevTrackReport:
    """Parsed weekly build report — the writer's + card's source of truth."""

    period_label: str = ""  # e.g. "Week 25"
    date_range: str = ""  # e.g. "Jun 15 to Jun 21, 2026"
    total_hours: float = 0.0
    code_hours: float = 0.0
    planning_hours: float = 0.0
    days_worked: str = ""
    momentum: str = ""  # e.g. "-8.9h (-14%)"
    commits: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    files_changed: int = 0
    tests_added: int = 0
    total_tests: int = 0
    ai_sessions: int = 0
    ai_output_tokens: int = 0
    by_technology: list[tuple[str, float]] = field(default_factory=list)
    by_project: list[tuple[str, float]] = field(default_factory=list)
    metrics: dict[str, str] = field(default_factory=dict)  # all 2-col rows, cleaned


def _clean(s: str) -> str:
    """Strip markdown bold + common HTML entities from a cell."""
    s = s.replace("**", "").replace("&amp;", "&").replace("&middot;", "·").replace("&mdash;", "—")
    return s.strip()


def _num(s: str, default: int = 0) -> int:
    """First integer in a string, commas stripped (e.g. '25,298,310 tokens' -> 25298310)."""
    m = re.search(r"-?\d[\d,]*", s or "")
    return int(m.group().replace(",", "")) if m else default


def _hours(s: str, default: float = 0.0) -> float:
    """Leading hours float before 'h' (e.g. '81.6h' -> 81.6)."""
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else default


def _parse_list_section(md: str, header: str) -> list[tuple[str, float]]:
    """Parse a '## <header>' section of '- name — Xh' bullet lines into [(name, hours)]."""
    out: list[tuple[str, float]] = []
    in_section = False
    for line in md.splitlines():
        if line.startswith("## "):
            in_section = header.lower() in line.lower()
            continue
        if in_section:
            m = re.match(r"\s*-\s*(.+?)\s*[—-]\s*([\d.]+)\s*h\s*$", line)
            if m:
                out.append((_clean(m.group(1)), float(m.group(2))))
    return out


def parse_report(md: str) -> DevTrackReport:
    """Parse a DevTrack weekly markdown report into typed metrics. Tolerant of missing rows."""
    r = DevTrackReport()

    period = re.search(r"Reporting Period:\*\*\s*(Week\s*\d+)\s*(?:&mdash;|[—-])+\s*(.+)", md)
    if period:
        r.period_label = _clean(period.group(1))
        r.date_range = _clean(period.group(2))

    # All 2-column "| label | value |" rows (3-col tables like Project Portfolio are skipped).
    metrics: dict[str, str] = {}
    for label, value in re.findall(r"^\|([^|\n]+)\|([^|\n]+)\|\s*$", md, re.MULTILINE):
        k, v = _clean(label), _clean(value)
        if k and v and set(k) != {"-"}:  # skip the '| --- | --- |' separator
            metrics[k] = v
    r.metrics = metrics

    g = lambda *keys: next((metrics[k] for k in keys if k in metrics), "")  # noqa: E731
    r.total_hours = _hours(g("Total Working Hours"))
    r.code_hours = _hours(g("Code & Build Time"))
    r.planning_hours = _hours(g("Planning, Research & Architecture"))
    r.days_worked = g("Days Worked")
    r.momentum = g("Code & Build vs Prior Week")
    r.commits = _num(g("Git Commits"))
    r.tests_added = _num(g("Tests Written This Week"))
    r.total_tests = _num(g("Total Tests in Repo"))
    r.ai_sessions = _num(g("AI Orchestration Sessions"))
    r.ai_output_tokens = _num(g("AI Output Generated"))
    lines = g("Lines Shipped")
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", lines)]
    if len(nums) >= 3:
        r.lines_added, r.lines_deleted, r.files_changed = nums[0], nums[1], nums[2]

    r.by_technology = _parse_list_section(md, "Hours by Technology")
    r.by_project = _parse_list_section(md, "Hours by Project")
    return r


def _build_summary(r: DevTrackReport) -> str:
    """Real-numbers summary for the writer (exact figures + anti-hallucination)."""
    parts = [f"MY BUILD WEEK ({r.period_label} · {r.date_range}):"]
    parts.append(f"  - {r.total_hours:g}h total working time ({r.code_hours:g}h coding/build)")
    if r.days_worked:
        parts.append(f"  - {r.days_worked} days worked")
    if r.momentum:
        parts.append(f"  - code/build vs prior week: {r.momentum}")
    parts.append(
        f"  - {r.commits} git commits, +{r.lines_added:,}/-{r.lines_deleted:,} lines across {r.files_changed} files"
    )
    if r.tests_added:
        parts.append(f"  - {r.tests_added} new tests written (RECR: test-first)")
    if r.ai_sessions:
        parts.append(f"  - {r.ai_sessions} AI orchestration sessions, {r.ai_output_tokens:,} output tokens")
    if r.by_technology:
        parts.append("  - top tech: " + ", ".join(f"{n} {h:g}h" for n, h in r.by_technology[:3]))
    if r.by_project:
        parts.append("  - top projects: " + ", ".join(f"{n} {h:g}h" for n, h in r.by_project[:3]))
    parts += [
        "",
        "IMPORTANT: These are REAL figures from my own weekly build report. Use them EXACTLY "
        "as given. Do NOT invent or change any number. Building-in-public: honest, specific, "
        "proud not bragging.",
    ]
    return "\n".join(parts)


def find_latest_report(reports_dir: Path) -> Path | None:
    """Return the newest weekly markdown report (highest week), or None."""
    try:
        reports = sorted(reports_dir.glob("wakatime-weekly-*.md"))
    except Exception:
        return None
    return reports[-1] if reports else None


class DevTrackInsights:
    """Read the latest DevTrack weekly report -> ScrapedArticle for Building in Public."""

    def __init__(self, reports_dir: Path = DEFAULT_REPORTS_DIR):
        self.reports_dir = reports_dir
        self.report: DevTrackReport | None = None

    @observe()
    def get_weekly_report(self) -> ScrapedArticle | None:
        """Parse the latest report into a ScrapedArticle. None if no report (caller falls back)."""
        path = find_latest_report(self.reports_dir)
        if not path:
            logger.info("No DevTrack weekly report found in %s", self.reports_dir)
            return None
        try:
            report = parse_report(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to parse DevTrack report %s", path, exc_info=True)
            return None
        if report.total_hours <= 0 and report.code_hours <= 0:
            logger.info("DevTrack report %s had no usable hours", path)
            return None
        self.report = report

        try:
            get_client().score_current_trace(
                name="devtrack_report_quality",
                value=1.0,
                data_type="NUMERIC",
                comment=f"{report.period_label}: {report.total_hours}h, {report.commits} commits",
            )
        except Exception:
            logger.debug("Langfuse devtrack scoring failed", exc_info=True)

        return ScrapedArticle(
            title=f"Build week: {report.period_label} — {report.total_hours:g}h, {report.commits} commits",
            url="",
            summary=_build_summary(report),
            source="devtrack:weekly",
            published_at=None,
            source_priority=0,
        )


def build_devtrack_screenshot_fields(report: DevTrackReport) -> dict:
    """Adapt a DevTrackReport into kwargs for the luxury Building-in-Public card."""
    return {
        "metrics": {
            "total_hours": report.total_hours,
            "code_hours": report.code_hours,
            "commits": report.commits,
            "files_changed": report.files_changed,
            "lines_added": report.lines_added,
            "lines_deleted": report.lines_deleted,
            "tests_added": report.tests_added,
            "ai_output_tokens": report.ai_output_tokens,
            "ai_sessions": report.ai_sessions,
            "days_worked": report.days_worked,
            "momentum": report.momentum,
        },
        "date_range": f"{report.period_label} · {report.date_range}",
    }
