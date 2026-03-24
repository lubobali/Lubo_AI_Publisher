"""Tests for git_insights — SSH to staging, parse git log, extract features."""

import subprocess
from unittest.mock import MagicMock, patch

from src.git_insights import (
    GitInsights,
    _filter_noise,
    _group_by_feature,
    _parse_git_log,
    _pick_biggest_feature,
)
from src.scraper import ScrapedArticle

# ---------------------------------------------------------------------------
# Sample git log output (--format used by the module)
# ---------------------------------------------------------------------------
SAMPLE_GIT_LOG = """\
abc1234|2026-03-22T14:30:00+00:00|Add stock fundamental analysis with 15 financial metrics
def5678|2026-03-22T10:15:00+00:00|Fix typo in README
aaa1111|2026-03-21T18:00:00+00:00|Implement real-time SSE streaming for stock quotes
bbb2222|2026-03-21T16:45:00+00:00|wip
ccc3333|2026-03-20T12:00:00+00:00|Add stock fundamental analysis endpoint to FastAPI routes
ddd4444|2026-03-20T09:30:00+00:00|Merge branch 'develop' into main
eee5555|2026-03-19T22:00:00+00:00|Implement PDF RAG chunking with 500-word sliding window
fff6666|2026-03-19T20:00:00+00:00|Add FAISS vector search for PDF chunks
ggg7777|2026-03-18T15:00:00+00:00|Update requirements.txt"""

SAMPLE_NUMSTAT = """\
15\t3\tsrc/stock/fundamental.py
2\t1\ttests/test_fundamental.py
---
1\t1\tREADME.md
---
8\t0\tsrc/stock/sse_streaming.py
3\t1\tsrc/stock/routes.py
---
---
5\t0\tsrc/stock/routes.py
---
---
12\t2\tsrc/pdf_rag/chunker.py
4\t0\ttests/test_chunker.py
---
10\t1\tsrc/pdf_rag/vector_search.py
---
1\t1\trequirements.txt
---"""


class TestParseGitLog:
    """Test parsing raw git log output into GitCommit objects."""

    def test_parses_commits(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        assert len(commits) == 9
        assert commits[0].hash == "abc1234"
        assert commits[0].message == "Add stock fundamental analysis with 15 financial metrics"
        assert commits[0].files_changed == 2
        assert commits[0].lines_added == 17
        assert commits[0].lines_deleted == 4

    def test_parses_datetime(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        assert commits[0].date.year == 2026
        assert commits[0].date.month == 3
        assert commits[0].date.day == 22

    def test_empty_log(self):
        commits = _parse_git_log("", "")
        assert commits == []

    def test_malformed_line_skipped(self):
        log = "bad-line-no-pipes\nabc|2026-03-22T14:00:00+00:00|Good commit"
        numstat = "---\n1\t0\tfile.py\n---"
        commits = _parse_git_log(log, numstat)
        assert len(commits) == 1
        assert commits[0].message == "Good commit"


class TestFilterNoise:
    """Test filtering out noise commits (typos, wip, merges, deps)."""

    def test_filters_typo_fix(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        messages = [c.message for c in filtered]
        assert "Fix typo in README" not in messages

    def test_filters_wip(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        messages = [c.message for c in filtered]
        assert "wip" not in messages

    def test_filters_merge(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        messages = [c.message for c in filtered]
        assert not any("Merge branch" in m for m in messages)

    def test_filters_requirements_only(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        messages = [c.message for c in filtered]
        assert "Update requirements.txt" not in messages

    def test_keeps_real_commits(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        assert len(filtered) == 5  # stock fundamental, SSE, stock routes, PDF chunker, FAISS


class TestGroupByFeature:
    """Test grouping commits by feature area."""

    def test_groups_stock_commits(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        groups = _group_by_feature(filtered)
        # Stock-related commits should be grouped together
        assert any("stock" in key for key in groups)

    def test_groups_pdf_commits(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        groups = _group_by_feature(filtered)
        assert any("pdf" in key for key in groups)

    def test_each_group_has_commits(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        groups = _group_by_feature(filtered)
        for group_commits in groups.values():
            assert len(group_commits) >= 1


class TestPickBiggestFeature:
    """Test selecting the most significant feature group."""

    def test_picks_group_with_most_lines(self):
        commits = _parse_git_log(SAMPLE_GIT_LOG, SAMPLE_NUMSTAT)
        filtered = _filter_noise(commits)
        groups = _group_by_feature(filtered)
        name, feature_commits = _pick_biggest_feature(groups)
        # Stock group has the most lines changed (fundamental + SSE + routes)
        assert "stock" in name
        assert len(feature_commits) >= 2

    def test_returns_none_for_empty(self):
        name, commits = _pick_biggest_feature({})
        assert name == ""
        assert commits == []


class TestGitInsightsToArticle:
    """Test the full flow: SSH → parse → filter → group → ScrapedArticle."""

    @patch("src.git_insights.subprocess.run")
    def test_returns_scraped_article(self, mock_run):
        """Full flow returns a ScrapedArticle compatible with the pipeline."""
        # Mock SSH calls: first for git log, second for numstat
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights()
        article = insights.get_latest_feature()

        assert isinstance(article, ScrapedArticle)
        assert article.source == "git:lubot-staging-services-agent-api"
        assert article.source_priority == 0  # top priority for own work
        assert article.url.startswith("https://")
        assert len(article.title) > 0
        assert len(article.summary) > 0

    @patch("src.git_insights.subprocess.run")
    def test_summary_contains_real_numbers(self, mock_run):
        """Summary includes lines changed and files modified from git."""
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights()
        article = insights.get_latest_feature()

        # Summary should mention real numbers from the commits
        assert any(char.isdigit() for char in article.summary)

    @patch("src.git_insights.subprocess.run")
    def test_ssh_failure_returns_none(self, mock_run):
        """Returns None when SSH fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")

        insights = GitInsights()
        article = insights.get_latest_feature()
        assert article is None

    @patch("src.git_insights.subprocess.run")
    def test_empty_log_returns_none(self, mock_run):
        """Returns None when git log is empty (no recent commits)."""
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=0),
            MagicMock(stdout="", returncode=0),
        ]

        insights = GitInsights()
        article = insights.get_latest_feature()
        assert article is None

    @patch("src.git_insights.subprocess.run")
    def test_custom_repo_path(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights(repo_path="/srv/lubot-staging/lubot-frontend")
        insights.get_latest_feature()
        # SSH command should use the custom path
        call_args = mock_run.call_args_list[0]
        assert "/srv/lubot-staging/lubot-frontend" in call_args[0][0]

    @patch("src.git_insights.subprocess.run")
    def test_custom_days_back(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights(days_back=14)
        insights.get_latest_feature()
        call_args = mock_run.call_args_list[0]
        assert "14 days ago" in call_args[0][0]


class TestGitInsightsCommitDetails:
    """Test that commit details are preserved for writer context."""

    @patch("src.git_insights.subprocess.run")
    def test_article_title_describes_feature(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights()
        article = insights.get_latest_feature()
        # Title should be descriptive, not just a commit hash
        assert len(article.title) > 10

    @patch("src.git_insights.subprocess.run")
    def test_summary_lists_commit_messages(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout=SAMPLE_GIT_LOG, returncode=0),
            MagicMock(stdout=SAMPLE_NUMSTAT, returncode=0),
        ]

        insights = GitInsights()
        article = insights.get_latest_feature()
        # Summary should contain the actual commit messages for writer context
        assert "stock" in article.summary.lower() or "Stock" in article.summary
