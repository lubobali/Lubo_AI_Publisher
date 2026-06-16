"""Tests for wakatime_insights — SSH to staging, read daily coding archives, build weekly stats."""

import json
import subprocess
from unittest.mock import MagicMock, patch

from src.scraper import ScrapedArticle
from src.wakatime_insights import (
    DayStats,
    WakaTimeInsights,
    WeeklyStats,
    _aggregate_week,
    _parse_day,
    _split_archives,
    _split_weeks,
    build_screenshot_fields,
)

# ---------------------------------------------------------------------------
# Sample WakaTime daily archive builder (mirrors the real archive JSON shape:
# values are STRINGS, one day lives in data[0])
# ---------------------------------------------------------------------------


def _archive(date, total_seconds, langs, projects, ai):
    """Build one daily archive dict like /srv/lubot-staging/.wakatime-archive/wakatime-*.json."""
    return {
        "data": [
            {
                "grand_total": {
                    "total_seconds": str(total_seconds),
                    "ai_input_tokens": str(ai["input"]),
                    "ai_output_tokens": str(ai["output"]),
                    "ai_sessions": str(ai["sessions"]),
                    "ai_prompt_events_total": str(ai["prompts"]),
                    "ai_agent_total_cost": str(ai["cost"]),
                },
                "range": {"date": date},
                "projects": [{"name": n, "total_seconds": str(s)} for n, s in projects.items()],
                "languages": [{"name": n, "total_seconds": str(s)} for n, s in langs.items()],
            }
        ]
    }


DAY1 = _archive(
    "2026-06-13",
    26572,
    langs={"Python": 19500, "TypeScript": 2580, "Bash": 240},
    projects={"LuBot": 23580, "lu": 1620},
    ai={"input": 132406475, "output": 112724, "sessions": 3, "prompts": 139, "cost": 379.40},
)
DAY2 = _archive(
    "2026-06-12",
    18000,
    langs={"Python": 16000, "Bash": 2000},
    projects={"LuBot": 18000},
    ai={"input": 50000000, "output": 50000, "sessions": 2, "prompts": 80, "cost": 150.00},
)


def _ssh_output(archives, notes=None):
    """Reproduce the stdout shape of _fetch_archives (markers + JSON, optional notes)."""
    out = "".join(f"===WAKA-FILE===\n{json.dumps(a)}\n" for a in archives)
    out += "===WAKA-NOTES===\n"
    if notes:
        out += notes
    return out


class TestParseDay:
    """Parse one daily archive dict into a DayStats."""

    def test_parses_core_fields(self):
        day = _parse_day(DAY1)
        assert isinstance(day, DayStats)
        assert day.date == "2026-06-13"
        assert day.total_seconds == 26572.0
        assert day.by_language["Python"] == 19500.0
        assert day.by_project["LuBot"] == 23580.0

    def test_casts_ai_string_values(self):
        day = _parse_day(DAY1)
        assert day.ai_input_tokens == 132406475
        assert day.ai_sessions == 3
        assert day.ai_prompt_events == 139
        assert day.ai_cost == 379.40

    def test_empty_data_returns_none(self):
        assert _parse_day({"data": []}) is None
        assert _parse_day({}) is None


