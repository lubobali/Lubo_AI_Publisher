"""Generate ONE swipeable carousel (Phase 2.21) and save it as PENDING for review.

Runs the full pipeline (topic -> scrape -> dedup -> carousel writer -> slide render) with
as_carousel=True, so a multi-image carousel lands on the dashboard exactly like an auto post.
Run it inside the worker container (or with DATABASE_URL pointed at the app `publisher` DB):

    python -m scripts.make_carousel            # today's rotation topic
    python -m scripts.make_carousel ai_news    # a specific category (sources_key)
"""

import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from src.cron import TIMEZONE
from src.db import SessionLocal
from src.models import PublisherPost
from src.scheduler import Pipeline
from src.topic_rotator import get_todays_topic, load_topic_categories

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("make_carousel")


def _pick_topic(category: str | None, gen_date) -> dict:
    """A specific category (by sources_key) if asked, else today's rotation topic."""
    if category:
        for topic in load_topic_categories():
            if topic["sources_key"] == category:
                return topic
        raise SystemExit(f"Unknown category '{category}'. Valid: {[t['sources_key'] for t in load_topic_categories()]}")
    return get_todays_topic(gen_date)


async def _go(category: str | None) -> None:
    session = SessionLocal()
    try:
        gen_date = datetime.now(ZoneInfo(TIMEZONE)).date()
        topic = _pick_topic(category, gen_date)
        logger.info("Generating CAROUSEL for topic: %s (%s)", topic["name"], topic["sources_key"])
        result = await Pipeline(session).generate_post(gen_date, topic=topic, as_carousel=True)
        session.commit()
        if result.success:
            post = session.query(PublisherPost).filter_by(id=result.post_id).first()
            slides = 1 + len(post.extra_image_paths or []) if post and post.image_path else 0
            logger.info("OK — pending carousel #%d, %d slides. Review it on the dashboard.", result.post_id, slides)
        else:
            logger.error("FAILED: %s", result.error)
    except Exception:
        session.rollback()
        logger.exception("Carousel generation crashed")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(_go(sys.argv[1] if len(sys.argv) > 1 else None))
