"""Tests for FastAPI backend routes — post management, approval, analytics."""

import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api import app, get_db_session
from src.models import Base, PublisherPost, PublisherTopicPerformance

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")

test_engine = create_engine(TEST_DB_URL)
TestSession = sessionmaker(bind=test_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


@pytest.fixture()
def db_session():
    session = TestSession()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def client(db_session):
    """FastAPI test client with overridden DB session."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _create_post(db_session, **kwargs):
    defaults = {
        "posted_at": datetime.now(UTC),
        "topic_category": "ai_news",
        "topic_title": "Test Post",
        "post_text": "Some post text",
        "status": "pending",
    }
    defaults.update(kwargs)
    post = PublisherPost(**defaults)
    db_session.add(post)
    db_session.flush()
    return post


# ---------------------------------------------------------------------------
# GET /api/posts — list posts
# ---------------------------------------------------------------------------


class TestListPosts:
    def test_list_posts_empty(self, client):
        resp = client.get("/api/posts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_posts_returns_posts(self, client, db_session):
        _create_post(db_session)
        resp = client.get("/api/posts")
        assert resp.status_code == 200
        posts = resp.json()
        assert len(posts) >= 1
        assert "topic_category" in posts[0]

    def test_filter_by_status(self, client, db_session):
        _create_post(db_session, status="pending")
        _create_post(db_session, status="published")
        resp = client.get("/api/posts?status=pending")
        assert resp.status_code == 200
        posts = resp.json()
        assert all(p["status"] == "pending" for p in posts)

    def test_filter_by_category(self, client, db_session):
        _create_post(db_session, topic_category="ai_news")
        _create_post(db_session, topic_category="biohacker")
        resp = client.get("/api/posts?category=biohacker")
        assert resp.status_code == 200
        posts = resp.json()
        assert all(p["topic_category"] == "biohacker" for p in posts)


# ---------------------------------------------------------------------------
# GET /api/posts/{id} — get single post
# ---------------------------------------------------------------------------


class TestGetPost:
    def test_get_post(self, client, db_session):
        post = _create_post(db_session)
        resp = client.get(f"/api/posts/{post.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == post.id

    def test_get_nonexistent_post(self, client):
        resp = client.get("/api/posts/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/posts/{id}/approve
# ---------------------------------------------------------------------------


class TestApprovePost:
    def test_approve_pending_post(self, client, db_session):
        post = _create_post(db_session, status="pending")
        resp = client.post(f"/api/posts/{post.id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_approve_nonexistent_post(self, client):
        resp = client.post("/api/posts/99999/approve")
        assert resp.status_code == 404

    def test_approve_rejected_post_fails(self, client, db_session):
        post = _create_post(db_session, status="rejected")
        resp = client.post(f"/api/posts/{post.id}/approve")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/posts/{id}/reject
# ---------------------------------------------------------------------------


class TestRejectPost:
    def test_reject_pending_post(self, client, db_session):
        post = _create_post(db_session, status="pending")
        resp = client.post(f"/api/posts/{post.id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_reject_nonexistent_post(self, client):
        resp = client.post("/api/posts/99999/reject")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/posts — create manual post
# ---------------------------------------------------------------------------


class TestCreatePost:
    def test_create_manual_post(self, client):
        resp = client.post(
            "/api/posts",
            json={
                "topic_category": "tech_talk",
                "topic_title": "My manual post",
                "post_text": "Hand-written post about something cool",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["topic_category"] == "tech_talk"

    def test_create_post_missing_text(self, client):
        resp = client.post(
            "/api/posts",
            json={
                "topic_category": "tech_talk",
                "topic_title": "Title only",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/analytics — engagement metrics
# ---------------------------------------------------------------------------


class TestAnalytics:
    def test_analytics_empty(self, client):
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert "topic_performance" in data

    def test_analytics_with_data(self, client, db_session):
        db_session.add(
            PublisherTopicPerformance(
                topic_category="ai_news",
                total_posts=10,
                avg_likes=25.0,
                avg_comments=5.0,
                avg_engagement_rate=0.03,
            )
        )
        db_session.flush()

        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["topic_performance"]) >= 1
        assert data["topic_performance"][0]["topic_category"] == "ai_news"


# ---------------------------------------------------------------------------
# GET /api/health — health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_check(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
