#!/usr/bin/env python3
"""Phase F dry run — generate ONE real Biohacker post end to end.

Forces today's topic to Biohacker, runs the full podcast-primary pipeline
(feed -> Deepgram transcribe -> distill -> 550B writer -> branded insight card),
and saves it as a PENDING post in the real `publisher` DB so it shows on the
dashboard at https://publisher.lubot.ai for eyeball review.

Run IN the container so the API keys, chromium, and the screenshots volume are present:
  docker compose run --rm --no-deps publisher-worker python scripts/dryrun_biohacker.py

Uses the DATABASE_URL from the environment as-is (publisher-db in the container).
The post is PENDING — it will not publish until approved on the dashboard.
"""

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.WARNING)

import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.scheduler as scheduler_mod
from src.models import Base, PublisherPost
from src.scheduler import Pipeline

BIOHACKER_TOPIC = {
    "name": "Biohacker",
    "description": "Biohacking, supplements, longevity science",
    "sources_key": "biohacker",
}


async def main():
    db_url = os.environ["DATABASE_URL"]
    print(f"DB: {db_url.rsplit('@', 1)[-1]}")  # host/db only, no creds

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    # Force today's topic to Biohacker regardless of the rotation.
    scheduler_mod.get_todays_topic = lambda _d: BIOHACKER_TOPIC

    print("\nGenerating a Biohacker post (podcast-primary)...\n")
    result = await Pipeline(session=session).generate_post(target_date=date.today())
    session.commit()

    if not result.success:
        print(f"\nFAILED: {result.error}")
        session.close()
        return

    post = session.query(PublisherPost).filter_by(id=result.post_id).first()
    print(f"\n{'=' * 70}")
    print(f"POST #{post.id} saved as {post.status.upper()}")
    print(f"Category : {post.topic_category}")
    print(f"Title    : {post.topic_title}")
    print(f"Source   : {post.source_url}")
    print(f"Hashtags : {post.hashtags}")
    print(f"Image    : {post.image_path or 'none'}")
    print(f"{'=' * 70}")
    print(f"\n--- POST TEXT ({len(post.post_text)} chars) ---\n")
    print(post.post_text)
    print("\n--- END ---")
    print(f"\nView on dashboard: https://publisher.lubot.ai (post #{post.id})")
    session.close()


if __name__ == "__main__":
    asyncio.run(main())
