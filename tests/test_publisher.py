"""Tests for publisher interface — multi-platform architecture."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherDestination, PublisherPost
from src.publisher import (
    LinkedInPublisher,
    Publisher,
    get_publisher,
    list_platforms,
)

# ---------------------------------------------------------------------------
# Database fixtures (same pattern as test_database.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# PublisherPost status column
# ---------------------------------------------------------------------------


class TestPostStatus:
    def test_status_column_exists(self, test_engine):
        inspector = inspect(test_engine)
        columns = [c["name"] for c in inspector.get_columns("publisher_posts")]
        assert "status" in columns

    def test_default_status_is_pending(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test Post",
            post_text="Some text here.",
        )
        db_session.add(post)
        db_session.flush()
        assert post.status == "pending"

    def test_status_can_be_approved(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Approved Post",
            post_text="Text.",
            status="approved",
        )
        db_session.add(post)
        db_session.flush()
        assert post.status == "approved"

    def test_status_can_be_rejected(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Rejected Post",
            post_text="Text.",
            status="rejected",
        )
        db_session.add(post)
        db_session.flush()
        assert post.status == "rejected"

    def test_status_can_be_published(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Published Post",
            post_text="Text.",
            status="published",
        )
        db_session.add(post)
        db_session.flush()
        assert post.status == "published"


# ---------------------------------------------------------------------------
# PublisherDestination table
# ---------------------------------------------------------------------------


class TestPublisherDestination:
    def test_table_exists(self, test_engine):
        inspector = inspect(test_engine)
        tables = inspector.get_table_names()
        assert "publisher_destinations" in tables

    def test_required_columns(self, test_engine):
        inspector = inspect(test_engine)
        columns = [c["name"] for c in inspector.get_columns("publisher_destinations")]
        assert "id" in columns
        assert "post_id" in columns
        assert "platform" in columns
        assert "platform_post_urn" in columns
        assert "status" in columns
        assert "published_at" in columns

    def test_create_destination(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Multi-platform Test",
            post_text="Text.",
        )
        db_session.add(post)
        db_session.flush()

        dest = PublisherDestination(
            post_id=post.id,
            platform="linkedin",
            status="pending",
        )
        db_session.add(dest)
        db_session.flush()

        assert dest.id is not None
        assert dest.platform == "linkedin"
        assert dest.status == "pending"
        assert dest.platform_post_urn is None

    def test_destination_relationship(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="big_tech",
            topic_title="Relationship Test",
            post_text="Text.",
        )
        db_session.add(post)
        db_session.flush()

        dest = PublisherDestination(
            post_id=post.id,
            platform="linkedin",
            status="published",
            platform_post_urn="urn:li:share:123456",
        )
        db_session.add(dest)
        db_session.flush()

        assert dest.post.id == post.id
        assert len(post.destinations) == 1
        assert post.destinations[0].platform == "linkedin"

    def test_multiple_destinations_per_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Multi Dest Test",
            post_text="Text.",
        )
        db_session.add(post)
        db_session.flush()

        for platform in ["linkedin", "x", "instagram"]:
            dest = PublisherDestination(
                post_id=post.id,
                platform=platform,
                status="pending",
            )
            db_session.add(dest)
        db_session.flush()

        assert len(post.destinations) == 3
        platforms = [d.platform for d in post.destinations]
        assert "linkedin" in platforms
        assert "x" in platforms
        assert "instagram" in platforms


# ---------------------------------------------------------------------------
# Publisher base class
# ---------------------------------------------------------------------------


class TestPublisherBase:
    def test_publisher_is_abstract(self):
        with pytest.raises(TypeError):
            Publisher()  # Cannot instantiate abstract class

    def test_linkedin_publisher_is_publisher(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )
        assert isinstance(pub, Publisher)


# ---------------------------------------------------------------------------
# LinkedInPublisher
# ---------------------------------------------------------------------------


class TestLinkedInPublisher:
    def test_platform_name(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )
        assert pub.platform_name == "linkedin"

    @pytest.mark.asyncio
    async def test_publish_text(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )

        with patch(
            "src.publisher.create_text_post",
            new_callable=AsyncMock,
            return_value="urn:li:share:99999",
        ) as mock_post:
            urn = await pub.publish_text("Hello LinkedIn!")
            assert urn == "urn:li:share:99999"
            mock_post.assert_called_once_with(
                access_token="fake-token",
                person_urn="urn:li:person:abc123",
                text="Hello LinkedIn!",
            )

    @pytest.mark.asyncio
    async def test_publish_image(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )

        with (
            patch(
                "src.publisher.initialize_image_upload",
                new_callable=AsyncMock,
                return_value=("https://upload.url", "urn:li:image:111"),
            ),
            patch(
                "src.publisher.upload_image",
                new_callable=AsyncMock,
            ),
            patch(
                "src.publisher.create_image_post",
                new_callable=AsyncMock,
                return_value="urn:li:share:88888",
            ),
        ):
            urn = await pub.publish_image("Post with pic", b"fake-png")
            assert urn == "urn:li:share:88888"

    @pytest.mark.asyncio
    async def test_publish_image_calls_all_three_steps(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )

        with (
            patch(
                "src.publisher.initialize_image_upload",
                new_callable=AsyncMock,
                return_value=("https://upload.url", "urn:li:image:111"),
            ) as mock_init,
            patch(
                "src.publisher.upload_image",
                new_callable=AsyncMock,
            ) as mock_upload,
            patch(
                "src.publisher.create_image_post",
                new_callable=AsyncMock,
                return_value="urn:li:share:88888",
            ) as mock_create,
        ):
            await pub.publish_image("Text", b"image-data")

            mock_init.assert_called_once()
            mock_upload.assert_called_once()
            mock_create.assert_called_once()

    def test_get_post_url(self):
        pub = LinkedInPublisher(
            access_token="fake-token",
            person_urn="urn:li:person:abc123",
        )
        url = pub.get_post_url("urn:li:share:99999")
        assert "linkedin.com" in url
        assert "99999" in url


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------


class TestPlatformRegistry:
    def test_list_platforms(self):
        platforms = list_platforms()
        assert "linkedin" in platforms

    def test_get_linkedin_publisher(self):
        pub = get_publisher(
            "linkedin",
            access_token="fake",
            person_urn="urn:li:person:abc",
        )
        assert isinstance(pub, LinkedInPublisher)

    def test_get_unknown_platform_returns_none(self):
        pub = get_publisher("tiktok", access_token="fake")
        assert pub is None
