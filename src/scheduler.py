"""Daily pipeline scheduler — orchestrates full post generation flow."""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from src.duplicate_checker import DuplicateChecker
from src.image_generator import generate_image
from src.models import PublisherDestination, PublisherPost
from src.publisher import get_publisher
from src.scraper import ScrapedArticle, scrape_topic
from src.screenshotter import take_screenshot
from src.self_learner import SelfLearner
from src.topic_rotator import get_todays_topic
from src.writer import WriterResult, write_post

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    success: bool
    post_id: int | None = None
    error: str = ""


class Pipeline:
    """Orchestrates the daily post generation pipeline."""

    def __init__(self, session: Session):
        self.session = session

    async def generate_post(self, target_date: date) -> PipelineResult:
        """Run the full pipeline: topic → scrape → dedup → write → screenshot → save.

        Saves post as PENDING (not auto-published). Returns PipelineResult.
        """
        # 1. Pick today's topic
        topic = get_todays_topic(target_date)
        category = topic["sources_key"]
        logger.info("Topic for %s: %s (%s)", target_date, topic["name"], category)

        # 2. Scrape articles
        articles = await scrape_topic(category)
        if not articles:
            return PipelineResult(success=False, error=f"No articles found for {category}")

        # 3. Find first non-duplicate article
        checker = DuplicateChecker(self.session)
        selected_article: ScrapedArticle | None = None

        for article in articles:
            result = await checker.check_article(
                url=article.url,
                title=article.title,
                category=category,
                published_at=article.published_at,
            )
            if not result.is_duplicate:
                selected_article = article
                break
            logger.info("Skipping duplicate: %s (%s)", article.title, result.reason)

        if selected_article is None:
            return PipelineResult(
                success=False,
                error=f"All {len(articles)} articles are duplicates for {category}",
            )

        # Record URL as seen
        checker.record_url(selected_article.url, used=True)

        # 4. Get performance feedback for writer
        learner = SelfLearner(self.session)
        report = learner.generate_performance_report()
        feedback = report.format_for_writer()

        # 5. Write post
        writer_result: WriterResult | None = await write_post(
            topic_name=topic["name"],
            topic_description=topic.get("description", ""),
            articles=[selected_article],
            performance_context=feedback,
        )

        if writer_result is None:
            return PipelineResult(success=False, error="Writer failed to generate post")

        # 6. Take screenshot — my_agent uses lubot.ai, everything else uses article URL
        image_path = None

        if category == "my_agent":
            screenshot = await take_screenshot("https://lubot.ai")
            if screenshot:
                image_path = screenshot.path
                logger.info("Screenshot from lubot.ai: %s", image_path)
        elif selected_article.url:
            screenshot = await take_screenshot(selected_article.url)
            if screenshot:
                image_path = screenshot.path
                logger.info("Screenshot from article: %s", image_path)

        # Fall back to AI-generated image
        if not image_path:
            generated = await generate_image(category, selected_article.title, writer_result.post_text)
            if generated:
                image_path = generated.path
                logger.info("Generated image: %s", image_path)

        # 7. Save as PENDING
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category=category,
            topic_title=selected_article.title,
            source_url=selected_article.url,
            post_text=writer_result.post_text,
            image_path=image_path,
            hashtags=writer_result.hashtags,
            status="pending",
            day_of_week=target_date.strftime("%A").lower(),
        )
        self.session.add(post)
        self.session.flush()

        logger.info("Post saved as PENDING: #%d — %s", post.id, selected_article.title)
        return PipelineResult(success=True, post_id=post.id)


def approve_post(session: Session, post_id: int) -> bool:
    """Approve a pending post for publishing. Returns False if post not found or not pending."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if post is None or post.status != "pending":
        return False
    post.status = "approved"
    session.flush()
    return True


def reject_post(session: Session, post_id: int) -> bool:
    """Reject a pending post. Returns False if post not found or not pending."""
    post = session.query(PublisherPost).filter_by(id=post_id).first()
    if post is None or post.status != "pending":
        return False
    post.status = "rejected"
    session.flush()
    return True


async def publish_approved_posts(
    session: Session,
    access_token: str,
    platform: str = "linkedin",
) -> int:
    """Publish all approved posts. Returns count of published posts."""
    approved = session.query(PublisherPost).filter_by(status="approved").all()
    if not approved:
        return 0

    publisher = get_publisher(platform, access_token=access_token)
    published_count = 0

    for post in approved:
        try:
            # Publish with image if available
            if post.image_path:
                with open(post.image_path, "rb") as f:
                    image_data = f.read()
                post_urn = await publisher.publish_image(post.post_text, image_data)
            else:
                post_urn = await publisher.publish_text(post.post_text)

            post.status = "published"
            post.linkedin_post_urn = post_urn

            # Record destination
            dest = PublisherDestination(
                post_id=post.id,
                platform=platform,
                platform_post_urn=post_urn,
                status="published",
                published_at=datetime.now(UTC),
            )
            session.add(dest)
            session.flush()

            published_count += 1
            logger.info("Published post #%d to %s: %s", post.id, platform, post_urn)

        except Exception as e:
            logger.error("Failed to publish post #%d: %s", post.id, e)

    return published_count
