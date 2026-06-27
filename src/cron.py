"""Scheduler daemon — daily post generation + publishing approved posts.

Runs as the publisher worker container (Step 17). Jobs:
  - daily_planner:     each midnight, schedule the day's posts (1 or 2) each at a
                       fresh RANDOM time within its window (from schedule.yaml)
  - todays_generation_N: the one-off jobs the planner schedules — each generates
                       one of the day's posts (saved as PENDING) at its time
  - publish_approved:  every few minutes, post any APPROVED posts to LinkedIn

A day can have up to 2 posts (9/week plan); on those days the 2nd is kept >= 8h
after the 1st. Each generation time is randomized within its window. Publishing
only happens after Lubo approves in the dashboard. Intervals/TZ are env-overridable.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from src.backup import run_backup
from src.db import SessionLocal
from src.scheduler import Pipeline, publish_approved_posts
from src.topic_rotator import get_random_post_time, get_todays_posts, load_schedule_config

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL_MIN = int(os.getenv("PUBLISHER_PUBLISH_INTERVAL_MIN", "5"))
TIMEZONE = os.getenv("PUBLISHER_TZ", "America/Chicago")


def _run_generation(topic: dict, show_offset: int = 0) -> None:
    """Generate ONE post for an explicit topic slot (saved as PENDING for approval).

    Uses the date in the configured timezone (not UTC) — a late-CT generation
    window can fall on the next UTC day, which would otherwise pick the wrong slot.
    """

    async def _go():
        session = SessionLocal()
        try:
            gen_date = datetime.now(ZoneInfo(TIMEZONE)).date()
            result = await Pipeline(session).generate_post(gen_date, topic=topic, show_offset=show_offset)
            session.commit()
            logger.info("Generation (%s): success=%s", topic.get("sources_key"), getattr(result, "success", None))
        except Exception:
            session.rollback()
            logger.exception("Generation failed for %s", topic.get("sources_key"))
        finally:
            session.close()

    asyncio.run(_go())


def _run_publish() -> None:
    """Publish any approved posts to LinkedIn. No-op without a token."""
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    person_urn = os.getenv("LINKEDIN_PERSON_URN")
    if not (token and person_urn):
        logger.warning("LINKEDIN_ACCESS_TOKEN/PERSON_URN not set — skipping publish")
        return

    async def _go():
        session = SessionLocal()
        try:
            n = await publish_approved_posts(session, token, person_urn)
            session.commit()
            if n:
                logger.info("Published %d approved post(s)", n)
        except Exception:
            session.rollback()
            logger.exception("Publish failed")
        finally:
            session.close()

    asyncio.run(_go())


def _run_backup() -> None:
    """Nightly backup to Backblaze B2 (DB dump + per-post files). No-op if B2 unconfigured."""

    session = SessionLocal()
    try:
        run_backup(session)
    except Exception:
        logger.exception("Nightly backup failed")
    finally:
        session.close()


def _plan_today_times(posts: list[dict], now: datetime) -> list[datetime]:
    """Assign each of today's posts a datetime in its window, >= min gap apart.

    Posts are listed in time order; the 2nd post on a double day is pushed to at
    least `min_hours_between_same_day` hours after the 1st (default 8h).
    """
    min_gap_h = load_schedule_config().get("rules", {}).get("min_hours_between_same_day", 8)
    times: list[datetime] = []
    prev: datetime | None = None
    for post in posts:
        t = get_random_post_time(now.date(), window=post.get("window"))
        run_at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if prev is not None and run_at < prev + timedelta(hours=min_gap_h):
            run_at = prev + timedelta(hours=min_gap_h)
        times.append(run_at)
        prev = run_at
    return times


def _schedule_todays_posts(sched: BlockingScheduler) -> None:
    """Schedule each of today's posts (1 or 2) at a fresh random time in its window.

    Windows come from schedule.yaml; a double day's 2nd post is kept >= 8h after the
    1st. Called at startup and each midnight. Slots whose time already passed (e.g. the
    worker booted late) are skipped — the next midnight planner handles tomorrow.
    """
    now = datetime.now(sched.timezone)
    posts = get_todays_posts(now.date())
    times = _plan_today_times(posts, now)

    scheduled = 0
    for i, (post, run_at) in enumerate(zip(posts, times, strict=True)):
        key = post["topic"].get("sources_key")
        if run_at <= now:
            logger.info("Slot %d (%s) at %s already passed — skipping today", i, key, run_at.strftime("%H:%M"))
            continue
        sched.add_job(
            _run_generation,
            "date",
            run_date=run_at,
            args=[post["topic"], post.get("show_offset", 0)],
            id=f"todays_generation_{i}",
            replace_existing=True,
        )
        scheduled += 1
        logger.info("Will generate %s at %s %s", key, run_at.strftime("%Y-%m-%d %H:%M"), TIMEZONE)
    if not scheduled:
        logger.info("No remaining posts to schedule today")


def build_scheduler() -> BlockingScheduler:
    """Build the scheduler: a midnight planner (today's posts) + the publish loop."""
    sched = BlockingScheduler(timezone=TIMEZONE)
    sched.add_job(lambda: _schedule_todays_posts(sched), "cron", hour=0, minute=1, id="daily_planner")
    sched.add_job(_run_publish, "interval", minutes=PUBLISH_INTERVAL_MIN, id="publish_approved")
    sched.add_job(_run_backup, "cron", hour=4, minute=0, id="nightly_backup")
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    sched = build_scheduler()
    _schedule_todays_posts(sched)  # don't wait for the first midnight — schedule today now
    logger.info(
        "Publisher scheduler up — 9 posts/week (biohacker 3x Sun/Wed/Fri), randomized times %s, publish every %d min",
        TIMEZONE,
        PUBLISH_INTERVAL_MIN,
    )
    sched.start()


if __name__ == "__main__":
    main()
