"""FastAPI backend routes for LuBot Publisher dashboard."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db import SessionLocal
from src.models import PublisherPost, PublisherTopicPerformance
from src.observability import get_client

DASHBOARD_FILE = Path(__file__).parent.parent / "static" / "dashboard.html"

logger = logging.getLogger(__name__)

app = FastAPI(title="LuBot Publisher API", version="1.0.0")


def get_db_session():
    """Dependency: yield a DB session, close after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PostOut(BaseModel):
    id: int
    posted_at: datetime
    topic_category: str
    topic_title: str
    source_url: str | None = None
    post_text: str
    image_path: str | None = None
    hashtags: list[str] | None = None
    linkedin_post_urn: str | None = None
    status: str
    day_of_week: str | None = None
    posting_time_ct: str | None = None

    model_config = {"from_attributes": True}


class CreatePostRequest(BaseModel):
    topic_category: str
    topic_title: str
    post_text: str
    source_url: str | None = None
    image_path: str | None = None
    hashtags: list[str] | None = None


class TopicPerformanceOut(BaseModel):
    topic_category: str
    total_posts: int
    avg_likes: float
    avg_comments: float
    avg_impressions: float
    avg_engagement_rate: float
    best_posting_hour: int | None = None

    model_config = {"from_attributes": True}


class AnalyticsResponse(BaseModel):
    topic_performance: list[TopicPerformanceOut]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the single-page approval dashboard."""
    return HTMLResponse(DASHBOARD_FILE.read_text())


@app.get("/api/posts/{post_id}/image")
def post_image(post_id: int, session: Session = Depends(get_db_session)):
    """Serve the screenshot/image for a post so the dashboard can show it."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if not post or not post.image_path or not Path(post.image_path).exists():
        raise HTTPException(status_code=404, detail="No image for this post")
    return FileResponse(post.image_path)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/posts", response_model=list[PostOut])
def list_posts(
    status: str | None = None,
    category: str | None = None,
    session: Session = Depends(get_db_session),
):
    """List posts with optional filters."""
    query = session.query(PublisherPost).order_by(PublisherPost.posted_at.desc())
    if status:
        query = query.filter(PublisherPost.status == status)
    if category:
        query = query.filter(PublisherPost.topic_category == category)
    return query.all()


@app.get("/api/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int, session: Session = Depends(get_db_session)):
    """Get a single post by ID."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@app.post("/api/posts", response_model=PostOut, status_code=201)
def create_post(body: CreatePostRequest, session: Session = Depends(get_db_session)):
    """Create a manual post (status: pending)."""
    post = PublisherPost(
        posted_at=datetime.now(UTC),
        topic_category=body.topic_category,
        topic_title=body.topic_title,
        post_text=body.post_text,
        source_url=body.source_url,
        image_path=body.image_path,
        hashtags=body.hashtags,
        status="pending",
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def _score_human_approval(trace_id: str | None, value: float, comment: str) -> None:
    """Submit human_approval score to Langfuse for the pipeline trace."""
    if not trace_id:
        return
    try:
        get_client().create_score(
            trace_id=trace_id,
            name="human_approval",
            value=value,
            data_type="NUMERIC",
            comment=comment,
        )
    except Exception:
        logger.debug("Langfuse human_approval scoring failed", exc_info=True)


@app.post("/api/posts/{post_id}/approve", response_model=PostOut)
def approve_post(post_id: int, session: Session = Depends(get_db_session)):
    """Approve a pending post for publishing."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot approve post with status '{post.status}'")
    post.status = "approved"
    session.commit()
    session.refresh(post)
    _score_human_approval(post.langfuse_trace_id, 1.0, "approved")
    return post


@app.post("/api/posts/{post_id}/reject", response_model=PostOut)
def reject_post(post_id: int, session: Session = Depends(get_db_session)):
    """Reject a pending post."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot reject post with status '{post.status}'")
    post.status = "rejected"
    session.commit()
    session.refresh(post)
    _score_human_approval(post.langfuse_trace_id, 0.0, "rejected")
    return post


@app.get("/api/analytics", response_model=AnalyticsResponse)
def get_analytics(session: Session = Depends(get_db_session)):
    """Get engagement analytics and topic performance."""
    rows = session.query(PublisherTopicPerformance).all()
    return AnalyticsResponse(topic_performance=rows)
