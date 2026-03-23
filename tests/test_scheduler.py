"""Tests for daily pipeline scheduler — full flow from topic to pending post."""

import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherPost
from src.scheduler import Pipeline, approve_post, publish_approved_posts, reject_post
from src.scraper import ScrapedArticle
from src.writer import WriterResult

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher")


@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    session = sessionmaker(bind=test_engine)()
    yield session
    session.rollback()
    session.close()


def _make_articles():
    return [
        ScrapedArticle(
            title="New AI Chip Breaks Speed Records",
            url="https://example.com/ai-chip",
            summary="A new chip achieves 10x performance.",
            source="TechCrunch",
            published_at=datetime.now(UTC) - timedelta(hours=6),
        ),
        ScrapedArticle(
            title="Second Article About Something",
            url="https://example.com/second",
            summary="Another topic.",
            source="HackerNews",
            published_at=datetime.now(UTC) - timedelta(hours=12),
        ),
    ]


def _make_writer_result():
    return WriterResult(
        post_text="Just tested the new AI chip. 10x faster than last gen. Wild times.",
        screenshot_url="https://example.com/ai-chip",
        hashtags=["#AI", "#chips"],
    )


# ---------------------------------------------------------------------------
# Pipeline: generate post (saves as PENDING)
# ---------------------------------------------------------------------------


class TestPipelineGenerate:
    @pytest.mark.asyncio
    async def test_successful_pipeline_creates_pending_post(self, db_session):
        """Full pipeline: scrape → dedup → write → screenshot → save as pending."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/shot.png")
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = "No data yet"
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(
                target_date=date(2026, 3, 23),
            )

        assert result.success is True
        assert result.post_id is not None

        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post is not None
        assert post.status == "pending"
        assert post.post_text == writer_result.post_text

    @pytest.mark.asyncio
    async def test_pipeline_skips_duplicate_articles(self, db_session):
        """Pipeline tries next article when first is a duplicate."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        duplicate_result = MagicMock(is_duplicate=True, reason="URL seen")
        not_duplicate = MagicMock(is_duplicate=False)

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/shot.png")
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            # First article is duplicate, second is not
            mock_dedup.check_article = AsyncMock(side_effect=[duplicate_result, not_duplicate])
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = "No data"
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        # Used the second article's URL
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.source_url == "https://example.com/second"

    @pytest.mark.asyncio
    async def test_pipeline_fails_when_no_articles(self, db_session):
        """Pipeline fails gracefully when scraper returns empty."""
        with patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=[]):
            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "no articles" in result.error.lower()

    @pytest.mark.asyncio
    async def test_pipeline_fails_when_all_duplicates(self, db_session):
        """Pipeline fails when all articles are duplicates."""
        articles = _make_articles()
        dup = MagicMock(is_duplicate=True, reason="duplicate")

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=dup)
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "duplicate" in result.error.lower()

    @pytest.mark.asyncio
    async def test_pipeline_handles_writer_failure(self, db_session):
        """Pipeline fails gracefully when writer returns None."""
        articles = _make_articles()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "writer" in result.error.lower()


# ---------------------------------------------------------------------------
# Approve / Reject workflow
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Screenshot fallback chain: AI URL → article URL → generate image
# ---------------------------------------------------------------------------


class TestScreenshotFallback:
    @pytest.mark.asyncio
    async def test_uses_article_url_for_screenshot(self, db_session):
        """Screenshot should use the real article URL, not AI-suggested URL."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_screenshot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            # Single call with article URL succeeds
            mock_screenshot.return_value = MagicMock(path="/tmp/fallback.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.image_path == "/tmp/fallback.png"

    @pytest.mark.asyncio
    async def test_falls_back_to_generated_image_when_all_screenshots_fail(self, db_session):
        """When both screenshot URLs fail, generate an AI image."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock) as mock_gen,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_gen.return_value = MagicMock(path="/tmp/generated.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.image_path == "/tmp/generated.png"
        mock_gen.assert_called_once()


class TestApprovalWorkflow:
    def test_approve_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        approve_post(db_session, post.id)
        db_session.refresh(post)
        assert post.status == "approved"

    def test_reject_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        reject_post(db_session, post.id)
        db_session.refresh(post)
        assert post.status == "rejected"

    def test_cannot_approve_rejected_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="rejected",
        )
        db_session.add(post)
        db_session.flush()

        result = approve_post(db_session, post.id)
        assert result is False

    def test_approve_nonexistent_post(self, db_session):
        result = approve_post(db_session, 99999)
        assert result is False


# ---------------------------------------------------------------------------
# Publish approved posts
# ---------------------------------------------------------------------------


class TestPublishApproved:
    @pytest.mark.asyncio
    async def test_publishes_approved_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="My post text",
            image_path="/tmp/image.png",
            status="approved",
        )
        db_session.add(post)
        db_session.flush()

        mock_publisher = AsyncMock()
        mock_publisher.publish_image = AsyncMock(return_value="urn:li:share:pub123")
        mock_publisher.platform_name = "linkedin"

        with (
            patch("src.scheduler.get_publisher", return_value=mock_publisher),
            patch("builtins.open", MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"img")))),
        ):
            count = await publish_approved_posts(db_session, access_token="test")

        assert count >= 1
        db_session.refresh(post)
        assert post.status == "published"

    @pytest.mark.asyncio
    async def test_skips_non_approved_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        count = await publish_approved_posts(db_session, access_token="test")
        assert count == 0
