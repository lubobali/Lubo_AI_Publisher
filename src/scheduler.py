"""Daily pipeline scheduler — orchestrates full post generation flow."""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from src.duplicate_checker import DuplicateChecker
from src.git_insights import GitInsights
from src.image_generator import generate_image
from src.knowledge_base import KnowledgeBase
from src.models import PublisherDestination, PublisherPost
from src.observability import get_client, observe
from src.post_processor import process_post, validate_post
from src.publisher import get_publisher
from src.scraper import ScrapedArticle, scrape_topic
from src.screenshotter import take_git_screenshot, take_screenshot, take_wakatime_screenshot
from src.self_learner import SelfLearner
from src.topic_rotator import get_todays_topic
from src.wakatime_insights import WakaTimeInsights, build_screenshot_fields
from src.writer import WriterResult, write_post

logger = logging.getLogger(__name__)

# Only technical posts get book-knowledge grounding (Phase 2.8 decision).
GROUNDED_CATEGORIES = {"tech_talk", "my_agent_git", "ai_news"}


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
        self._git_insights: GitInsights | None = None
        self._wakatime: WakaTimeInsights | None = None

    @observe()
    async def generate_post(self, target_date: date) -> PipelineResult:
        """Run the full pipeline: topic → scrape → dedup → write → screenshot → save.

        Saves post as PENDING (not auto-published). Returns PipelineResult.
        """
        # 1. Pick today's topic
        topic = get_todays_topic(target_date)
        category = topic["sources_key"]
        logger.info("Topic for %s: %s (%s)", target_date, topic["name"], category)

        # 2. Get content — git insights, wakatime stats, or web scraper depending on category
        extra_articles: list[ScrapedArticle] = []
        if category == "my_agent_git":
            selected_article = self._get_git_article()
            if selected_article is None:
                return PipelineResult(success=False, error="No meaningful commits found in staging git log")
            # Enrich the build-log post with this week's real coding stats (Phase 2.75 "both")
            waka_article = WakaTimeInsights().get_weekly_stats()
            if waka_article is not None:
                extra_articles.append(waka_article)
        elif category == "wakatime":
            selected_article = self._get_wakatime_article()
            if selected_article is None:
                return PipelineResult(success=False, error="No WakaTime archives found for weekly stats")
        else:
            articles = await scrape_topic(category)
            if not articles:
                return PipelineResult(success=False, error=f"No articles found for {category}")

            # 3. Find first non-duplicate article
            checker = DuplicateChecker(self.session)
            selected_article = await self._find_non_duplicate(checker, articles, category)

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

        # 4.5. RAG — book concepts for technical categories only (invisible background)
        book_concepts = self._get_book_concepts(category, topic, selected_article)

        # 5. Write post
        writer_result: WriterResult | None = await write_post(
            topic_name=topic["name"],
            topic_description=topic.get("description", ""),
            articles=[selected_article, *extra_articles],
            performance_context=feedback,
            book_concepts=book_concepts,
        )

        if writer_result is None:
            return PipelineResult(success=False, error="Writer failed to generate post")

        # 5.5. Post-process + validate (inside trace for Langfuse scoring)
        writer_result.post_text, writer_result.hashtags = process_post(writer_result.post_text, writer_result.hashtags)
        ok, reason = validate_post(writer_result.post_text)
        if not ok:
            logger.warning("Post failed validation: %s", reason)

        # 6. Take screenshot — my_agent uses lubot.ai, everything else uses article URL
        image_path = None

        if category == "my_agent_git" and self._git_insights and self._git_insights.best_commit:
            bc = self._git_insights.best_commit
            screenshot = await take_git_screenshot(
                commit_message=bc.message,
                lines_added=bc.lines_added,
                lines_deleted=bc.lines_deleted,
                files_changed=bc.files_changed,
                changed_files=bc.changed_files,
                commit_hash=bc.hash,
                commit_date=bc.date.strftime("%B %d, %Y"),
            )
            if screenshot:
                image_path = screenshot.path
                logger.info("Git screenshot: %s", image_path)
        elif category == "my_agent":
            screenshot = await take_screenshot("https://staging.lubot.ai")
            if screenshot:
                image_path = screenshot.path
                logger.info("Screenshot from staging: %s", image_path)
        elif category == "wakatime":
            # Dashboard URL is login-walled — render our own stat card (never screenshot the URL).
            if self._wakatime and self._wakatime.weekly_stats:
                fields = build_screenshot_fields(self._wakatime.weekly_stats, self._wakatime.include_costs)
                screenshot = await take_wakatime_screenshot(**fields)
                if screenshot:
                    image_path = screenshot.path
                    logger.info("WakaTime stat-card screenshot: %s", image_path)
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

        # Store Langfuse trace ID for later scoring (e.g. human approval)
        try:
            trace_id = get_client().get_current_trace_id()
            if trace_id:
                post.langfuse_trace_id = trace_id
                self.session.flush()
        except Exception:
            logger.debug("Could not store Langfuse trace ID", exc_info=True)

        logger.info("Post saved as PENDING: #%d — %s", post.id, selected_article.title)
        return PipelineResult(success=True, post_id=post.id)

    def _get_git_article(self) -> ScrapedArticle | None:
        """Fetch latest feature from staging git log."""
        self._git_insights = GitInsights()
        return self._git_insights.get_latest_feature()

    def _get_wakatime_article(self) -> ScrapedArticle | None:
        """Fetch this week's coding stats from WakaTime archives on staging."""
        self._wakatime = WakaTimeInsights()
        return self._wakatime.get_weekly_stats()

    def _get_book_concepts(self, category: str, topic: dict, article: ScrapedArticle) -> list[str]:
        """Retrieve 2-3 book concepts to ground a technical post. Never fatal.

        Only the technical categories get grounding; everything else returns [].
        A KB/embedding failure logs and returns [] so it never breaks a post.
        """
        if category not in GROUNDED_CATEGORIES:
            return []
        try:
            query = f"{topic['name']} {article.title} {article.summary[:200]}"
            return [c.text for c in KnowledgeBase(self.session).search(query)]
        except Exception:
            logger.warning("Knowledge-base search failed for %s — skipping grounding", category, exc_info=True)
            return []

    async def _find_non_duplicate(
        self, checker: DuplicateChecker, articles: list[ScrapedArticle], category: str
    ) -> ScrapedArticle | None:
        """Find first non-duplicate article from a list. Scores source quality."""
        selected: ScrapedArticle | None = None
        duplicates_skipped = 0

        for article in articles:
            result = await checker.check_article(
                url=article.url,
                title=article.title,
                category=category,
                published_at=article.published_at,
            )
            if not result.is_duplicate:
                selected = article
                break
            duplicates_skipped += 1
            logger.info("Skipping duplicate: %s (%s)", article.title, result.reason)

        # Submit source_quality score
        try:
            total_scraped = len(articles)
            source_quality = 1.0 - (duplicates_skipped / total_scraped) if total_scraped > 0 else 0.0
            get_client().score_current_trace(
                name="source_quality",
                value=source_quality,
                data_type="NUMERIC",
                comment=f"{duplicates_skipped}/{total_scraped} duplicates for {category}",
            )
        except Exception:
            logger.debug("Langfuse source_quality scoring failed", exc_info=True)

        return selected


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
