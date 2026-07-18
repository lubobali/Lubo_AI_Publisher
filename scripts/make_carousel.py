"""Generate ONE swipeable carousel (Phase 2.21) and save it as PENDING for review.

Runs the full pipeline (topic -> scrape -> dedup -> carousel writer -> slide render + real
topic-card splice) with as_carousel=True, so a multi-image carousel lands on the dashboard.
Run it inside the worker container (or with DATABASE_URL pointed at the app `publisher` DB):

    python -m scripts.make_carousel            # today's rotation topic
    python -m scripts.make_carousel ai_news    # a specific category (sources_key)
"""

import logging
import sys

from src.cron import generate_carousel_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    category = sys.argv[1] if len(sys.argv) > 1 else None
    post_id = generate_carousel_now(category)
    if post_id:
        print(f"OK — pending carousel #{post_id}. Review it on the dashboard.")
        sys.exit(0)
    print("FAILED — see logs above.")
    sys.exit(1)
