"""FastAPI backend routes for LuBot Publisher dashboard."""

import io
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db import SessionLocal
from src.models import PublisherPost, PublisherTopicPerformance
from src.observability import get_client
from src.screenshotter import SCREENSHOT_DIR

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB cap on uploaded images

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
    extra_image_count: int = 0
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
    """Serve the primary image (the card) for a post so the dashboard can show it."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if not post or not post.image_path or not Path(post.image_path).exists():
        raise HTTPException(status_code=404, detail="No image for this post")
    return FileResponse(post.image_path)


@app.get("/api/posts/{post_id}/image/{idx}")
def post_image_n(post_id: int, idx: int, session: Session = Depends(get_db_session)):
    """Serve the idx-th image of a post: 0 = the card, 1+ = extra images (e.g. a real photo)."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    paths = [post.image_path, *(post.extra_image_paths or [])] if post else []
    if not (0 <= idx < len(paths)) or not paths[idx] or not Path(paths[idx]).exists():
        raise HTTPException(status_code=404, detail="No such image for this post")
    return FileResponse(paths[idx])


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


class EditPostRequest(BaseModel):
    post_text: str | None = None
    hashtags: list[str] | None = None


def _require_pending(post: PublisherPost | None) -> PublisherPost:
    """Only PENDING posts are editable (never touch approved/published/rejected)."""
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != "pending":
        raise HTTPException(status_code=409, detail=f"Can only edit a pending post (this one is '{post.status}')")
    return post


def _images(post: PublisherPost) -> list[str]:
    """Ordered image paths: [card, *extras], dropping any empties."""
    return [p for p in [post.image_path, *(post.extra_image_paths or [])] if p]


def _set_images(post: PublisherPost, images: list[str]) -> None:
    """Write an ordered image list back to the post (first = card, rest = extras)."""
    post.image_path = images[0] if images else None
    post.extra_image_paths = images[1:] or None


@app.patch("/api/posts/{post_id}", response_model=PostOut)
def edit_post(post_id: int, body: EditPostRequest, session: Session = Depends(get_db_session)):
    """Edit a PENDING post's text and/or hashtags before approving. Pending-only."""
    post = _require_pending(session.query(PublisherPost).filter_by(id=post_id).first())
    if body.post_text is not None:
        text = body.post_text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="post_text cannot be empty")
        post.post_text = text
    if body.hashtags is not None:
        post.hashtags = body.hashtags
    session.commit()
    session.refresh(post)
    return post


@app.post("/api/posts/{post_id}/images", response_model=PostOut)
async def add_post_image(post_id: int, file: UploadFile = File(...), session: Session = Depends(get_db_session)):
    """Add an image to a PENDING post (e.g. a real photo alongside the card). EXIF-oriented,
    downsized, re-encoded to a safe filename in the shared screenshots volume. Pending-only."""
    post = _require_pending(session.query(PublisherPost).filter_by(id=post_id).first())
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    from PIL import Image, ImageOps  # lazy — keeps the API importable without Pillow

    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not read that image") from e
    img.thumbnail((2000, 2000))  # cap the long edge; keep it lean
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if has_alpha:
        path = SCREENSHOT_DIR / f"upload-{post_id}-{uuid.uuid4().hex[:8]}.png"
        img.save(path, "PNG")
    else:
        path = SCREENSHOT_DIR / f"upload-{post_id}-{uuid.uuid4().hex[:8]}.jpg"
        img.convert("RGB").save(path, "JPEG", quality=88)

    _set_images(post, [*_images(post), str(path)])
    session.commit()
    session.refresh(post)
    return post


@app.delete("/api/posts/{post_id}/images/{idx}", response_model=PostOut)
def remove_post_image(post_id: int, idx: int, session: Session = Depends(get_db_session)):
    """Remove the idx-th image (0 = card, 1+ = extras) from a PENDING post. Pending-only."""
    post = _require_pending(session.query(PublisherPost).filter_by(id=post_id).first())
    images = _images(post)
    if not (0 <= idx < len(images)):
        raise HTTPException(status_code=404, detail="No such image on this post")
    images.pop(idx)
    _set_images(post, images)
    session.commit()
    session.refresh(post)
    return post


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


class GenerateCarouselRequest(BaseModel):
    category: str | None = None  # a sources_key (e.g. "ai_news"); None = today's rotation topic


@app.post("/api/carousels", status_code=202)
def generate_carousel(body: GenerateCarouselRequest, background: BackgroundTasks):
    """Kick off a swipeable CAROUSEL generation (Phase 2.21) for a topic, in the BACKGROUND.

    Generation is slow (LLM + Playwright renders ~7 slides), so we return 202 immediately and
    do the work in a background task; the new carousel shows up on the dashboard as PENDING in
    ~1 minute. `category` picks a specific topic (sources_key) or defaults to today's rotation."""
    # Lazy import — keeps the heavy pipeline (Playwright, RAG, models) out of API import time.
    from src.cron import generate_carousel_now

    background.add_task(generate_carousel_now, body.category)
    return {"status": "generating", "category": body.category or "today"}


@app.get("/api/analytics", response_model=AnalyticsResponse)
def get_analytics(session: Session = Depends(get_db_session)):
    """Get engagement analytics and topic performance."""
    rows = session.query(PublisherTopicPerformance).all()
    return AnalyticsResponse(topic_performance=rows)
