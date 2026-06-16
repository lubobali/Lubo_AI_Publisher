"""Tests for duplicate checker — URL dedup, title similarity, embeddings, category balance."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.duplicate_checker import (
    NVIDIA_EMBED_MODEL,
    DuplicateChecker,
    cosine_similarity,
    is_too_old,
    levenshtein_ratio,
)
from src.models import Base, PublisherPost, PublisherScrapedUrl

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")


@pytest.fixture(scope="module")
def test_engine():
    """Create a test engine and tables, tear down after all tests."""
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    """Create a fresh session for each test, rollback after."""
    session = sessionmaker(bind=test_engine)()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Pure functions: levenshtein_ratio
# ---------------------------------------------------------------------------


class TestLevenshteinRatio:
    """Title similarity via Levenshtein distance ratio."""

    def test_identical_strings(self):
        assert levenshtein_ratio("hello world", "hello world") == 1.0

    def test_completely_different(self):
        ratio = levenshtein_ratio("abc", "xyz")
        assert ratio < 0.5

    def test_similar_titles(self):
        a = "NVIDIA launches new GPU for AI training"
        b = "NVIDIA launches new GPU for AI inference"
        ratio = levenshtein_ratio(a, b)
        assert ratio > 0.7

    def test_empty_strings(self):
        assert levenshtein_ratio("", "") == 1.0

    def test_one_empty(self):
        assert levenshtein_ratio("hello", "") == 0.0
        assert levenshtein_ratio("", "hello") == 0.0

    def test_case_insensitive(self):
        """Similarity should be case-insensitive."""
        ratio = levenshtein_ratio("Hello World", "hello world")
        assert ratio == 1.0


# ---------------------------------------------------------------------------
# Pure functions: cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    """Cosine similarity between embedding vectors."""

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_similar_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [1.1, 2.1, 2.9]
        sim = cosine_similarity(a, b)
        assert sim > 0.99

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# Pure functions: is_too_old
# ---------------------------------------------------------------------------

NEWS_CATEGORIES = {"ai_news", "ai_gadgets", "big_tech"}
NON_NEWS_CATEGORIES = {"tech_talk", "my_agent", "biohacker", "my_agent_git"}


class TestIsTooOld:
    """Recency check — news categories reject articles older than 7 days."""

    def test_recent_news_passes(self):
        recent = datetime.now(UTC) - timedelta(days=2)
        assert is_too_old(recent, "ai_news") is False

    def test_old_news_rejected(self):
        old = datetime.now(UTC) - timedelta(days=10)
        assert is_too_old(old, "ai_news") is True

    def test_boundary_7_days_passes(self):
        """Article just under 7 days old should pass."""
        boundary = datetime.now(UTC) - timedelta(days=6, hours=23, minutes=59)
        assert is_too_old(boundary, "ai_news") is False

    @pytest.mark.parametrize("category", ["tech_talk", "my_agent", "biohacker", "my_agent_git"])
    def test_non_news_categories_exempt(self, category):
        """Non-news categories don't have a recency requirement."""
        old = datetime.now(UTC) - timedelta(days=30)
        assert is_too_old(old, category) is False

    def test_none_date_passes(self):
        """Articles without a date are not rejected for recency."""
        assert is_too_old(None, "ai_news") is False

    @pytest.mark.parametrize("category", ["ai_news", "ai_gadgets", "big_tech"])
    def test_news_categories_enforce_recency(self, category):
        old = datetime.now(UTC) - timedelta(days=14)
        assert is_too_old(old, category) is True


# ---------------------------------------------------------------------------
# DB methods: URL dedup
# ---------------------------------------------------------------------------


class TestUrlDedup:
    """URL duplicate detection against publisher_scraped_urls table."""

    def test_unseen_url_not_duplicate(self, db_session):
        checker = DuplicateChecker(db_session)
        assert checker.is_url_seen("https://example.com/new-article") is False

    def test_seen_url_is_duplicate(self, db_session):
        db_session.add(PublisherScrapedUrl(url="https://example.com/old-article"))
        db_session.flush()

        checker = DuplicateChecker(db_session)
        assert checker.is_url_seen("https://example.com/old-article") is True

    def test_record_url(self, db_session):
        checker = DuplicateChecker(db_session)
        checker.record_url("https://example.com/recorded")

        result = db_session.query(PublisherScrapedUrl).filter_by(url="https://example.com/recorded").first()
        assert result is not None
        assert result.used is False

    def test_record_url_with_used_flag(self, db_session):
        checker = DuplicateChecker(db_session)
        checker.record_url("https://example.com/used-url", used=True)

        result = db_session.query(PublisherScrapedUrl).filter_by(url="https://example.com/used-url").first()
        assert result.used is True


