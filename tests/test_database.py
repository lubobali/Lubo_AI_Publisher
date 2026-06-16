"""Database tests — connection, table creation, CRUD operations.

These tests require a running PostgreSQL instance.
In CI, the PostgreSQL service container handles this.
Locally, run: docker compose up publisher-db -d
"""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherAnalytics, PublisherPost, PublisherScrapedUrl, PublisherTopicPerformance

# Test database URL — separate from production
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


class TestConnection:
    def test_database_connection(self, test_engine):
        """Verify we can connect and execute a query."""
        with test_engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    def test_database_version(self, test_engine):
        """Verify PostgreSQL is running."""
        with test_engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            assert "PostgreSQL" in version


class TestTableCreation:
    EXPECTED_TABLES = [
        "publisher_posts",
        "publisher_analytics",
        "publisher_topic_performance",
        "publisher_scraped_urls",
    ]

    def test_all_tables_exist(self, test_engine):
        """Verify all 4 tables are created."""
        inspector = inspect(test_engine)
        tables = inspector.get_table_names()
        for table in self.EXPECTED_TABLES:
            assert table in tables, f"Missing table: {table}"

    def test_publisher_posts_columns(self, test_engine):
        """Verify publisher_posts has correct columns."""
        inspector = inspect(test_engine)
        columns = {col["name"] for col in inspector.get_columns("publisher_posts")}
        expected = {
            "id",
            "posted_at",
            "topic_category",
            "topic_title",
            "source_url",
            "post_text",
            "image_path",
            "hashtags",
            "linkedin_post_urn",
            "posting_time_ct",
            "day_of_week",
            "langfuse_trace_id",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_publisher_analytics_columns(self, test_engine):
        """Verify publisher_analytics has correct columns."""
        inspector = inspect(test_engine)
        columns = {col["name"] for col in inspector.get_columns("publisher_analytics")}
        expected = {
            "id",
            "post_id",
            "linkedin_post_urn",
            "checked_at",
            "likes",
            "comments",
            "shares",
            "impressions",
            "clicks",
            "engagement_rate",
        }
        assert expected.issubset(columns)

    def test_publisher_topic_performance_columns(self, test_engine):
        """Verify publisher_topic_performance has correct columns."""
        inspector = inspect(test_engine)
        columns = {col["name"] for col in inspector.get_columns("publisher_topic_performance")}
        expected = {
            "topic_category",
            "total_posts",
            "avg_likes",
            "avg_comments",
            "avg_impressions",
            "avg_engagement_rate",
            "best_post_id",
            "worst_post_id",
            "best_posting_hour",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_publisher_scraped_urls_columns(self, test_engine):
        """Verify publisher_scraped_urls has correct columns."""
        inspector = inspect(test_engine)
        columns = {col["name"] for col in inspector.get_columns("publisher_scraped_urls")}
        expected = {"id", "url", "scraped_at", "used"}
        assert expected.issubset(columns)


class TestCRUD:
    def test_create_post(self, db_session):
        """Create a post and verify it persists."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="AI News",
            topic_title="GPT-5 released",
            post_text="Just tested the new model. The results were wild.",
            hashtags=["#AI", "#GPT5"],
            posting_time_ct="5:30 PM",
            day_of_week="monday",
        )
        db_session.add(post)
        db_session.flush()

        assert post.id is not None
        assert post.topic_category == "AI News"

    def test_read_post(self, db_session):
        """Create and read back a post."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="Tech Talk",
            topic_title="Built a new pipeline",
            post_text="3 months of work. 120K lines of code.",
        )
        db_session.add(post)
        db_session.flush()

        fetched = db_session.query(PublisherPost).filter_by(id=post.id).first()
        assert fetched is not None
        assert fetched.topic_title == "Built a new pipeline"

    def test_update_post(self, db_session):
        """Update a post's linkedin_post_urn after posting."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="Big Tech",
            topic_title="Google announcement",
            post_text="Google just did something interesting.",
        )
        db_session.add(post)
        db_session.flush()

        post.linkedin_post_urn = "urn:li:share:123456"
        db_session.flush()

        fetched = db_session.query(PublisherPost).filter_by(id=post.id).first()
        assert fetched.linkedin_post_urn == "urn:li:share:123456"

    def test_delete_post(self, db_session):
        """Delete a post."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="My Agent Build",
            topic_title="Airflow migration",
            post_text="Just finished the migration.",
        )
        db_session.add(post)
        db_session.flush()
        post_id = post.id

        db_session.delete(post)
        db_session.flush()

        fetched = db_session.query(PublisherPost).filter_by(id=post_id).first()
        assert fetched is None

    def test_create_analytics(self, db_session):
        """Create analytics linked to a post."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="AI News",
            topic_title="New model benchmark",
            post_text="The numbers are insane.",
            linkedin_post_urn="urn:li:share:789",
        )
        db_session.add(post)
        db_session.flush()

        analytics = PublisherAnalytics(
            post_id=post.id,
            linkedin_post_urn="urn:li:share:789",
            checked_at=datetime.now(UTC),
            likes=45,
            comments=12,
            shares=5,
            impressions=2000,
            engagement_rate=3.1,
        )
        db_session.add(analytics)
        db_session.flush()

        assert analytics.id is not None
        assert analytics.likes == 45
        assert analytics.post.topic_category == "AI News"

    def test_create_topic_performance(self, db_session):
        """Create a topic performance record."""
        perf = PublisherTopicPerformance(
            topic_category="Biohacker",
            total_posts=15,
            avg_likes=52.0,
            avg_comments=15.0,
            avg_impressions=3000.0,
            avg_engagement_rate=2.8,
        )
        db_session.add(perf)
        db_session.flush()

        fetched = db_session.query(PublisherTopicPerformance).filter_by(topic_category="Biohacker").first()
        assert fetched is not None
        assert fetched.avg_likes == 52.0

    def test_create_scraped_url(self, db_session):
        """Create a scraped URL record."""
        url = PublisherScrapedUrl(url="https://techcrunch.com/article-123")
        db_session.add(url)
        db_session.flush()

        assert url.id is not None
        assert url.used is False

    def test_scraped_url_unique_constraint(self, db_session):
        """Verify duplicate URLs are rejected."""
        url1 = PublisherScrapedUrl(url="https://example.com/unique-test")
        db_session.add(url1)
        db_session.flush()

        url2 = PublisherScrapedUrl(url="https://example.com/unique-test")
        db_session.add(url2)

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_post_analytics_relationship(self, db_session):
        """Verify post -> analytics relationship works."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="My Agent",
            topic_title="LuBot v2 showcase",
            post_text="Look at that output.",
            linkedin_post_urn="urn:li:share:456",
        )
        db_session.add(post)
        db_session.flush()

        for i in range(3):
            a = PublisherAnalytics(
                post_id=post.id,
                linkedin_post_urn="urn:li:share:456",
                checked_at=datetime.now(UTC),
                likes=10 * (i + 1),
            )
            db_session.add(a)
        db_session.flush()

        assert len(post.analytics) == 3

    def test_hashtags_array(self, db_session):
        """Verify PostgreSQL ARRAY type works for hashtags."""
        tags = ["#AI", "#DataEngineering", "#NVIDIA", "#FastAPI"]
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="Tech Talk",
            topic_title="Array test",
            post_text="Testing arrays.",
            hashtags=tags,
        )
        db_session.add(post)
        db_session.flush()

        fetched = db_session.query(PublisherPost).filter_by(id=post.id).first()
        assert fetched.hashtags == tags
        assert len(fetched.hashtags) == 4
