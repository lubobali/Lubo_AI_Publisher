"""Tests for DevTrack weekly-report parsing (Phase 2.11).

Parses the DevTrack-AI weekly markdown report (the rich, trusted source Lubo gets)
into typed metrics for the Building in Public post + card. Parsing is pure; verified
on real W23/W24/W25 fixtures separately.
"""

from src.devtrack_insights import DevTrackReport, _build_summary, find_latest_report, parse_report

SAMPLE = """<div align="center">
## LuBot &mdash; Weekly Build Report
**Reporting Period:** Week 25 &mdash; Jun 15 to Jun 21, 2026
</div>

## Executive Summary

| Metric | Value |
| --- | --- |
| **Total Working Hours** | **81.6h** |
| Code & Build Time | 54.9h |
| Planning, Research & Architecture | 26.7h |
| Days Worked | 7 of 7 |
| Code & Build vs Prior Week | -8.9h (-14%) |
| Git Commits | 82 |
| Lines Shipped | +25922 / -3949 across 128 files |
| **Tests Written This Week** | **+428 new test functions** |
| Total Tests in Repo | 8260 |
| **AI Orchestration Sessions** | **18** |
| AI Output Generated | 25,298,310 tokens (25.3M) |

## Hours by Project

- **LuBot** — 43.3h
- **lubot-publisher** — 11.4h
- **root** — 0.1h

## Hours by Technology

- Python — 34.2h
- Text — 12.6h
- Markdown — 3.7h

## Project Portfolio

| Project | Status | Public URL |
| --- | --- | --- |
| **LuBot** | Live | https://lubot.ai |
"""


class TestParseReport:
    def test_period(self):
        r = parse_report(SAMPLE)
        assert r.period_label == "Week 25"
        assert "Jun 15" in r.date_range and "Jun 21" in r.date_range

    def test_headline_metrics(self):
        r = parse_report(SAMPLE)
        assert r.total_hours == 81.6
        assert r.code_hours == 54.9
        assert r.commits == 82
        assert (r.lines_added, r.lines_deleted, r.files_changed) == (25922, 3949, 128)
        assert r.tests_added == 428
        assert r.ai_sessions == 18
        assert r.ai_output_tokens == 25298310
        assert r.days_worked == "7 of 7"
        assert "-14%" in r.momentum

    def test_breakdowns(self):
        r = parse_report(SAMPLE)
        assert r.by_technology[0] == ("Python", 34.2)
        assert r.by_project[0] == ("LuBot", 43.3)

    def test_three_column_table_not_parsed_as_metric(self):
        # the Project Portfolio (3-col) must NOT pollute the metric dict
        r = parse_report(SAMPLE)
        assert "Project" not in r.metrics  # 3-col rows skipped

    def test_summary_has_exact_numbers_and_guardrail(self):
        text = _build_summary(parse_report(SAMPLE)).lower()
        assert "81.6" in text and "82" in text and "428" in text
        assert "exact" in text or "do not invent" in text  # anti-hallucination

    def test_is_dataclass(self):
        assert isinstance(parse_report(SAMPLE), DevTrackReport)


class TestFindLatestReport:
    def test_picks_highest_week(self, tmp_path):
        for wk in ("W23", "W24", "W25"):
            (tmp_path / f"wakatime-weekly-2026-{wk}.md").write_text("x")
        (tmp_path / "wakatime-weekly-2026-W25.html").write_text("x")  # ignore non-md
        latest = find_latest_report(tmp_path)
        assert latest.name == "wakatime-weekly-2026-W25.md"

    def test_none_when_no_reports(self, tmp_path):
        assert find_latest_report(tmp_path) is None
