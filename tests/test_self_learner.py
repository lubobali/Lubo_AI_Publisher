"""Tests for self-learning engine — performance reports and content adjustments."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherAnalytics, PublisherPost, PublisherTopicPerformance
from src.self_learner import SelfLearner, TopicInsight

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


def _seed_topic_performance(session, data: list[dict]):
    """Helper to seed publisher_topic_performance rows."""
    for d in data:
        session.add(PublisherTopicPerformance(**d))
    session.flush()


def _seed_posts_with_analytics(session, posts: list[dict]):
    """Helper to seed posts with analytics records."""
    for p in posts:
        analytics_data = p.pop("analytics", {})
        post = PublisherPost(**p)
        session.add(post)
        session.flush()
        if analytics_data:
            session.add(
                PublisherAnalytics(
                    post_id=post.id,
                    linkedin_post_urn=p.get("linkedin_post_urn", f"urn:li:share:{post.id}"),
                    checked_at=datetime.now(UTC),
                    **analytics_data,
                )
            )
    session.flush()


# ---------------------------------------------------------------------------
# TopicInsight dataclass
# ---------------------------------------------------------------------------


class TestTopicInsight:
    def test_trending_detection(self):
        insight = TopicInsight(
            category="biohacker",
            avg_likes=50.0,
            avg_comments=15.0,
            avg_engagement_rate=0.05,
            total_posts=10,
            trend="up",
        )
        assert insight.trend == "up"

    def test_declining_detection(self):
        insight = TopicInsight(
            category="big_tech",
            avg_likes=10.0,
            avg_comments=2.0,
            avg_engagement_rate=0.01,
            total_posts=10,
            trend="down",
        )
        assert insight.trend == "down"


# ---------------------------------------------------------------------------
# Performance report generation
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_empty_db_returns_empty_report(self, db_session):
        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        assert report is not None
        assert report.insights == []

    def test_report_includes_all_categories(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {"topic_category": "ai_news", "total_posts": 5, "avg_likes": 30, "avg_comments": 8},
                {"topic_category": "biohacker", "total_posts": 5, "avg_likes": 50, "avg_comments": 15},
                {"topic_category": "big_tech", "total_posts": 5, "avg_likes": 10, "avg_comments": 2},
            ],
        )

        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        categories = [i.category for i in report.insights]
        assert "ai_news" in categories
        assert "biohacker" in categories
        assert "big_tech" in categories

    def test_report_ranked_by_engagement(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {
                    "topic_category": "ai_news",
                    "total_posts": 5,
                    "avg_likes": 30,
                    "avg_comments": 8,
                    "avg_engagement_rate": 0.03,
                },
                {
                    "topic_category": "biohacker",
                    "total_posts": 5,
                    "avg_likes": 50,
                    "avg_comments": 15,
                    "avg_engagement_rate": 0.06,
                },
            ],
        )

        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        # Biohacker should rank first (higher engagement)
        assert report.insights[0].category == "biohacker"

    def test_report_identifies_best_and_worst(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {
                    "topic_category": "biohacker",
                    "total_posts": 5,
                    "avg_likes": 50,
                    "avg_comments": 15,
                    "avg_engagement_rate": 0.06,
                },
                {
                    "topic_category": "big_tech",
                    "total_posts": 5,
                    "avg_likes": 10,
                    "avg_comments": 2,
                    "avg_engagement_rate": 0.01,
                },
            ],
        )

        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        assert report.best_category == "biohacker"
        assert report.worst_category == "big_tech"


# ---------------------------------------------------------------------------
# Format report for AI writer context
# ---------------------------------------------------------------------------


class TestFormatForWriter:
    def test_format_returns_string(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {
                    "topic_category": "ai_news",
                    "total_posts": 5,
                    "avg_likes": 30,
                    "avg_comments": 8,
                    "avg_engagement_rate": 0.03,
                },
            ],
        )

        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        text = report.format_for_writer()
        assert isinstance(text, str)
        assert "ai_news" in text

    def test_empty_report_returns_no_data_message(self, db_session):
        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        text = report.format_for_writer()
        assert "no performance data" in text.lower()

    def test_format_includes_metrics(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {
                    "topic_category": "biohacker",
                    "total_posts": 10,
                    "avg_likes": 45.5,
                    "avg_comments": 12.0,
                    "avg_engagement_rate": 0.05,
                },
            ],
        )

        learner = SelfLearner(db_session)
        report = learner.generate_performance_report()
        text = report.format_for_writer()
        assert "46" in text  # avg likes (45.5 rounds to 46)
        assert "12" in text  # avg comments


# ---------------------------------------------------------------------------
# Best posting hour suggestion
# ---------------------------------------------------------------------------


class TestBestPostingHour:
    def test_returns_best_hour_when_data_exists(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {"topic_category": "ai_news", "total_posts": 5, "best_posting_hour": 17},
            ],
        )

        learner = SelfLearner(db_session)
        hour = learner.get_best_posting_hour("ai_news")
        assert hour == 17

    def test_returns_none_when_no_data(self, db_session):
        learner = SelfLearner(db_session)
        hour = learner.get_best_posting_hour("nonexistent_category")
        assert hour is None

    def test_returns_none_when_hour_not_set(self, db_session):
        _seed_topic_performance(
            db_session,
            [
                {"topic_category": "de_work", "total_posts": 2, "best_posting_hour": None},
            ],
        )

        learner = SelfLearner(db_session)
        hour = learner.get_best_posting_hour("de_work")
        assert hour is None
