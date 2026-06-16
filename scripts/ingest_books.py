#!/usr/bin/env python3
"""Ingest book PDFs into the knowledge base (Phase 2.8 / 15c-7).

Extract -> chunk -> embed (NVIDIA) -> store. Idempotent + re-runnable (each book's
rows are replaced by slug). Raw PDFs are KEPT. Real embedding API calls.

Usage (run on the server, from repo root):
    python3 scripts/ingest_books.py                 # all books in books/
    python3 scripts/ingest_books.py books/DDIA.pdf  # one or more specific files
"""

import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

from src.db import SessionLocal, create_tables  # noqa: E402
from src.knowledge_base import ingest_book  # noqa: E402

logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

BOOKS_DIR = Path(__file__).parent.parent / "books"


def main(argv: list[str]) -> int:
    load_dotenv()
    create_tables()  # ensure publisher_knowledge_base exists

    paths = argv or sorted(str(p) for p in BOOKS_DIR.glob("*.pdf"))
    if not paths:
        print(f"No PDFs found in {BOOKS_DIR}")
        return 1

    session = SessionLocal()
    total = 0
    try:
        for path in paths:
            try:
                n = ingest_book(session, path)
                session.commit()
                total += n
                print(f"  OK   {Path(path).name}: {n} chunks")
            except Exception as e:  # one bad book must not abort the rest
                session.rollback()
                print(f"  FAIL {Path(path).name}: {e}")
        print(f"\nDONE: {total} chunks from {len(paths)} book(s). Raw PDFs kept.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