class TestAggregateWeek:
    """Aggregate multiple DayStats into a WeeklyStats."""

    def test_sums_total_seconds(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.total_seconds == 26572.0 + 18000.0

    def test_counts_active_days(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.days_active == 2

    def test_aggregates_languages(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.by_language["Python"] == 19500.0 + 16000.0
        assert stats.by_language["Bash"] == 240.0 + 2000.0

    def test_sums_ai_usage_and_cost(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.ai_sessions == 5
        assert stats.ai_prompt_events == 219
        assert stats.ai_input_tokens == 182406475
        assert stats.ai_cost == 379.40 + 150.00

    def test_top_language_and_project(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.top_language == "Python"
        assert stats.top_project == "LuBot"

    def test_date_range(self):
        stats = _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])
        assert stats.start_date == "2026-06-12"
        assert stats.end_date == "2026-06-13"


class TestSplitArchives:
    """Split raw SSH stdout into archive dicts."""

    def test_splits_multiple_files(self):
        raw = "".join(f"===WAKA-FILE===\n{json.dumps(a)}\n" for a in (DAY1, DAY2))
        archives = _split_archives(raw)
        assert len(archives) == 2

    def test_skips_malformed_segment(self):
        raw = f"===WAKA-FILE===\nnot json\n===WAKA-FILE===\n{json.dumps(DAY1)}\n"
        archives = _split_archives(raw)
        assert len(archives) == 1

    def test_empty_returns_empty(self):
        assert _split_archives("") == []


class TestWakaTimeInsightsToArticle:
    """Full flow: SSH -> split -> parse -> aggregate -> ScrapedArticle."""

    @patch("src.wakatime_insights.subprocess.run")
    def test_returns_scraped_article(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        article = WakaTimeInsights().get_weekly_stats()
        assert isinstance(article, ScrapedArticle)
        assert article.source == "wakatime:lubot"
        assert article.source_priority == 0
        assert article.url.startswith("https://")
        assert len(article.title) > 0
        assert len(article.summary) > 0

    @patch("src.wakatime_insights.subprocess.run")
    def test_summary_contains_real_numbers(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        article = WakaTimeInsights().get_weekly_stats()
        assert any(ch.isdigit() for ch in article.summary)
        assert "Python" in article.summary

    @patch("src.wakatime_insights.subprocess.run")
    def test_includes_cost_when_enabled(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        article = WakaTimeInsights(include_costs=True).get_weekly_stats()
        assert "$" in article.summary

    @patch("src.wakatime_insights.subprocess.run")
    def test_excludes_cost_when_disabled(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        article = WakaTimeInsights(include_costs=False).get_weekly_stats()
        assert "$" not in article.summary

    @patch("src.wakatime_insights.subprocess.run")
    def test_includes_notes_when_present(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=_ssh_output([DAY1], notes="shipped wakatime insights module"),
            returncode=0,
        )
        article = WakaTimeInsights().get_weekly_stats()
        assert "shipped wakatime insights module" in article.summary

    @patch("src.wakatime_insights.subprocess.run")
    def test_ssh_failure_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
        assert WakaTimeInsights().get_weekly_stats() is None

    @patch("src.wakatime_insights.subprocess.run")
    def test_no_archives_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(stdout="===WAKA-NOTES===\n", returncode=0)
        assert WakaTimeInsights().get_weekly_stats() is None

    @patch("src.wakatime_insights.subprocess.run")
    def test_fetches_two_weeks_for_delta(self, mock_run):
        # days_back=7 must pull 14 files (this week + last week) for the delta
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1]), returncode=0)
        WakaTimeInsights(days_back=7).get_weekly_stats()
        sent_cmd = mock_run.call_args[0][0]
        assert "head -14" in sent_cmd

    @patch("src.wakatime_insights.subprocess.run")
    def test_exposes_weekly_stats(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        insights = WakaTimeInsights()
        insights.get_weekly_stats()
        assert isinstance(insights.weekly_stats, WeeklyStats)
        assert insights.weekly_stats.days_active == 2


class TestBuildScreenshotFields:
    """Adapter from WeeklyStats to take_wakatime_screenshot kwargs."""

    def _stats(self):
        return _aggregate_week([_parse_day(DAY1), _parse_day(DAY2)])

    def test_returns_expected_keys(self):
        fields = build_screenshot_fields(self._stats())
        assert set(fields) == {
            "total_time",
            "days_active",
            "languages",
            "projects",
            "ai_sessions",
            "ai_prompts",
            "ai_tokens",
            "ai_cost",
            "momentum",
            "date_range",
        }

    def test_languages_are_name_detail_pct_tuples(self):
        fields = build_screenshot_fields(self._stats())
        name, detail, pct = fields["languages"][0]
        assert name == "Python"
        assert "h" in detail and "m" in detail  # formatted duration
        assert isinstance(pct, float)

    def test_cost_hidden_when_disabled(self):
        assert build_screenshot_fields(self._stats(), include_costs=False)["ai_cost"] is None
        assert build_screenshot_fields(self._stats(), include_costs=True)["ai_cost"] is not None

    def test_momentum_up_and_down(self):
        up = self._stats()
        up.prev_total_seconds = 1.0  # tiny prior → big increase
        assert build_screenshot_fields(up)["momentum"].startswith("up")

        down = self._stats()
        down.prev_total_seconds = down.total_seconds * 10  # prior much larger
        assert build_screenshot_fields(down)["momentum"].startswith("down")

    def test_momentum_empty_without_prior(self):
        assert build_screenshot_fields(self._stats())["momentum"] == ""


def _simple_day(date, seconds):
    """Minimal one-day archive — just a total, for week-over-week tests."""
    ai = {"input": 1, "output": 1, "sessions": 1, "prompts": 1, "cost": 1}
    return _archive(date, seconds, {"Python": seconds}, {"LuBot": seconds}, ai)


# Prior week (Jun 1-7) at 1h/day, current week (Jun 8-14) at 2h/day → +100%
PRIOR_WEEK = [_simple_day(f"2026-06-{d:02d}", 3600) for d in range(1, 8)]
CURRENT_WEEK = [_simple_day(f"2026-06-{d:02d}", 7200) for d in range(8, 15)]


class TestWeekOverWeek:
    """Split 14 days into this-week / last-week and compute the momentum delta."""

    def test_split_weeks_partitions_by_recency(self):
        days = [_parse_day(a) for a in PRIOR_WEEK + CURRENT_WEEK]
        current, prior = _split_weeks(days, 7)
        assert len(current) == 7 and len(prior) == 7
        # current week is the most recent dates
        assert min(d.date for d in current) == "2026-06-08"
        assert max(d.date for d in prior) == "2026-06-07"

    def test_delta_pct_positive_when_up(self):
        stats = _aggregate_week([_parse_day(a) for a in CURRENT_WEEK])
        stats.prev_total_seconds = 7 * 3600  # last week was half
        assert stats.total_delta_pct == 100.0

    def test_delta_none_without_prior(self):
        stats = _aggregate_week([_parse_day(a) for a in CURRENT_WEEK])
        assert stats.total_delta_pct is None

    @patch("src.wakatime_insights.subprocess.run")
    def test_summary_includes_momentum(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output(CURRENT_WEEK + PRIOR_WEEK), returncode=0)
        insights = WakaTimeInsights()
        article = insights.get_weekly_stats()
        assert insights.weekly_stats.total_delta_pct == 100.0
        assert "100%" in article.summary
        assert "up" in article.summary.lower()


class TestLocalMode:
    """Local-read mode: read archive files directly instead of SSH (worker has no ssh)."""

    def test_local_mode_reads_files_without_ssh(self, tmp_path):
        (tmp_path / "wakatime-2026-06-13.json").write_text(json.dumps(DAY1))
        (tmp_path / "wakatime-2026-06-12.json").write_text(json.dumps(DAY2))
        with patch("src.wakatime_insights.subprocess.run") as mock_run:
            article = WakaTimeInsights(archive_dir=str(tmp_path), local=True).get_weekly_stats()
        mock_run.assert_not_called()
        assert article is not None
        assert "Building in public" in article.title

    def test_local_mode_reads_notes(self, tmp_path):
        (tmp_path / "wakatime-2026-06-13.json").write_text(json.dumps(DAY1))
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "week.md").write_text("shipped local-read mode")
        article = WakaTimeInsights(archive_dir=str(tmp_path), local=True).get_weekly_stats()
        assert "shipped local-read mode" in article.summary

    @patch("src.wakatime_insights.subprocess.run")
    def test_ssh_is_default(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_ssh_output([DAY1, DAY2]), returncode=0)
        WakaTimeInsights().get_weekly_stats()
        assert "ssh" in mock_run.call_args_list[0].args[0]