# ---------------------------------------------------------------------------
# DB methods: title similarity against recent posts
# ---------------------------------------------------------------------------


class TestTitleDuplicateCheck:
    """Title fuzzy match against recent posts in DB."""

    def test_no_recent_posts_not_duplicate(self, db_session):
        checker = DuplicateChecker(db_session)
        result = checker.check_title_against_recent("Brand new topic nobody posted about")
        assert result.is_duplicate is False

    def test_very_similar_title_flagged(self, db_session):
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=5),
                topic_category="ai_news",
                topic_title="NVIDIA launches new GPU for AI training",
                post_text="Some post text",
                status="published",
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        result = checker.check_title_against_recent("NVIDIA launches new GPU for AI training tasks")
        assert result.is_duplicate is True
        assert "title" in result.reason.lower()

    def test_different_title_passes(self, db_session):
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=5),
                topic_category="ai_news",
                topic_title="OpenAI releases GPT-5",
                post_text="Some post text",
                status="published",
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        result = checker.check_title_against_recent("Google launches Gemini Ultra 2.0")
        assert result.is_duplicate is False

    def test_old_post_outside_window_ignored(self, db_session):
        """Posts older than 90 days should not trigger title match."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=100),
                topic_category="ai_news",
                topic_title="NVIDIA launches new GPU for AI training",
                post_text="Some post text",
                status="published",
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        result = checker.check_title_against_recent("NVIDIA launches new GPU for AI training")
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# DB methods: category balance
# ---------------------------------------------------------------------------


class TestCategoryBalance:
    """Category balance check — prevent over-representation."""

    def test_empty_db_balanced(self, db_session):
        checker = DuplicateChecker(db_session)
        assert checker.is_category_overrepresented("ai_news") is False

    def test_balanced_categories(self, db_session):
        """Even distribution across categories is fine."""
        categories = ["ai_news", "tech_talk", "biohacker", "big_tech"]
        for cat in categories:
            for i in range(3):
                db_session.add(
                    PublisherPost(
                        posted_at=datetime.now(UTC) - timedelta(days=i + 1),
                        topic_category=cat,
                        topic_title=f"Post {i} about {cat}",
                        post_text="text",
                        status="published",
                    )
                )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        assert checker.is_category_overrepresented("ai_news") is False

    def test_overrepresented_category_flagged(self, db_session):
        """A category with way more posts than average gets flagged."""
        # Add 10 ai_news posts, 1 each for others
        for i in range(10):
            db_session.add(
                PublisherPost(
                    posted_at=datetime.now(UTC) - timedelta(days=i + 1),
                    topic_category="ai_news",
                    topic_title=f"AI news post {i}",
                    post_text="text",
                    status="published",
                )
            )
        for cat in ["tech_talk", "biohacker"]:
            db_session.add(
                PublisherPost(
                    posted_at=datetime.now(UTC) - timedelta(days=1),
                    topic_category=cat,
                    topic_title=f"Post about {cat}",
                    post_text="text",
                    status="published",
                )
            )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        assert checker.is_category_overrepresented("ai_news") is True

    def test_get_category_counts(self, db_session):
        """get_category_counts returns post counts per category."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=1),
                topic_category="ai_news",
                topic_title="Test",
                post_text="text",
                status="published",
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        counts = checker.get_category_counts(days=30)
        assert counts.get("ai_news", 0) >= 1


# ---------------------------------------------------------------------------
# NVIDIA embedding API (mocked)
# ---------------------------------------------------------------------------


class TestGetEmbedding:
    """Get embedding vector from NVIDIA NIM API — always mocked."""

    @pytest.mark.asyncio
    async def test_get_embedding_returns_vector(self):
        """Successful API call returns list of floats."""
        mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client):
            checker = DuplicateChecker(session=None)
            result = await checker.get_embedding("Test text about AI")

        assert result == mock_embedding
        mock_client.embeddings.create.assert_called_once_with(
            model=NVIDIA_EMBED_MODEL,
            input="Test text about AI",
            extra_body={"input_type": "query"},
        )

    @pytest.mark.asyncio
    async def test_get_embedding_api_error_returns_none(self):
        """API failure returns None gracefully."""
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=Exception("API down"))

        with patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client):
            checker = DuplicateChecker(session=None)
            result = await checker.get_embedding("Some text")

        assert result is None


