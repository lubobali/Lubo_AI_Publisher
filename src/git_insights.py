"""Git Insights — SSH to staging, parse git log, extract meaningful features.

Reads real commit history from Hetzner staging server and converts it into
ScrapedArticle format so the pipeline can generate "My Agent" posts grounded
in actual work, not scraped news.
"""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

from src.observability import get_client, observe
from src.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

# Default SSH target
DEFAULT_HOST = "root@178.156.214.8"
DEFAULT_REPO = "/srv/lubot-staging/services-agent-api"
DEFAULT_DAYS_BACK = 7

# Commit messages matching these patterns are noise
NOISE_PATTERNS = [
    re.compile(r"^fix(ed|es|ing)?\s+typo", re.IGNORECASE),
    re.compile(r"^wip$", re.IGNORECASE),
    re.compile(r"^wip\b", re.IGNORECASE),
    re.compile(r"^merge\s+branch", re.IGNORECASE),
    re.compile(r"^merge\s+pull\s+request", re.IGNORECASE),
    re.compile(r"^update\s+requirements", re.IGNORECASE),
    re.compile(r"^bump\s+version", re.IGNORECASE),
    re.compile(r"^update\s+\.env", re.IGNORECASE),
    re.compile(r"^update\s+readme", re.IGNORECASE),
    re.compile(r"^fix\s+lint", re.IGNORECASE),
    re.compile(r"^format(ting)?(\s|$)", re.IGNORECASE),
    re.compile(r"^minor\s+fix", re.IGNORECASE),
    re.compile(r"^clean\s*up", re.IGNORECASE),
]

# Keywords for grouping commits into feature areas
FEATURE_KEYWORDS = {
    "stock": ["stock", "ticker", "fundamental", "portfolio", "yfinance", "quote", "market"],
    "pdf": ["pdf", "rag", "chunk", "vector", "faiss", "embed", "document"],
    "auth": ["auth", "login", "oauth", "supabase", "stripe", "payment", "subscription"],
    "frontend": ["frontend", "react", "component", "ui", "tailwind", "vite", "dashboard"],
    "analytics": ["analytics", "metric", "engagement", "performance", "tracking"],
    "pipeline": ["pipeline", "scheduler", "cron", "worker", "deploy"],
    "llm": ["llm", "nvidia", "nemotron", "model", "prompt", "writer", "inference"],
    "api": ["api", "route", "endpoint", "fastapi", "sse", "streaming", "websocket"],
    "test": ["test", "pytest", "ci", "coverage", "fixture"],
    "data": ["database", "postgres", "migration", "schema", "table", "query"],
}


@dataclass
class GitCommit:
    """A single parsed git commit."""

    hash: str
    date: datetime
    message: str
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    changed_files: list[str] = field(default_factory=list)


def _parse_git_log(log_output: str, numstat_output: str) -> list[GitCommit]:
    """Parse git log output into GitCommit objects.

    log_output: from --format='%h|%aI|%s'
    numstat_output: from --numstat with --- separators between commits
    """
    if not log_output.strip():
        return []

    commits = []
    lines = log_output.strip().split("\n")

    # Parse numstat blocks (separated by ---)
    # With --pretty="format:---", each commit starts with --- then file stats
    numstat_blocks: list[list[str]] = []
    if numstat_output.strip():
        current_block: list[str] = []
        for line in numstat_output.strip().split("\n"):
            if line.strip() == "---":
                # Save previous block if non-empty, start new one
                if current_block:
                    numstat_blocks.append(current_block)
                current_block = []
            elif line.strip():
                current_block.append(line.strip())
        # Don't forget last block if no trailing ---
        if current_block:
            numstat_blocks.append(current_block)

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        parts = line.split("|", 2)
        if len(parts) < 3:
            continue

        commit_hash, date_str, message = parts

        try:
            commit_date = datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue

        # Parse numstat for this commit
        files_changed = 0
        lines_added = 0
        lines_deleted = 0
        changed_files: list[str] = []

        if i < len(numstat_blocks):
            block = numstat_blocks[i]
            for stat_line in block:
                stat_parts = stat_line.split("\t")
                if len(stat_parts) >= 3:
                    try:
                        added = int(stat_parts[0]) if stat_parts[0] != "-" else 0
                        deleted = int(stat_parts[1]) if stat_parts[1] != "-" else 0
                        lines_added += added
                        lines_deleted += deleted
                        files_changed += 1
                        changed_files.append(stat_parts[2])
                    except ValueError:
                        continue

        commits.append(
            GitCommit(
                hash=commit_hash.strip(),
                date=commit_date,
                message=message.strip(),
                files_changed=files_changed,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                changed_files=changed_files,
            )
        )

    return commits


def _filter_noise(commits: list[GitCommit]) -> list[GitCommit]:
    """Remove noise commits (typos, wip, merges, dep bumps)."""
    result = []
    for c in commits:
        if any(p.search(c.message) for p in NOISE_PATTERNS):
            continue
        result.append(c)
    return result


def _group_by_feature(commits: list[GitCommit]) -> dict[str, list[GitCommit]]:
    """Group commits by feature area based on message + file path keywords."""
    groups: dict[str, list[GitCommit]] = {}

    for commit in commits:
        # Build searchable text from message + file paths
        searchable = commit.message.lower() + " " + " ".join(commit.changed_files).lower()

        matched_feature = "other"
        for feature_name, keywords in FEATURE_KEYWORDS.items():
            if any(kw in searchable for kw in keywords):
                matched_feature = feature_name
                break

        groups.setdefault(matched_feature, []).append(commit)

    return groups


