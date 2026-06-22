"""Scheduler daemon — daily post generation + publishing approved posts.

Runs as the publisher worker container (Step 17). Jobs:
  - daily_planner:     each midnight, roll a fresh RANDOM generation time for the
                       day (from schedule.yaml windows) and schedule it
  - todays_generation: the one-off job the planner schedules — generates the day's
                       post (saved as PENDING) at that random time
  - publish_approved:  every few minutes, post any APPROVED posts to LinkedIn

Generation time is randomized within the day's window (weekday vs weekend) so the
post never lands at the same hour twice. Publishing only happens after Lubo
approves in the dashboard. Intervals/timezone are env-overridable.
"""

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from src.db import SessionLocal
from src.scheduler import Pipeline, publish_approved_posts
from src.topic_rotator import get_random_post_time

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL_MIN = int(os.getenv("PUBLISHER_PUBLISH_INTERVAL_MIN", "5"))
TIMEZONE = os.getenv("PUBLISHER_TZ", "America/Chicago")


def _run_daily_generation() -> None:
    """Generate today's post (saved as PENDING for approval).

    Uses the date in the configured timezone (not UTC) — a late-CT generation
    window can fall on the next UTC day, which would otherwise pick the wrong
    rotation slot.
    """

    async def _go():
        session = SessionLocal()
        try:
            gen_date = datetime.now(ZoneInfo(TIMEZONE)).date()
            result = await Pipeline(session).generate_post(gen_date)
            session.commit()
            logger.info("Daily generation: success=%s", getattr(result, "success", None))
        except Exception:
            session.rollback()
            logger.exception("Daily generation failed")
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


def _schedule_todays_generation(sched: BlockingScheduler) -> None:
    """Pick a random time within today's window and schedule generation for it.

    Weekday/weekend windows come from schedule.yaml. Called at startup and again
    each midnight, so every day gets a fresh, different time. If today's window has
    already passed (e.g. the worker booted in the evening), it's skipped — the next
    midnight planner run schedules tomorrow.
    """
    now = datetime.now(sched.timezone)
    post_time = get_random_post_time(now.date())
    run_at = now.replace(hour=post_time.hour, minute=post_time.minute, second=0, microsecond=0)

    if run_at <= now:
        logger.info(
            "Today's window (%s %s) already passed — will schedule tomorrow", post_time.strftime("%H:%M"), TIMEZONE
        )
        return

    sched.add_job(_run_daily_generation, "date", run_date=run_at, id="todays_generation", replace_existing=True)
    logger.info("Today's post will generate at %s %s", run_at.strftime("%Y-%m-%d %H:%M"), TIMEZONE)


def build_scheduler() -> BlockingScheduler:
    """Build the scheduler: a midnight planner (random daily time) + the publish loop."""
    sched = BlockingScheduler(timezone=TIMEZONE)
    sched.add_job(lambda: _schedule_todays_generation(sched), "cron", hour=0, minute=1, id="daily_planner")
    sched.add_job(_run_publish, "interval", minutes=PUBLISH_INTERVAL_MIN, id="publish_approved")
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    sched = build_scheduler()
    _schedule_todays_generation(sched)  # don't wait for the first midnight — schedule today now
    logger.info(
        "Publisher scheduler up — randomized daily generation (weekday 3-5 PM / weekend 11 PM %s), publish every %d min",
        TIMEZONE,
        PUBLISH_INTERVAL_MIN,
    )
    sched.start()


if __name__ == "__main__":
    main()