# ---------------------------------------------------------------------------
# Embedding similarity against recent posts
# ---------------------------------------------------------------------------


class TestEmbeddingSimilarity:
    """Check embedding similarity against recent posts with stored embeddings."""

    def test_no_recent_embeddings_not_duplicate(self, db_session):
        """No posts with embeddings — not a duplicate."""
        checker = DuplicateChecker(db_session)
        result = checker.check_embedding_against_recent([0.1, 0.2, 0.3])
        assert result.is_duplicate is False

    def test_similar_embedding_flagged(self, db_session):
        """Post with very similar embedding should be flagged."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=5),
                topic_category="ai_news",
                topic_title="Some AI post",
                post_text="text",
                status="published",
                post_embedding=[1.0, 0.0, 0.0],
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        # Very similar vector (cosine > 0.85)
        result = checker.check_embedding_against_recent([0.99, 0.01, 0.0])
        assert result.is_duplicate is True
        assert "embedding" in result.reason.lower()

    def test_different_embedding_passes(self, db_session):
        """Post with different embedding should pass."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=5),
                topic_category="ai_news",
                topic_title="Some AI post",
                post_text="text",
                status="published",
                post_embedding=[1.0, 0.0, 0.0],
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        # Orthogonal vector (cosine = 0)
        result = checker.check_embedding_against_recent([0.0, 1.0, 0.0])
        assert result.is_duplicate is False

    def test_old_embedding_outside_window_ignored(self, db_session):
        """Posts older than 90 days should not trigger embedding match."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=100),
                topic_category="ai_news",
                topic_title="Old post",
                post_text="text",
                status="published",
                post_embedding=[1.0, 0.0, 0.0],
            )
        )
        db_session.flush()

        checker = DuplicateChecker(db_session)
        result = checker.check_embedding_against_recent([1.0, 0.0, 0.0])
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Full check_article orchestration
# ---------------------------------------------------------------------------


class TestCheckArticle:
    """Full duplicate check pipeline — wires URL, title, embedding, recency, category."""

    @pytest.mark.asyncio
    async def test_new_article_passes_all_checks(self, db_session):
        """A genuinely new article passes all duplicate checks."""
        mock_embedding = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/brand-new",
                title="Completely unique article about quantum computing",
                category="ai_news",
                published_at=datetime.now(UTC) - timedelta(days=1),
            )

        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_duplicate_url_caught(self, db_session):
        """Article with a previously seen URL is caught immediately."""
        db_session.add(PublisherScrapedUrl(url="https://example.com/seen"))
        db_session.flush()

        checker = DuplicateChecker(db_session)
        result = await checker.check_article(
            url="https://example.com/seen",
            title="Some title",
            category="ai_news",
        )

        assert result.is_duplicate is True
        assert "url" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_old_news_article_caught(self, db_session):
        """Old news article is caught by recency check."""
        checker = DuplicateChecker(db_session)
        result = await checker.check_article(
            url="https://example.com/old-news",
            title="Some old news",
            category="ai_news",
            published_at=datetime.now(UTC) - timedelta(days=14),
        )

        assert result.is_duplicate is True
        assert "old" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_similar_title_caught(self, db_session):
        """Article with similar title to recent post is caught."""
        db_session.add(
            PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=3),
                topic_category="ai_news",
                topic_title="NVIDIA announces breakthrough in AI chip design",
                post_text="text",
                status="published",
            )
        )
        db_session.flush()

        # Mock embedding to return something dissimilar
        mock_embedding = [0.1, 0.9, 0.1]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/new-url",
                title="NVIDIA announces breakthrough in AI chip designs",
                category="ai_news",
                published_at=datetime.now(UTC) - timedelta(hours=2),
            )

        assert result.is_duplicate is True
        assert "title" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_embedding_api_failure_still_checks_other_signals(self, db_session):
        """If embedding API fails, other checks still run and article can pass."""
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=Exception("API down"))

        with patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/unique-url",
                title="Totally unique article nobody posted about",
                category="tech_talk",
                published_at=datetime.now(UTC) - timedelta(hours=1),
            )

        assert result.is_duplicate is False