def _pick_biggest_feature(groups: dict[str, list[GitCommit]]) -> tuple[str, list[GitCommit]]:
    """Pick the feature group with the most total lines changed.

    Returns (feature_name, commits). Empty tuple if no groups.
    """
    if not groups:
        return ("", [])

    best_name = ""
    best_commits: list[GitCommit] = []
    best_lines = -1

    for name, commits in groups.items():
        total_lines = sum(c.lines_added + c.lines_deleted for c in commits)
        if total_lines > best_lines:
            best_lines = total_lines
            best_name = name
            best_commits = commits

    return (best_name, best_commits)


def _pick_best_commit(commits: list[GitCommit]) -> GitCommit:
    """Pick the single most interesting commit (most lines changed)."""
    return max(commits, key=lambda c: c.lines_added + c.lines_deleted)


def _build_feature_title(feature_name: str, commits: list[GitCommit]) -> str:
    """Build a human-readable title from the best commit."""
    return _pick_best_commit(commits).message


def _build_feature_summary(feature_name: str, commits: list[GitCommit]) -> str:
    """Build a focused summary around ONE commit for the writer.

    Gives the writer exactly one feature to talk about with real numbers.
    No commit dump — just the one story.
    """
    best = _pick_best_commit(commits)

    # List the actual files changed (strip test files for a cleaner story)
    src_files = [f for f in best.changed_files if "/test" not in f and not f.startswith("test")]
    file_list = ", ".join(src_files[:5]) if src_files else ", ".join(best.changed_files[:5])

    net = best.lines_added - best.lines_deleted
    action = "net addition" if net > 0 else "net deletion (cleanup/rewrite)" if net < 0 else "pure refactor"

    parts = [
        f"THIS WEEK I BUILT: {best.message}",
        f"Date: {best.date.strftime('%B %d, %Y')}",
        f"Lines added: +{best.lines_added}",
        f"Lines deleted: -{best.lines_deleted}",
        f"Net: {'+' if net > 0 else ''}{net} lines ({action})",
        f"Files touched: {best.files_changed} ({file_list})",
        "",
        "CONTEXT (other related commits this week):",
    ]
    for c in commits:
        if c.hash != best.hash:
            parts.append(f"  - {c.message} ({c.date.strftime('%b %d')})")

    parts.append("")
    parts.append(
        "IMPORTANT: Write about the ONE feature above. "
        "Use the exact numbers (+lines/-lines, file count). "
        "The related commits are just context — do NOT list them all in the post."
    )

    return "\n".join(parts)


class GitInsights:
    """Fetches and analyzes git history from staging server."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        repo_path: str = DEFAULT_REPO,
        days_back: int = DEFAULT_DAYS_BACK,
    ):
        self.host = host
        self.repo_path = repo_path
        self.days_back = days_back
        self.best_commit: GitCommit | None = None

    @observe()
    def get_latest_feature(self) -> ScrapedArticle | None:
        """SSH to staging, parse git log, return biggest feature as ScrapedArticle.

        Returns None if SSH fails or no meaningful commits found.
        """
        try:
            raw_log, raw_numstat = self._fetch_git_log()
        except (subprocess.CalledProcessError, OSError) as e:
            logger.warning("SSH to staging failed: %s", e)
            return None

        if not raw_log.strip():
            logger.info("No commits in the last %d days", self.days_back)
            return None

        commits = _parse_git_log(raw_log, raw_numstat)
        if not commits:
            return None

        filtered = _filter_noise(commits)
        if not filtered:
            logger.info("All %d commits were noise", len(commits))
            return None

        groups = _group_by_feature(filtered)
        feature_name, feature_commits = _pick_biggest_feature(groups)

        if not feature_commits:
            return None

        self.best_commit = _pick_best_commit(feature_commits)
        title = _build_feature_title(feature_name, feature_commits)
        summary = _build_feature_summary(feature_name, feature_commits)

        # Score the insight quality in Langfuse
        try:
            quality = min(1.0, len(feature_commits) / 5)  # more commits = richer story
            get_client().score_current_trace(
                name="git_insight_quality",
                value=quality,
                data_type="NUMERIC",
                comment=f"{len(feature_commits)} commits in {feature_name}",
            )
        except Exception:
            logger.debug("Langfuse git_insight_quality scoring failed", exc_info=True)

        # Build the Forgejo URL for the repo
        repo_name = self.repo_path.rstrip("/").split("/")[-1]
        url = f"https://git.lubot.ai/lubot/{repo_name}"

        return ScrapedArticle(
            title=title,
            url=url,
            summary=summary,
            source=f"git:{self.repo_path.rstrip('/').split('/')[-2]}-{repo_name}"
            if "/" in self.repo_path
            else f"git:{repo_name}",
            published_at=feature_commits[0].date if feature_commits else None,
            source_priority=0,  # own work = top priority
        )

    def _fetch_git_log(self) -> tuple[str, str]:
        """SSH to staging and run git log. Returns (log_output, numstat_output)."""
        since = f"{self.days_back} days ago"

        # Fetch formatted log
        log_cmd = (
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.host} "
            f'\'cd {self.repo_path} && git log --since="{since}" --format="%h|%aI|%s"\''
        )
        log_result = subprocess.run(log_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if log_result.returncode != 0:
            raise subprocess.CalledProcessError(log_result.returncode, log_cmd)

        # Fetch numstat (lines added/deleted per file, --- between commits)
        numstat_cmd = (
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.host} "
            f'\'cd {self.repo_path} && git log --since="{since}" --pretty="format:---" --numstat\''
        )
        numstat_result = subprocess.run(numstat_cmd, shell=True, capture_output=True, text=True, timeout=30)

        return (log_result.stdout, numstat_result.stdout)
