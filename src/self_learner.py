"""Self-learning engine — generates performance reports and content adjustments."""

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.models import PublisherTopicPerformance

logger = logging.getLogger(__name__)


@dataclass
class TopicInsight:
    """Performance insight for a single topic category."""

    category: str
    avg_likes: float
    avg_comments: float
    avg_engagement_rate: float
    total_posts: int
    trend: str = "stable"  # "up", "down", "stable"


@dataclass
class PerformanceReport:
    """Full performance report across all categories."""

    insights: list[TopicInsight] = field(default_factory=list)
    best_category: str | None = None
    worst_category: str | None = None

    def format_for_writer(self) -> str:
        """Format report as context for the AI writer prompt."""
        if not self.insights:
            return "No performance data yet — write naturally, analytics will guide future posts."

        lines = ["Your top topics (last 30 days):"]
        for i, insight in enumerate(self.insights, 1):
            trend_arrow = {"up": "trending UP", "down": "trending DOWN", "stable": "stable"}
            lines.append(
                f"  {i}. {insight.category} — avg {insight.avg_likes:.0f} likes, "
                f"{insight.avg_comments:.0f} comments ({trend_arrow[insight.trend]})"
            )

        if self.best_category:
            lines.append(f"Best performer: {self.best_category}")
        if self.worst_category:
            lines.append(f"Needs improvement: {self.worst_category} — try a different angle")

        return "\n".join(lines)


class SelfLearner:
    """Generates performance reports and suggests content adjustments."""

    def __init__(self, session: Session):
        self.session = session

    def generate_performance_report(self) -> PerformanceReport:
        """Generate a performance report from topic performance data.

        Returns insights ranked by engagement rate (highest first).
        """
        rows = self.session.query(PublisherTopicPerformance).filter(PublisherTopicPerformance.total_posts > 0).all()

        if not rows:
            return PerformanceReport()

        insights = []
        for row in rows:
            insight = TopicInsight(
                category=row.topic_category,
                avg_likes=row.avg_likes or 0,
                avg_comments=row.avg_comments or 0,
                avg_engagement_rate=row.avg_engagement_rate or 0,
                total_posts=row.total_posts,
                trend=self._determine_trend(row),
            )
            insights.append(insight)

        # Sort by engagement rate descending
        insights.sort(key=lambda x: x.avg_engagement_rate, reverse=True)

        best = insights[0].category if insights else None
        worst = insights[-1].category if len(insights) > 1 else None

        return PerformanceReport(
            insights=insights,
            best_category=best,
            worst_category=worst,
        )

    def _determine_trend(self, row: PublisherTopicPerformance) -> str:
        """Determine if a topic is trending up, down, or stable.

        Simple heuristic: compare engagement rate against the overall average.
        With more data, this could compare recent vs older performance.
        """
        if row.avg_engagement_rate is None or row.avg_engagement_rate == 0:
            return "stable"

        all_rows = (
            self.session.query(PublisherTopicPerformance)
            .filter(
                PublisherTopicPerformance.total_posts > 0,
            )
            .all()
        )

        rates = [r.avg_engagement_rate for r in all_rows if r.avg_engagement_rate]
        if not rates:
            return "stable"

        avg = sum(rates) / len(rates)
        if row.avg_engagement_rate > avg * 1.2:
            return "up"
        if row.avg_engagement_rate < avg * 0.8:
            return "down"
        return "stable"

    def get_best_posting_hour(self, category: str) -> int | None:
        """Get the best posting hour for a category, if data exists."""
        row = self.session.query(PublisherTopicPerformance).filter_by(topic_category=category).first()
        if row is None or row.best_posting_hour is None:
            return None
        return row.best_posting_hour
