"""Tests for the scheduler daemon — daily generation + publishing approved posts."""

import datetime as real_dt
from unittest.mock import AsyncMock, MagicMock, patch

from src import cron


def test_build_scheduler_registers_planner_and_publish():
    with patch.object(cron.BlockingScheduler, "add_job") as mock_add:
        cron.build_scheduler()
    triggers = {c.kwargs.get("id"): c.args[1] for c in mock_add.call_args_list}
    assert triggers.get("daily_planner") == "cron"
    assert triggers.get("publish_approved") == "interval"


@patch("src.cron.get_random_post_time")
@patch("src.cron.datetime")
def test_schedule_todays_generation_adds_future_date_job(mock_dt, mock_time):
    mock_dt.now.return_value = real_dt.datetime(2026, 6, 17, 8, 0, tzinfo=real_dt.UTC)
    mock_time.return_value = real_dt.time(16, 30)  # window time later today
    sched = MagicMock()
    sched.timezone = real_dt.UTC
    cron._schedule_todays_generation(sched)
    assert sched.add_job.call_args.args[1] == "date"
    assert sched.add_job.call_args.kwargs.get("id") == "todays_generation"


@patch("src.cron.get_random_post_time")
@patch("src.cron.datetime")
def test_schedule_skips_when_window_already_passed(mock_dt, mock_time):
    mock_dt.now.return_value = real_dt.datetime(2026, 6, 17, 20, 0, tzinfo=real_dt.UTC)
    mock_time.return_value = real_dt.time(16, 30)  # earlier today — already passed
    sched = MagicMock()
    sched.timezone = real_dt.UTC
    cron._schedule_todays_generation(sched)
    sched.add_job.assert_not_called()


@patch("src.cron.SessionLocal")
@patch("src.cron.Pipeline")
def test_daily_generation_runs_pipeline(mock_pipeline_cls, mock_session_local):
    mock_pipeline_cls.return_value.generate_post = AsyncMock(return_value=MagicMock(success=True))
    cron._run_daily_generation()
    mock_pipeline_cls.return_value.generate_post.assert_awaited_once()
    mock_session_local.return_value.commit.assert_called()
    mock_session_local.return_value.close.assert_called()


@patch("src.cron.publish_approved_posts", new_callable=AsyncMock)
@patch("src.cron.SessionLocal")
def test_publish_runs_when_token_present(mock_session_local, mock_publish, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok123")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:x")
    mock_publish.return_value = 1
    cron._run_publish()
    mock_publish.assert_awaited_once()


@patch("src.cron.publish_approved_posts", new_callable=AsyncMock)
def test_publish_skips_without_token(mock_publish, monkeypatch):
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    cron._run_publish()
    mock_publish.assert_not_awaited()
