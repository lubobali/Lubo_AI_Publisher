"""Scheduler daemon — daily post generation + publishing approved posts.

Runs as the publisher worker container (Step 17). Two jobs:
  - daily_generation: once a day, generate the day's post (saved as PENDING)
  - publish_approved:  every few minutes, post any APPROVED posts to LinkedIn

Generation is automatic; publishing only happens after Lubo approves in the
dashboard. Times/intervals are env-overridable.
"""

import asyncio
import logging
import os
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler

from src.db import SessionLocal
from src.scheduler import Pipeline, publish_approved_posts

logger = logging.getLogger(__name__)

GEN_HOUR = int(os.getenv("PUBLISHER_GEN_HOUR", "9"))  # local hour to generate the day's post
PUBLISH_INTERVAL_MIN = int(os.getenv("PUBLISHER_PUBLISH_INTERVAL_MIN", "5"))
TIMEZONE = os.getenv("PUBLISHER_TZ", "America/Chicago")


def _run_daily_generation() -> None:
    """Generate today's post (saved as PENDING for approval)."""

    async def _go():
        session = SessionLocal()
        try:
            result = await Pipeline(session).generate_post(date.today())
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
    if not token:
        logger.warning("LINKEDIN_ACCESS_TOKEN not set — skipping publish")
        return

    async def _go():
        session = SessionLocal()
        try:
            n = await publish_approved_posts(session, token)
            session.commit()
            if n:
                logger.info("Published %d approved post(s)", n)
        except Exception:
            session.rollback()
            logger.exception("Publish failed")
        finally:
            session.close()

    asyncio.run(_go())


def build_scheduler() -> BlockingScheduler:
    """Build the scheduler with the daily-generation and publish jobs."""
    sched = BlockingScheduler(timezone=TIMEZONE)
    sched.add_job(_run_daily_generation, "cron", hour=GEN_HOUR, minute=0, id="daily_generation")
    sched.add_job(_run_publish, "interval", minutes=PUBLISH_INTERVAL_MIN, id="publish_approved")
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    logger.info(
        "Publisher scheduler up — generate at %02d:00 %s, publish every %d min",
        GEN_HOUR,
        TIMEZONE,
        PUBLISH_INTERVAL_MIN,
    )
    build_scheduler().start()


if __name__ == "__main__":
    main()
