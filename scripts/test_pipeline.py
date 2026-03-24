#!/usr/bin/env python3
"""Live pipeline test — 7 days through Pipeline.generate_post() with full Langfuse tracing.

Simulates a real week: Sun-Sat, all 7 topic categories, real scraping, real LLM calls.
Each run creates a complete Langfuse trace with nested spans and all 5 quality scores.

Usage: python3 scripts/test_pipeline.py

Check results at: https://us.cloud.langfuse.com
"""

import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")
# Quiet down noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.WARNING)

import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

os.environ["DATABASE_URL"] = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://publisher:publisher_dev@localhost:5433/publisher",
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherPost
from src.scheduler import Pipeline, PipelineResult
from src.topic_rotator import get_todays_topic


async def run_day(session, target_date: date, day_num: int) -> PipelineResult:
    """Run Pipeline.generate_post() for one day — full Langfuse trace."""
    topic = get_todays_topic(target_date)
    day_name = target_date.strftime("%A")

    print(f"\n{'=' * 70}")
    print(f"DAY {day_num}/7: {day_name} {target_date} — {topic['name']} ({topic['sources_key']})")
    print(f"{'=' * 70}")

    pipeline = Pipeline(session=session)
    result = await pipeline.generate_post(target_date=target_date)
    session.commit()

    if result.success:
        post = session.query(PublisherPost).filter_by(id=result.post_id).first()

        # Post-processing + validation now happens inside Pipeline.generate_post()
        print(f"  POST #{post.id} saved as PENDING")
        print(f"  Trace ID: {post.langfuse_trace_id or 'none'}")
        print(f"  Category: {post.topic_category}")
        print(f"  Title: {post.topic_title[:70]}")
        print(f"  Hashtags: {post.hashtags}")
        print(f"  Image: {post.image_path or 'none'}")
        print(f"\n  --- POST TEXT ({len(post.post_text)} chars) ---")
        for line in post.post_text.split("\n"):
            print(f"  {line}")
        print("  --- END ---")
    else:
        print(f"  FAILED: {result.error}")

    return result


async def main():
    db_url = os.getenv("DATABASE_URL")
    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    # Find the Sunday that starts this week (all 7 categories)
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    start = today - timedelta(days=days_since_sunday)

    print(f"Pipeline test — generating 7 posts for week starting {start}")
    print("Langfuse project: us.cloud.langfuse.com\n")

    # Preview the week
    for i in range(7):
        d = start + timedelta(days=i)
        topic = get_todays_topic(d)
        print(f"  {d.strftime('%A'):>10} {d} -> {topic['name']}")

    results = []
    for i in range(7):
        target = start + timedelta(days=i)
        session = Session()
        try:
            result = await run_day(session, target, i + 1)
            results.append((target, result))
        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            import traceback

            traceback.print_exc()
            results.append((target, PipelineResult(success=False, error=str(e))))
        finally:
            session.close()

    # Flush Langfuse — ensure all traces are sent before exit
    try:
        from langfuse import get_client

        client = get_client()
        print("\nFlushing Langfuse traces...")
        client.flush()
        print("Langfuse flush complete.")
    except Exception as e:
        print(f"Langfuse flush failed: {e}")

    # Summary
    print(f"\n\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    success = sum(1 for _, r in results if r.success)
    print(f"Generated: {success}/7 posts\n")
    for d, result in results:
        topic = get_todays_topic(d)
        status = f"OK #{result.post_id}" if result.success else f"FAIL — {result.error[:50]}"
        print(f"  {d.strftime('%A'):>10} {d} — {topic['name']:20} {status}")

    print("\nCheck Langfuse: https://us.cloud.langfuse.com")
    print("Look for 7 traces named 'generate_post' with nested spans + scores.")


if __name__ == "__main__":
    asyncio.run(main())
