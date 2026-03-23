"""Analytics worker — fetches engagement metrics and recalculates topic performance."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.linkedin_client import LINKEDIN_API_BASE, get_auth_headers
from src.models import PublisherAnalytics, PublisherPost, PublisherTopicPerformance

logger = logging.getLogger(__name__)


def calculate_engagement_rate(
    likes: int,
    comments: int,
    shares: int,
    impressions: int,
) -> float:
    """Calculate engagement rate: (likes + comments + shares) / impressions."""
    if impressions == 0:
        return 0.0
    return (likes + comments + shares) / impressions


class AnalyticsWorker:
    """Fetches engagement metrics from LinkedIn and stores in DB."""

    def __init__(self, access_token: str, session: Session | None = None):
        self.access_token = access_token
        self.session = session

    async def fetch_post_metrics(self, post_urn: str) -> dict | None:
        """Fetch engagement metrics for a single post from LinkedIn API.

        Returns dict with likes, comments, shares, impressions, clicks.
        Returns None on failure.
        """
        try:
            response = await self._api_get(
                f"{LINKEDIN_API_BASE}/rest/socialActions/{post_urn}",
            )
            data = response.json()

            likes = data.get("likes", {}).get("paging", {}).get("total", 0)
            comments = data.get("comments", {}).get("paging", {}).get("total", 0)

            return {
                "likes": likes,
                "comments": comments,
                "shares": 0,
                "impressions": 0,
                "clicks": 0,
            }
        except Exception as e:
            logger.warning("Failed to fetch metrics for %s: %s", post_urn, e)
            return None

    async def _api_get(self, url: str) -> httpx.Response:
        """Make an authenticated GET request to LinkedIn API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=get_auth_headers(self.access_token),
            )
            response.raise_for_status()
            return response

    def store_metrics(
        self,
        post_id: int,
        post_urn: str,
        metrics: dict,
    ) -> None:
        """Store engagement metrics in publisher_analytics table."""
        likes = metrics.get("likes", 0)
        comments = metrics.get("comments", 0)
        shares = metrics.get("shares", 0)
        impressions = metrics.get("impressions", 0)
        clicks = metrics.get("clicks", 0)

        rate = calculate_engagement_rate(likes, comments, shares, impressions)

        record = PublisherAnalytics(
            post_id=post_id,
            linkedin_post_urn=post_urn,
            checked_at=datetime.now(UTC),
            likes=likes,
            comments=comments,
            shares=shares,
            impressions=impressions,
            clicks=clicks,
            engagement_rate=rate,
        )
        self.session.add(record)
        self.session.flush()

    def get_posts_for_update(self, days: int = 7) -> list[PublisherPost]:
        """Get published posts from last N days that have LinkedIn URNs."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return (
            self.session.query(PublisherPost)
            .filter(
                PublisherPost.posted_at >= cutoff,
                PublisherPost.status == "published",
                PublisherPost.linkedin_post_urn.isnot(None),
            )
            .all()
        )


def recalculate_topic_performance(session: Session) -> None:
    """Recalculate publisher_topic_performance from latest analytics data.

    For each category, takes the most recent analytics record per post
    and computes averages.
    """
    # Get latest analytics per post (subquery for max checked_at per post_id)
    latest_subq = (
        session.query(
            PublisherAnalytics.post_id,
            func.max(PublisherAnalytics.checked_at).label("max_checked"),
        )
        .group_by(PublisherAnalytics.post_id)
        .subquery()
    )

    # Join to get full analytics rows + post category
    rows = (
        session.query(
            PublisherPost.topic_category,
            func.count(PublisherPost.id).label("total"),
            func.avg(PublisherAnalytics.likes).label("avg_likes"),
            func.avg(PublisherAnalytics.comments).label("avg_comments"),
            func.avg(PublisherAnalytics.impressions).label("avg_impressions"),
            func.avg(PublisherAnalytics.engagement_rate).label("avg_engagement"),
        )
        .join(PublisherAnalytics, PublisherPost.id == PublisherAnalytics.post_id)
        .join(
            latest_subq,
            (PublisherAnalytics.post_id == latest_subq.c.post_id)
            & (PublisherAnalytics.checked_at == latest_subq.c.max_checked),
        )
        .group_by(PublisherPost.topic_category)
        .all()
    )

    for row in rows:
        existing = session.query(PublisherTopicPerformance).filter_by(topic_category=row.topic_category).first()
        if existing:
            existing.total_posts = row.total
            existing.avg_likes = float(row.avg_likes or 0)
            existing.avg_comments = float(row.avg_comments or 0)
            existing.avg_impressions = float(row.avg_impressions or 0)
            existing.avg_engagement_rate = float(row.avg_engagement or 0)
            existing.updated_at = datetime.now(UTC)
        else:
            session.add(
                PublisherTopicPerformance(
                    topic_category=row.topic_category,
                    total_posts=row.total,
                    avg_likes=float(row.avg_likes or 0),
                    avg_comments=float(row.avg_comments or 0),
                    avg_impressions=float(row.avg_impressions or 0),
                    avg_engagement_rate=float(row.avg_engagement or 0),
                )
            )
    session.flush()
