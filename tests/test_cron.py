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


def _bio_post():
    return {"topic": {"name": "Biohacker", "sources_key": "biohacker"}, "window": "morning", "show_offset": 1}


def _other_post():
    return {"topic": {"name": "AI News", "sources_key": "ai_news"}, "window": "weekday", "show_offset": 0}


@patch("src.cron.get_todays_posts")
@patch("src.cron.get_random_post_time")
@patch("src.cron.datetime")
def test_schedule_todays_posts_adds_future_date_jobs(mock_dt, mock_time, mock_posts):
    mock_dt.now.return_value = real_dt.datetime(2026, 6, 17, 5, 0, tzinfo=real_dt.UTC)
    mock_time.return_value = real_dt.time(16, 30)  # window time later today
    mock_posts.return_value = [_other_post()]
    sched = MagicMock()
    sched.timezone = real_dt.UTC
    cron._schedule_todays_posts(sched)
    assert sched.add_job.call_args.args[1] == "date"
    assert sched.add_job.call_args.kwargs.get("id") == "todays_generation_0"
    # the slot's topic + show_offset are passed to the generation job
    assert sched.add_job.call_args.kwargs["args"][0]["sources_key"] == "ai_news"


@patch("src.cron.get_todays_posts")
@patch("src.cron.get_random_post_time")
@patch("src.cron.datetime")
def test_schedule_double_day_keeps_8h_gap(mock_dt, mock_time, mock_posts):
    """Two posts in a day: the 2nd is scheduled >= 8h after the 1st."""
    mock_dt.now.return_value = real_dt.datetime(2026, 6, 17, 0, 30, tzinfo=real_dt.UTC)
    # both windows would land at 07:00 if not spaced
    mock_time.return_value = real_dt.time(7, 0)
    mock_posts.return_value = [_bio_post(), _other_post()]
    sched = MagicMock()
    sched.timezone = real_dt.UTC
    cron._schedule_todays_posts(sched)
    run_times = [c.kwargs["run_date"] for c in sched.add_job.call_args_list]
    assert len(run_times) == 2
    gap = run_times[1] - run_times[0]
    assert gap >= real_dt.timedelta(hours=8)


@patch("src.cron.get_todays_posts")
@patch("src.cron.get_random_post_time")
@patch("src.cron.datetime")
def test_schedule_skips_slots_already_passed(mock_dt, mock_time, mock_posts):
    mock_dt.now.return_value = real_dt.datetime(2026, 6, 17, 20, 0, tzinfo=real_dt.UTC)
    mock_time.return_value = real_dt.time(16, 30)  # earlier today — already passed
    mock_posts.return_value = [_other_post()]
    sched = MagicMock()
    sched.timezone = real_dt.UTC
    cron._schedule_todays_posts(sched)
    sched.add_job.assert_not_called()


@patch("src.cron.SessionLocal")
@patch("src.cron.Pipeline")
def test_generation_runs_pipeline_with_topic_and_offset(mock_pipeline_cls, mock_session_local):
    mock_pipeline_cls.return_value.generate_post = AsyncMock(return_value=MagicMock(success=True))
    topic = {"name": "Biohacker", "sources_key": "biohacker"}
    cron._run_generation(topic, show_offset=2)
    mock_pipeline_cls.return_value.generate_post.assert_awaited_once()
    _, kwargs = mock_pipeline_cls.return_value.generate_post.call_args
    assert kwargs["topic"] == topic
    assert kwargs["show_offset"] == 2
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
