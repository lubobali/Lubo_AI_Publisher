"""Duplicate checker — URL dedup, title similarity, embedding similarity, category balance."""

import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from openai import AsyncOpenAI
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import PublisherPost, PublisherScrapedUrl

logger = logging.getLogger(__name__)

# News categories enforce a 7-day recency limit
NEWS_CATEGORIES = frozenset({"ai_news", "ai_gadgets", "big_tech"})

# NVIDIA NIM embedding model
NVIDIA_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Thresholds
TITLE_SIMILARITY_THRESHOLD = 0.80
EMBEDDING_SIMILARITY_THRESHOLD = 0.85
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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (-1.0 to 1.0).

    Returns 0.0 if either vector is zero.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


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
    """Checks articles for duplicates using URL, title, embedding, and category balance."""

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

    # --- Embedding similarity ---

    async def get_embedding(self, text: str) -> list[float] | None:
        """Get embedding vector from NVIDIA NIM API.

        Returns list of floats on success, None on failure.
        """
        try:
            client = AsyncOpenAI(
                api_key=os.getenv("NVIDIA_API_KEY", ""),
                base_url=NVIDIA_BASE_URL,
            )
            response = await client.embeddings.create(
                model=NVIDIA_EMBED_MODEL,
                input=text,
                extra_body={"input_type": "query"},
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Failed to get embedding: %s", e)
            return None

    def check_embedding_against_recent(
        self,
        embedding: list[float],
        days: int = 90,
        threshold: float = EMBEDDING_SIMILARITY_THRESHOLD,
    ) -> DuplicateResult:
        """Check embedding similarity against recent posts with stored embeddings."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        recent_posts = (
            self.session.query(PublisherPost)
            .filter(
                PublisherPost.posted_at >= cutoff,
                PublisherPost.post_embedding.isnot(None),
            )
            .all()
        )

        for post in recent_posts:
            sim = cosine_similarity(embedding, post.post_embedding)
            if sim >= threshold:
                return DuplicateResult(
                    is_duplicate=True,
                    reason=f"Embedding too similar to post #{post.id} "
                    f"({sim:.2f} cosine similarity): {post.topic_title!r}",
                )

        return DuplicateResult(is_duplicate=False)

    # --- Full orchestration ---

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
        4. Category balance (DB aggregate)
        5. Embedding similarity (API call + DB)
        """
        # 1. URL dedup
        if self.is_url_seen(url):
            return DuplicateResult(is_duplicate=True, reason=f"URL already seen: {url}")

        # 2. Recency
        if is_too_old(published_at, category):
            return DuplicateResult(
                is_duplicate=True,
                reason=f"Article too old for {category} (published {published_at})",
            )

        # 3. Title similarity
        title_result = self.check_title_against_recent(title)
        if title_result.is_duplicate:
            return title_result

        # 4. Category balance (warning, not blocking — logged for pipeline to decide)
        if self.is_category_overrepresented(category):
            logger.info("Category %s is overrepresented but not blocking", category)

        # 5. Embedding similarity
        embedding = await self.get_embedding(title)
        if embedding is not None:
            embed_result = self.check_embedding_against_recent(embedding)
            if embed_result.is_duplicate:
                return embed_result

        return DuplicateResult(is_duplicate=False)
