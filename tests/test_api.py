"""Tests for FastAPI backend routes — post management, approval, analytics."""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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

    def test_extra_image_count_exposed(self, client, db_session):
        _create_post(db_session, image_path="/tmp/card.png", extra_image_paths=["/tmp/photo.jpg"])
        resp = client.get("/api/posts")
        assert resp.json()[0]["extra_image_count"] == 1

    def test_extra_image_served(self, client, db_session, tmp_path):
        card = tmp_path / "card.png"
        photo = tmp_path / "photo.jpg"
        card.write_bytes(b"\x89PNG card")
        photo.write_bytes(b"\xff\xd8 photo")
        p = _create_post(db_session, image_path=str(card), extra_image_paths=[str(photo)])
        # idx 0 = card, idx 1 = the extra photo, idx 2 = 404
        assert client.get(f"/api/posts/{p.id}/image/0").status_code == 200
        assert client.get(f"/api/posts/{p.id}/image/1").content == b"\xff\xd8 photo"
        assert client.get(f"/api/posts/{p.id}/image/2").status_code == 404

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


class TestDashboard:
    def test_serves_dashboard_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "LuBot Publisher" in r.text

    def test_post_image_404_when_no_image(self, client, db_session):
        post = _create_post(db_session, image_path=None)
        r = client.get(f"/api/posts/{post.id}/image")
        assert r.status_code == 404

    def test_post_image_404_when_post_missing(self, client):
        assert client.get("/api/posts/999999/image").status_code == 404

    def test_post_image_served_when_present(self, client, db_session, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 200)
        post = _create_post(db_session, image_path=str(img))
        r = client.get(f"/api/posts/{post.id}/image")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image")


# ---------------------------------------------------------------------------
# Edit-before-approve: text edit + image add/remove (pending-only)
# ---------------------------------------------------------------------------


def _png_bytes(size=(12, 12), color=(20, 120, 240)) -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


class TestEditBeforeApprove:
    def test_edit_text_and_hashtags_on_pending(self, client, db_session):
        p = _create_post(db_session, status="pending", post_text="old")
        r = client.patch(f"/api/posts/{p.id}", json={"post_text": "new take @Zach", "hashtags": ["#AI"]})
        assert r.status_code == 200 and r.json()["post_text"] == "new take @Zach"
        db_session.refresh(p)
        assert p.post_text == "new take @Zach" and p.hashtags == ["#AI"]

    def test_edit_rejected_when_not_pending(self, client, db_session):
        p = _create_post(db_session, status="published", post_text="live")
        r = client.patch(f"/api/posts/{p.id}", json={"post_text": "nope"})
        assert r.status_code == 409
        db_session.refresh(p)
        assert p.post_text == "live"  # unchanged

    def test_edit_empty_text_rejected(self, client, db_session):
        p = _create_post(db_session, status="pending", post_text="old")
        assert client.patch(f"/api/posts/{p.id}", json={"post_text": "   "}).status_code == 400

    def test_add_first_image_becomes_card(self, client, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.SCREENSHOT_DIR", tmp_path)
        p = _create_post(db_session, status="pending", image_path=None)
        r = client.post(f"/api/posts/{p.id}/images", files={"file": ("photo.png", _png_bytes(), "image/png")})
        assert r.status_code == 200
        db_session.refresh(p)
        assert p.image_path and p.image_path.endswith(".jpg")  # RGB -> re-encoded jpg
        assert Path(p.image_path).exists()

    def test_add_second_image_appends_as_extra(self, client, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.SCREENSHOT_DIR", tmp_path)
        p = _create_post(db_session, status="pending", image_path="/tmp/card.png")
        r = client.post(f"/api/posts/{p.id}/images", files={"file": ("photo.png", _png_bytes(), "image/png")})
        assert r.status_code == 200
        db_session.refresh(p)
        assert p.image_path == "/tmp/card.png" and p.extra_image_count == 1

    def test_add_rejects_non_image(self, client, db_session):
        p = _create_post(db_session, status="pending")
        r = client.post(f"/api/posts/{p.id}/images", files={"file": ("x.txt", b"hi", "text/plain")})
        assert r.status_code == 400

    def test_add_rejects_non_pending(self, client, db_session):
        p = _create_post(db_session, status="published")
        r = client.post(f"/api/posts/{p.id}/images", files={"file": ("x.png", _png_bytes(), "image/png")})
        assert r.status_code == 409

    def test_remove_extra_image(self, client, db_session):
        p = _create_post(db_session, status="pending", image_path="/tmp/card.png", extra_image_paths=["/tmp/photo.jpg"])
        assert client.delete(f"/api/posts/{p.id}/images/1").status_code == 200
        db_session.refresh(p)
        assert p.image_path == "/tmp/card.png" and not p.extra_image_paths

    def test_remove_card_promotes_next(self, client, db_session):
        p = _create_post(db_session, status="pending", image_path="/tmp/card.png", extra_image_paths=["/tmp/photo.jpg"])
        assert client.delete(f"/api/posts/{p.id}/images/0").status_code == 200
        db_session.refresh(p)
        assert p.image_path == "/tmp/photo.jpg" and not p.extra_image_paths

    def test_remove_bad_index_404(self, client, db_session):
        p = _create_post(db_session, status="pending", image_path="/tmp/card.png")
        assert client.delete(f"/api/posts/{p.id}/images/5").status_code == 404


class TestGenerateCarousel:
    """POST /api/carousels kicks off background carousel generation (Phase 2.21)."""

    def test_returns_202_and_schedules_background_task(self, client):
        with patch("src.cron.generate_carousel_now") as mock_gen:
            r = client.post("/api/carousels", json={"category": "ai_news"})
        assert r.status_code == 202
        assert r.json()["category"] == "ai_news"
        mock_gen.assert_called_once_with("ai_news")

    def test_defaults_to_today_when_no_category(self, client):
        with patch("src.cron.generate_carousel_now") as mock_gen:
            r = client.post("/api/carousels", json={})
        assert r.status_code == 202
        assert r.json()["category"] == "today"
        mock_gen.assert_called_once_with(None)


class TestConvertToCarousel:
    """POST /api/posts/{id}/convert-to-carousel reshapes a pending single post (Phase 2.25)."""

    def test_converts_pending_single_post(self, client, db_session):
        p = _create_post(db_session, status="pending", image_path="/tmp/card.png")
        with patch("src.cron.convert_post_to_carousel") as mock_conv:
            r = client.post(f"/api/posts/{p.id}/convert-to-carousel")
        assert r.status_code == 202
        assert r.json()["post_id"] == p.id
        mock_conv.assert_called_once_with(p.id)

    def test_rejects_non_pending(self, client, db_session):
        p = _create_post(db_session, status="published")
        assert client.post(f"/api/posts/{p.id}/convert-to-carousel").status_code == 409

    def test_rejects_already_a_carousel(self, client, db_session):
        p = _create_post(db_session, status="pending", image_path="/tmp/a.png", extra_image_paths=["/tmp/b.png"])
        assert client.post(f"/api/posts/{p.id}/convert-to-carousel").status_code == 400

    def test_404_when_missing(self, client):
        assert client.post("/api/posts/999999/convert-to-carousel").status_code == 404
