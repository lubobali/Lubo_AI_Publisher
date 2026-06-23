"""SQLAlchemy models for LuBot Publisher — 7 tables."""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class PublisherPost(Base):
    __tablename__ = "publisher_posts"

    id = Column(Integer, primary_key=True)
    posted_at = Column(DateTime(timezone=True), nullable=False)
    topic_category = Column(String(50), nullable=False)
    topic_title = Column(String(500), nullable=False)
    source_url = Column(Text)
    post_text = Column(Text, nullable=False)
    image_path = Column(Text)
    hashtags = Column(ARRAY(String))
    linkedin_post_urn = Column(String(200))
    posting_time_ct = Column(String(10))
    day_of_week = Column(String(10))
    status = Column(String(20), nullable=False, default="pending")
    post_embedding = Column(JSON, nullable=True)
    langfuse_trace_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    analytics = relationship("PublisherAnalytics", back_populates="post")
    destinations = relationship("PublisherDestination", back_populates="post")


class PublisherAnalytics(Base):
    __tablename__ = "publisher_analytics"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("publisher_posts.id"))
    linkedin_post_urn = Column(String(200), nullable=False)
    checked_at = Column(DateTime(timezone=True), nullable=False)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)

    post = relationship("PublisherPost", back_populates="analytics")


class PublisherTopicPerformance(Base):
    __tablename__ = "publisher_topic_performance"

    topic_category = Column(String(50), primary_key=True)
    total_posts = Column(Integer, default=0)
    avg_likes = Column(Float, default=0)
    avg_comments = Column(Float, default=0)
    avg_impressions = Column(Float, default=0)
    avg_engagement_rate = Column(Float, default=0)
    best_post_id = Column(Integer)
    worst_post_id = Column(Integer)
    best_posting_hour = Column(Integer)
    updated_at = Column(DateTime, server_default=func.now())


class PublisherDestination(Base):
    __tablename__ = "publisher_destinations"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("publisher_posts.id"), nullable=False)
    platform = Column(String(50), nullable=False)
    platform_post_urn = Column(String(200))
    status = Column(String(20), nullable=False, default="pending")
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime, server_default=func.now())

    post = relationship("PublisherPost", back_populates="destinations")


class PublisherScrapedUrl(Base):
    __tablename__ = "publisher_scraped_urls"

    id = Column(Integer, primary_key=True)
    url = Column(Text, unique=True, nullable=False)
    scraped_at = Column(DateTime, server_default=func.now())
    used = Column(Boolean, default=False)


class PublisherKnowledgeBase(Base):
    """Book chunks + embeddings for RAG grounding (Phase 2.8)."""

    __tablename__ = "publisher_knowledge_base"

    id = Column(Integer, primary_key=True)
    book_title = Column(String(300), nullable=False)
    book_slug = Column(String(200), nullable=False, index=True)  # filename stem, for idempotent re-ingest
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    word_count = Column(Integer, nullable=False, default=0)
    embedding = Column(JSON, nullable=False)  # list[float], 2048-dim, L2-normalized
    created_at = Column(DateTime, server_default=func.now())


class PublisherPodcastTranscript(Base):
    """Cached podcast episode transcript + distilled bullets (Phase 2.10b).

    Keyed by episode guid so a given episode is transcribed (paid for) only once;
    `distilled` holds the P5.5 market-theme bullets, filled in after transcription.
    """

    __tablename__ = "publisher_podcast_transcripts"

    id = Column(Integer, primary_key=True)
    guid = Column(String(500), unique=True, nullable=False, index=True)  # cache key
    podcast_name = Column(String(200), nullable=False, default="")
    episode_title = Column(Text, nullable=False, default="")
    audio_url = Column(Text, nullable=False, default="")
    transcript = Column(Text, nullable=False)
    distilled = Column(Text, nullable=True)  # P5.5 market-theme bullets
    created_at = Column(DateTime, server_default=func.now())
