"""Tests for analytics worker — fetch engagement metrics, update topic performance."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics_worker import AnalyticsWorker, calculate_engagement_rate, recalculate_topic_performance
from src.models import Base, PublisherAnalytics, PublisherPost, PublisherTopicPerformance

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")


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


# ---------------------------------------------------------------------------
# Pure function: engagement rate calculation
# ---------------------------------------------------------------------------


class TestEngagementRate:
    def test_basic_calculation(self):
        rate = calculate_engagement_rate(likes=10, comments=5, shares=2, impressions=1000)
        assert rate == pytest.approx(0.017)

    def test_zero_impressions_returns_zero(self):
        assert calculate_engagement_rate(likes=10, comments=5, shares=2, impressions=0) == 0.0

    def test_no_engagement_returns_zero(self):
        assert calculate_engagement_rate(likes=0, comments=0, shares=0, impressions=1000) == 0.0

    def test_high_engagement(self):
        rate = calculate_engagement_rate(likes=100, comments=50, shares=20, impressions=500)
        assert rate == pytest.approx(0.34)


# ---------------------------------------------------------------------------
# Fetch metrics from LinkedIn API (mocked)
# ---------------------------------------------------------------------------


class TestFetchMetrics:
    @pytest.mark.asyncio
    async def test_fetch_post_metrics_returns_dict(self):
        """Successful API call returns engagement metrics dict."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "likes": {"paging": {"total": 25}},
            "comments": {"paging": {"total": 8}},
        }

        worker = AnalyticsWorker(access_token="test_token")
        worker._api_get = AsyncMock(return_value=mock_response)
        metrics = await worker.fetch_post_metrics("urn:li:share:12345")

        assert metrics["likes"] == 25
        assert metrics["comments"] == 8

    @pytest.mark.asyncio
    async def test_fetch_post_metrics_api_error_returns_none(self):
        """API failure returns None."""
        worker = AnalyticsWorker(access_token="test_token")
        worker._api_get = AsyncMock(side_effect=Exception("API down"))
        metrics = await worker.fetch_post_metrics("urn:li:share:12345")

        assert metrics is None


# ---------------------------------------------------------------------------
# Store metrics in DB
# ---------------------------------------------------------------------------


class TestStoreMetrics:
    def test_store_creates_analytics_record(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            linkedin_post_urn="urn:li:share:111",
            status="published",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        worker.store_metrics(
            post_id=post.id,
            post_urn="urn:li:share:111",
            metrics={"likes": 15, "comments": 3, "shares": 1, "impressions": 500, "clicks": 20},
        )

        record = db_session.query(PublisherAnalytics).filter_by(post_id=post.id).first()
        assert record is not None
        assert record.likes == 15
        assert record.comments == 3
        assert record.shares == 1
        assert record.impressions == 500
        assert record.clicks == 20
        assert record.engagement_rate == pytest.approx(0.038)

    def test_store_with_zero_impressions(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            linkedin_post_urn="urn:li:share:222",
            status="published",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        worker.store_metrics(
            post_id=post.id,
            post_urn="urn:li:share:222",
            metrics={"likes": 0, "comments": 0, "shares": 0, "impressions": 0, "clicks": 0},
        )

        record = db_session.query(PublisherAnalytics).filter_by(post_id=post.id).first()
        assert record.engagement_rate == 0.0


# ---------------------------------------------------------------------------
# Recalculate topic performance
# ---------------------------------------------------------------------------


class TestRecalculateTopicPerformance:
    def test_calculates_averages(self, db_session):
        """Topic performance should average metrics across posts."""
        for i in range(3):
            post = PublisherPost(
                posted_at=datetime.now(UTC) - timedelta(days=i),
                topic_category="biohacker",
                topic_title=f"Bio post {i}",
                post_text="text",
                linkedin_post_urn=f"urn:li:share:bio{i}",
                status="published",
            )
            db_session.add(post)
            db_session.flush()

            db_session.add(
                PublisherAnalytics(
                    post_id=post.id,
                    linkedin_post_urn=f"urn:li:share:bio{i}",
                    checked_at=datetime.now(UTC),
                    likes=10 * (i + 1),
                    comments=2 * (i + 1),
                    impressions=100 * (i + 1),
                    engagement_rate=0.12,
                )
            )
        db_session.flush()

        recalculate_topic_performance(db_session)

        perf = db_session.query(PublisherTopicPerformance).filter_by(topic_category="biohacker").first()
        assert perf is not None
        assert perf.total_posts == 3
        assert perf.avg_likes == pytest.approx(20.0)  # (10+20+30)/3
        assert perf.avg_comments == pytest.approx(4.0)  # (2+4+6)/3

    def test_empty_db_no_crash(self, db_session):
        """Recalculating with no data should not crash."""
        recalculate_topic_performance(db_session)

    def test_multiple_categories(self, db_session):
        """Performance should be calculated per category."""
        for cat in ["ai_news", "tech_talk"]:
            post = PublisherPost(
                posted_at=datetime.now(UTC),
                topic_category=cat,
                topic_title=f"Post about {cat}",
                post_text="text",
                linkedin_post_urn=f"urn:li:share:{cat}",
                status="published",
            )
            db_session.add(post)
            db_session.flush()

            db_session.add(
                PublisherAnalytics(
                    post_id=post.id,
                    linkedin_post_urn=f"urn:li:share:{cat}",
                    checked_at=datetime.now(UTC),
                    likes=50 if cat == "ai_news" else 10,
                    comments=5,
                    impressions=1000,
                    engagement_rate=0.05,
                )
            )
        db_session.flush()

        recalculate_topic_performance(db_session)

        ai = db_session.query(PublisherTopicPerformance).filter_by(topic_category="ai_news").first()
        tech = db_session.query(PublisherTopicPerformance).filter_by(topic_category="tech_talk").first()
        assert ai.avg_likes > tech.avg_likes


# ---------------------------------------------------------------------------
# Get posts needing metrics update
# ---------------------------------------------------------------------------


class TestGetPostsForUpdate:
    def test_returns_published_posts_with_urns(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC) - timedelta(days=3),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            linkedin_post_urn="urn:li:share:update1",
            status="published",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        posts = worker.get_posts_for_update(days=7)
        urns = [p.linkedin_post_urn for p in posts]
        assert "urn:li:share:update1" in urns

    def test_skips_pending_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC) - timedelta(days=1),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            linkedin_post_urn="urn:li:share:pending1",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        posts = worker.get_posts_for_update(days=7)
        urns = [p.linkedin_post_urn for p in posts]
        assert "urn:li:share:pending1" not in urns

    def test_skips_posts_without_urn(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC) - timedelta(days=1),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            linkedin_post_urn=None,
            status="published",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        posts = worker.get_posts_for_update(days=7)
        assert post not in posts

    def test_skips_old_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC) - timedelta(days=30),
            topic_category="ai_news",
            topic_title="Old post",
            post_text="text",
            linkedin_post_urn="urn:li:share:old1",
            status="published",
        )
        db_session.add(post)
        db_session.flush()

        worker = AnalyticsWorker(access_token="test", session=db_session)
        posts = worker.get_posts_for_update(days=7)
        urns = [p.linkedin_post_urn for p in posts]
        assert "urn:li:share:old1" not in urns
