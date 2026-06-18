"""Ingest finance-blog RSS content into the knowledge base for Stock Talk grounding.

Phase 2.10. Run periodically (e.g. weekly) — ingest_rss_feed accumulates posts
over time (one slug per post), so the wisdom well grows as blogs publish.

Usage:
    python3 scripts/ingest_stock_feeds.py
    DATABASE_URL=...publisher_test python3 scripts/ingest_stock_feeds.py   # scratch run
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import SessionLocal  # noqa: E402
from src.knowledge_base import ingest_rss_feed  # noqa: E402

# (feed_url, display title [metadata only — never named in posts], slug prefix)
FEEDS = [
    ("https://ofdollarsanddata.com/feed/", "Of Dollars and Data", "blog-odad"),
    ("https://awealthofcommonsense.com/feed/", "A Wealth of Common Sense", "blog-awocs"),
    ("https://jlcollinsnh.com/feed/", "JL Collins", "blog-jlcollins"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session = SessionLocal()
    total = 0
    try:
        for url, title, slug in FEEDS:
            try:
                n = ingest_rss_feed(session, url, title, slug)
                session.commit()
                total += n
                print(f"  OK   {title}: {n} chunks")
            except Exception as e:
                session.rollback()
                print(f"  FAIL {title}: {e}")
        print(f"Total chunks ingested: {total}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
