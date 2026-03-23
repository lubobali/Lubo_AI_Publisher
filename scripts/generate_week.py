#!/usr/bin/env python3
"""Generate 7 real posts — one for each day of the week.

Runs the full pipeline: scrape → dedup → AI write → screenshot → save as PENDING.
Does NOT publish to LinkedIn. Just saves to DB for review.

Usage: python3 scripts/generate_week.py
"""

import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Unbuffered stdout so we see output in real time
sys.stdout.reconfigure(line_buffering=True)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Enable logging so we see warnings
logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")

import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Override Docker DB URL with local dev URL when running outside Docker
os.environ["DATABASE_URL"] = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://publisher:publisher_dev@localhost:5433/publisher",
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base
from src.topic_rotator import get_todays_topic


async def generate_single_post(session, target_date: date, day_num: int):
    """Generate a single post for a given date."""
    from datetime import UTC, datetime

    from src.duplicate_checker import DuplicateChecker
    from src.models import PublisherPost
    from src.scraper import scrape_topic
    from src.screenshotter import take_lubot_screenshot, take_screenshot
    from src.self_learner import SelfLearner
    from src.writer import write_post

    topic = get_todays_topic(target_date)
    category = topic["sources_key"]
    day_name = target_date.strftime("%A")

    print(f"\n{'=' * 70}")
    print(f"DAY {day_num}/7: {day_name} {target_date} — {topic['name']} ({category})")
    print(f"{'=' * 70}")

    # 1. Scrape
    print(f"  Scraping {category}...")
    articles = await scrape_topic(category)
    if not articles:
        print(f"  ❌ No articles found for {category}")
        return None
    print(f"  Found {len(articles)} articles")
    for i, a in enumerate(articles[:5]):
        print(f"    {i + 1}. {a.title[:80]}")

    # 2. Dedup
    checker = DuplicateChecker(session)
    selected = None
    for article in articles:
        result = await checker.check_article(
            url=article.url,
            title=article.title,
            category=category,
            published_at=article.published_at,
        )
        if not result.is_duplicate:
            selected = article
            break
        print(f"  Skipping duplicate: {article.title[:60]}... ({result.reason[:40]})")

    if not selected:
        print("  ❌ All articles are duplicates")
        return None

    print(f"  Selected: {selected.title}")
    checker.record_url(selected.url, used=True)

    # 3. Self-learning feedback
    learner = SelfLearner(session)
    report = learner.generate_performance_report()
    feedback = report.format_for_writer()

    # 4. Write
    print("  Writing post via NVIDIA NIM...")
    writer_result = await write_post(
        topic_name=topic["name"],
        topic_description=topic.get("description", ""),
        articles=[selected],
        performance_context=feedback,
    )
    if not writer_result:
        print("  ❌ Writer failed")
        return None

    # 5. Screenshot — my_agent uses lubot.ai, everything else uses article URL
    from src.image_generator import generate_image

    image_path = None

    # My Agent posts screenshot LuBot.ai after clicking Start (SPA needs interaction)
    if category == "my_agent":
        print("  Taking screenshot of lubot.ai (My Agent post)...")
        screenshot = await take_lubot_screenshot()
        if screenshot:
            image_path = screenshot.path
            print(f"  Screenshot saved: {image_path}")
    elif selected.url:
        print(f"  Taking screenshot of article: {selected.url[:80]}...")
        screenshot = await take_screenshot(selected.url)
        if screenshot:
            image_path = screenshot.path
            print(f"  Screenshot saved: {image_path}")

    # Fall back to AI-generated image
    if not image_path:
        print(f"  Fallback: generating AI image for {category}...")
        generated = await generate_image(category, selected.title, writer_result.post_text)
        if generated:
            image_path = generated.path
            print(f"  Generated image saved: {image_path}")
        else:
            print("  ⚠️  All image methods failed (no image)")

    # 5.5. Post-process — enforce rules the LLM can't be trusted to follow
    from src.post_processor import process_post, validate_post

    writer_result.post_text, writer_result.hashtags = process_post(writer_result.post_text, writer_result.hashtags)
    ok, reason = validate_post(writer_result.post_text)
    if not ok:
        print(f"  ⚠️  Post failed validation: {reason}")

    # 6. Save as PENDING
    post = PublisherPost(
        posted_at=datetime.now(UTC),
        topic_category=category,
        topic_title=selected.title,
        source_url=selected.url,
        post_text=writer_result.post_text,
        image_path=image_path,
        hashtags=writer_result.hashtags,
        status="pending",
        day_of_week=day_name.lower(),
    )
    session.add(post)
    session.commit()

    print(f"\n  ✅ POST #{post.id} saved as PENDING")
    print(f"  Category: {category}")
    print(f"  Title: {selected.title}")
    print(f"  Hashtags: {writer_result.hashtags}")
    print(f"  Image: {image_path or 'none'}")
    print("\n  --- POST TEXT ---")
    print(f"  {writer_result.post_text}")
    print("  --- END ---")

    return post


async def main():
    # Use local DB (port 5433)
    import os

    db_url = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher")
    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    # Generate full Sun-Sat week (every category appears exactly once)
    today = date.today()
    # Find the Sunday that starts this week
    days_since_sunday = (today.weekday() + 1) % 7
    start = today - timedelta(days=days_since_sunday)
    print(f"Generating 7 posts starting from {start}")
    print("Using topics rotation for this week\n")

    # Show the rotation for this week
    for i in range(7):
        d = start + timedelta(days=i)
        topic = get_todays_topic(d)
        print(f"  {d.strftime('%A'):>10} {d} → {topic['name']}")

    results = []
    for i in range(7):
        target = start + timedelta(days=i)
        session = Session()
        try:
            post = await generate_single_post(session, target, i + 1)
            results.append((target, post))
        except Exception as e:
            print(f"\n  ❌ FAILED: {e}")
            results.append((target, None))
        finally:
            session.close()

    # Summary
    print(f"\n\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    success = sum(1 for _, p in results if p is not None)
    print(f"Generated: {success}/7 posts\n")
    for d, post in results:
        topic = get_todays_topic(d)
        status = f"✅ #{post.id}" if post else "❌ failed"
        print(f"  {d.strftime('%A'):>10} {d} — {topic['name']:15} {status}")


if __name__ == "__main__":
    asyncio.run(main())
