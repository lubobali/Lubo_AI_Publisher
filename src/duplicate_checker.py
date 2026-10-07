"""Duplicate checker — URL dedup, recency, title similarity, category balance.

There is no embedding check on purpose (removed Oct 2026): the NIM model it used was
retired (410), and measured on real posts no similarity threshold separated a repeated
idea from a new one. Podcast episodes are de-duplicated in podcast_insights instead.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import PublisherPost, PublisherScrapedUrl
from src.observability import get_client, observe

logger = logging.getLogger(__name__)

# News categories enforce a 7-day recency limit
NEWS_CATEGORIES = frozenset({"ai_news", "ai_gadgets", "big_tech"})

# Thresholds
TITLE_SIMILARITY_THRESHOLD = 0.80
CATEGORY_OVERREPRESENTATION_FACTOR = 2.0


@dataclass
class DuplicateResult:
    """Result of a duplicate check."""

    is_duplicate: bool
    reason: str = ""


def levenshtein_ratio(a: str, b: str) -> float:
    """Levenshtein similarity ratio between two strings (0.0 to 1.0).

    Case-insensitive. Returns 1.0 for identical strings, 0.0 for completely different.
    """
    a = a.lower()
    b = b.lower()

    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    len_a, len_b = len(a), len(b)
    # Classic DP Levenshtein distance
    prev = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        curr = [i] + [0] * len_b
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr

    distance = prev[len_b]
    max_len = max(len_a, len_b)
    return 1.0 - (distance / max_len)


def is_too_old(
    published_at: datetime | None,
    category: str,
    max_days: int = 7,
) -> bool:
    """Check if an article is too old for its category.

    News categories (ai_news, ai_gadgets, big_tech) reject articles older than max_days.
    Non-news categories have no recency requirement.
    Articles without a date are not rejected.
    """
    if published_at is None:
        return False
    if category not in NEWS_CATEGORIES:
        return False

    age = datetime.now(UTC) - published_at
    return age > timedelta(days=max_days)


class DuplicateChecker:
    """Checks articles for duplicates using URL, recency, title, and category balance."""

    def __init__(self, session: Session):
        self.session = session

    # --- URL dedup ---

    def is_url_seen(self, url: str) -> bool:
        """Check if URL exists in publisher_scraped_urls."""
        result = self.session.query(PublisherScrapedUrl).filter_by(url=url).first()
        return result is not None

    def record_url(self, url: str, used: bool = False) -> None:
        """Save URL to publisher_scraped_urls."""
        self.session.add(PublisherScrapedUrl(url=url, used=used))
        self.session.flush()

    # --- Title similarity ---

    def check_title_against_recent(
        self,
        title: str,
        days: int = 90,
        threshold: float = TITLE_SIMILARITY_THRESHOLD,
    ) -> DuplicateResult:
        """Check title against posts from last N days using Levenshtein similarity."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        recent_posts = self.session.query(PublisherPost).filter(PublisherPost.posted_at >= cutoff).all()

        for post in recent_posts:
            ratio = levenshtein_ratio(title, post.topic_title)
            if ratio >= threshold:
                return DuplicateResult(
                    is_duplicate=True,
                    reason=f"Title too similar to post #{post.id} ({ratio:.0%} match): {post.topic_title!r}",
                )

        return DuplicateResult(is_duplicate=False)

    # --- Category balance ---

    def get_category_counts(self, days: int = 30) -> dict[str, int]:
        """Get post counts per category for last N days."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        rows = (
            self.session.query(PublisherPost.topic_category, func.count(PublisherPost.id))
            .filter(PublisherPost.posted_at >= cutoff)
            .group_by(PublisherPost.topic_category)
            .all()
        )
        return {category: count for category, count in rows}

    def is_category_overrepresented(
        self,
        category: str,
        days: int = 30,
        factor: float = CATEGORY_OVERREPRESENTATION_FACTOR,
    ) -> bool:
        """Check if category has more than factor * average posts.

        Returns False if fewer than 2 categories have posts (not enough data).
        """
        counts = self.get_category_counts(days)
        if not counts or len(counts) < 2:
            return False

        avg = sum(counts.values()) / len(counts)
        category_count = counts.get(category, 0)

        return category_count > avg * factor

    # --- Langfuse metadata reporting ---

    def _report_check_metadata(
        self,
        url: str,
        title: str,
        category: str,
        is_duplicate: bool,
        caught_by: str | None,
    ) -> None:
        """Report duplicate check results to Langfuse."""
        try:
            get_client().update_current_span(
                metadata={
                    "url": url,
                    "title": title,
                    "category": category,
                    "is_duplicate": is_duplicate,
                    "caught_by": caught_by,
                }
            )
        except Exception:
            logger.debug("Langfuse check_article reporting failed", exc_info=True)

    # --- Full orchestration ---

    @observe()
    async def check_article(
        self,
        url: str,
        title: str,
        category: str,
        published_at: datetime | None = None,
    ) -> DuplicateResult:
        """Run all duplicate checks on an article.

        Checks in order (cheapest first):
        1. URL dedup (DB lookup)
        2. Recency (pure date check)
        3. Title similarity (DB + Levenshtein)
        4. Category balance (DB aggregate, logged only)
        """
        # 1. URL dedup
        if self.is_url_seen(url):
            result = DuplicateResult(is_duplicate=True, reason=f"URL already seen: {url}")
            self._report_check_metadata(url, title, category, True, "url_dedup")
            return result

        # 2. Recency
        if is_too_old(published_at, category):
            result = DuplicateResult(
                is_duplicate=True,
                reason=f"Article too old for {category} (published {published_at})",
            )
            self._report_check_metadata(url, title, category, True, "recency")
            return result

        # 3. Title similarity
        title_result = self.check_title_against_recent(title)
        if title_result.is_duplicate:
            self._report_check_metadata(url, title, category, True, "title_similarity")
            return title_result

        # 4. Category balance (warning, not blocking — logged for pipeline to decide)
        if self.is_category_overrepresented(category):
            logger.info("Category %s is overrepresented but not blocking", category)

        self._report_check_metadata(url, title, category, False, None)
        return DuplicateResult(is_duplicate=False)
