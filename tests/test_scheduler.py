"""Tests for daily pipeline scheduler — full flow from topic to pending post."""

import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherDestination, PublisherPost
from src.scheduler import Pipeline, _x_reply_link, approve_post, publish_approved_posts, reject_post
from src.scraper import ScrapedArticle
from src.writer import WriterResult

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")


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


@pytest.fixture(autouse=True)
def _default_topic():
    """Pin the daily topic to a NON-grounded scraper category (ai_gadgets) so
    pipeline-flow tests are deterministic AND never invoke RAG/embeddings. Tests
    exercising git/wakatime/my_agent/grounded categories patch get_todays_topic
    themselves inside their own `with`, which takes precedence over this default.
    """
    with patch(
        "src.scheduler.get_todays_topic",
        return_value={"name": "AI Gadgets", "sources_key": "ai_gadgets", "description": "test"},
    ):
        yield


@pytest.fixture(autouse=True)
def _devtrack_off_by_default():
    """Building in Public defaults to the WakaTime fallback in tests (no real DevTrack
    report read from /srv). The DevTrack-specific test overrides this within its own `with`."""
    with patch("src.scheduler.DevTrackInsights") as m:
        m.return_value.get_weekly_report.return_value = None
        yield m


def _make_articles():
    return [
        ScrapedArticle(
            title="New AI Chip Breaks Speed Records",
            url="https://example.com/ai-chip",
            summary="A new chip achieves 10x performance.",
            source="TechCrunch",
            published_at=datetime.now(UTC) - timedelta(hours=6),
        ),
        ScrapedArticle(
            title="Second Article About Something",
            url="https://example.com/second",
            summary="Another topic.",
            source="HackerNews",
            published_at=datetime.now(UTC) - timedelta(hours=12),
        ),
    ]


def _make_writer_result():
    return WriterResult(
        post_text="Just tested the new AI chip. 10x faster than last gen. Wild times.",
        screenshot_url="https://example.com/ai-chip",
        hashtags=["#AI", "#chips"],
    )


# ---------------------------------------------------------------------------
# Pipeline: generate post (saves as PENDING)
# ---------------------------------------------------------------------------


class TestPipelineGenerate:
    @pytest.mark.asyncio
    async def test_successful_pipeline_creates_pending_post(self, db_session):
        """Full pipeline: scrape → dedup → write → screenshot → save as pending."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/shot.png")
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = "No data yet"
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(
                target_date=date(2026, 3, 23),
            )

        assert result.success is True
        assert result.post_id is not None

        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post is not None
        assert post.status == "pending"
        assert post.post_text == writer_result.post_text

    @pytest.mark.asyncio
    async def test_stores_post_embedding_for_dedup(self, db_session):
        """The saved post must carry an embedding so future runs can skip same-idea posts."""
        articles = _make_articles()
        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup.get_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
            mock_dedup_cls.return_value = mock_dedup
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 16))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.post_embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_pipeline_skips_duplicate_articles(self, db_session):
        """Pipeline tries next article when first is a duplicate."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        duplicate_result = MagicMock(is_duplicate=True, reason="URL seen")
        not_duplicate = MagicMock(is_duplicate=False)

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/shot.png")
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            # First article is duplicate, second is not
            mock_dedup.check_article = AsyncMock(side_effect=[duplicate_result, not_duplicate])
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = "No data"
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        # Used the second article's URL
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.source_url == "https://example.com/second"

    @pytest.mark.asyncio
    async def test_pipeline_fails_when_no_articles(self, db_session):
        """Pipeline fails gracefully when scraper returns empty."""
        with patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=[]):
            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "no articles" in result.error.lower()

    @pytest.mark.asyncio
    async def test_pipeline_fails_when_all_duplicates(self, db_session):
        """Pipeline fails when all articles are duplicates."""
        articles = _make_articles()
        dup = MagicMock(is_duplicate=True, reason="duplicate")

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=dup)
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "duplicate" in result.error.lower()

    @pytest.mark.asyncio
    async def test_pipeline_handles_writer_failure(self, db_session):
        """Pipeline fails gracefully when writer returns None."""
        articles = _make_articles()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "writer" in result.error.lower()


# ---------------------------------------------------------------------------
# Approve / Reject workflow
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Screenshot fallback chain: AI URL → article URL → generate image
# ---------------------------------------------------------------------------


class TestScreenshotFallback:
    @pytest.mark.asyncio
    async def test_uses_article_url_for_screenshot(self, db_session):
        """Screenshot should use the real article URL, not AI-suggested URL."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_screenshot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            # Single call with article URL succeeds
            mock_screenshot.return_value = MagicMock(path="/tmp/fallback.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.image_path == "/tmp/fallback.png"

    @pytest.mark.asyncio
    async def test_falls_back_to_generated_image_when_all_screenshots_fail(self, db_session):
        """When both screenshot URLs fail, generate an AI image."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock) as mock_gen,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_gen.return_value = MagicMock(path="/tmp/generated.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.image_path == "/tmp/generated.png"
        mock_gen.assert_called_once()


class TestGitPipeline:
    """my_agent_git category uses GitInsights instead of web scraper."""

    @pytest.mark.asyncio
    async def test_my_agent_git_uses_git_insights(self, db_session):
        """Pipeline uses GitInsights for my_agent_git, not scrape_topic."""
        git_article = ScrapedArticle(
            title="Add stock fundamental analysis with 15 metrics",
            url="https://git.lubot.ai/lubot/services-agent-api",
            summary="Feature area: stock\nCommits: 3\nLines changed: +40/-5",
            source="git:lubot-staging-services-agent-api",
            published_at=None,
            source_priority=0,
        )
        writer_result = _make_writer_result()

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "My Agent Build", "sources_key": "my_agent_git", "description": "test"},
            ),
            patch.object(Pipeline, "_get_git_article", return_value=git_article) as mock_git,
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock) as mock_scrape,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        mock_git.assert_called_once()
        mock_scrape.assert_not_called()  # scraper NOT used

    @pytest.mark.asyncio
    async def test_my_agent_git_fails_gracefully(self, db_session):
        """Pipeline returns failure when git insights finds no commits."""
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "My Agent Build", "sources_key": "my_agent_git", "description": "test"},
            ),
            patch.object(Pipeline, "_get_git_article", return_value=None),
        ):
            pipeline = Pipeline(session=db_session)
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is False
        assert "git log" in result.error.lower() or "commit" in result.error.lower()


class TestXReplyLink:
    """The X self-reply link text must be plain human typing (no arrows)."""

    def _post(self, category, source_url=None):
        return MagicMock(topic_category=category, source_url=source_url)

    def test_stock_link_has_no_arrows(self):
        link = _x_reply_link(self._post("stock_talk"))
        assert "→" not in link and "->" not in link
        assert "lubot.ai" in link

    def test_general_link_has_no_arrows(self):
        link = _x_reply_link(self._post("biohacker"))
        assert "→" not in link and "->" not in link
        assert "lubot.ai" in link

    def test_ai_news_uses_source_url(self):
        link = _x_reply_link(self._post("ai_news", source_url="https://example.com/x"))
        assert link == "https://example.com/x"


class TestBiohackerPipeline:
    """Phase F: biohacker is podcast-primary, with a news-scrape fallback."""

    _BIO_TOPIC = {"name": "Biohacker", "sources_key": "biohacker", "description": "longevity"}

    def _podcast_article(self):
        return ScrapedArticle(
            title="Why morning light matters",
            url="https://podcast.example/ep1",
            summary="- stop seed oils\n- get morning sun (free)\n- test ApoB (cheap bloodwork)",
            source="The Human Upgrade with Dave Asprey",
            published_at=None,
            source_priority=0,
        )

    @pytest.mark.asyncio
    async def test_biohacker_uses_podcast_not_scraper(self, db_session):
        """When a longevity episode is available, it IS the article — no web scrape."""
        with (
            patch("src.scheduler.get_todays_topic", return_value=self._BIO_TOPIC),
            patch.object(Pipeline, "_get_podcast_article", return_value=self._podcast_article()) as mock_pod,
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock) as mock_scrape,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 27))

        assert result.success is True
        mock_pod.assert_called_once()
        assert mock_pod.call_args.kwargs.get("topic") == "biohacker"
        mock_scrape.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_topic_and_show_offset_override_rotation(self, db_session):
        """generate_post(topic=..., show_offset=N) uses that slot, not get_todays_topic."""
        with (
            # rotation says something else — the explicit topic must win
            patch("src.scheduler.get_todays_topic", return_value={"name": "AI News", "sources_key": "ai_news"}),
            patch.object(Pipeline, "_get_podcast_article", return_value=self._podcast_article()) as mock_pod,
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock) as mock_scrape,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(
                target_date=date(2026, 6, 27), topic=self._BIO_TOPIC, show_offset=2
            )

        assert result.success is True
        mock_scrape.assert_not_called()  # took the biohacker path, not ai_news scrape
        assert mock_pod.call_args.kwargs.get("show_offset") == 2

    @pytest.mark.asyncio
    async def test_biohacker_falls_back_to_scrape_when_no_episode(self, db_session):
        """No usable episode -> the news scraper keeps the post flowing."""
        with (
            patch("src.scheduler.get_todays_topic", return_value=self._BIO_TOPIC),
            patch.object(Pipeline, "_get_podcast_article", return_value=None),
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=_make_articles()),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 27))

        assert result.success is True
        # the post was created from the scraped fallback article
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post is not None and post.status == "pending"


def _waka_article():
    return ScrapedArticle(
        title="Building in public: 58h 33m coded this week, mostly Python",
        url="https://wakatime.com/dashboard",
        summary="MY CODING WEEK (2026-06-07 to 2026-06-13):\nTotal time coding: 58h 33m",
        source="wakatime:lubot",
        published_at=None,
        source_priority=0,
    )


class TestWakatimePipeline:
    """wakatime category uses WakaTimeInsights instead of the web scraper (Phase 2.75 / 15o)."""

    @pytest.mark.asyncio
    async def test_wakatime_uses_insights(self, db_session):
        """Pipeline uses WakaTimeInsights for the wakatime category, not scrape_topic."""
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Building in Public", "sources_key": "wakatime", "description": "test"},
            ),
            patch.object(Pipeline, "_get_wakatime_article", return_value=_waka_article()) as mock_waka,
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock) as mock_scrape,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        mock_waka.assert_called_once()
        mock_scrape.assert_not_called()

    @pytest.mark.asyncio
    async def test_wakatime_fails_gracefully(self, db_session):
        """Returns failure when no WakaTime archives are found."""
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Building in Public", "sources_key": "wakatime", "description": "test"},
            ),
            patch.object(Pipeline, "_get_wakatime_article", return_value=None),
        ):
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is False
        assert "wakatime" in result.error.lower()

    @pytest.mark.asyncio
    async def test_wakatime_does_not_screenshot_login_url(self, db_session):
        """The dashboard URL is login-walled — never screenshot it; fall back to a generated image."""
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Building in Public", "sources_key": "wakatime", "description": "test"},
            ),
            patch.object(Pipeline, "_get_wakatime_article", return_value=_waka_article()),
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_shot,
            patch(
                "src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")
            ) as mock_gen,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        mock_shot.assert_not_called()  # must NOT hit the login-walled URL
        mock_gen.assert_called_once()  # used the generated-image fallback

    @pytest.mark.asyncio
    async def test_my_agent_git_enriched_with_wakatime(self, db_session):
        """Build-log post also feeds this week's WakaTime stats to the writer (both articles)."""
        git_article = ScrapedArticle(
            title="Add stock fundamental analysis",
            url="https://git.lubot.ai/lubot/services-agent-api",
            summary="Feature area: stock",
            source="git:lubot-staging-services-agent-api",
            published_at=None,
            source_priority=0,
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "My Agent Build", "sources_key": "my_agent_git", "description": "test"},
            ),
            patch.object(Pipeline, "_get_git_article", return_value=git_article),
            patch("src.scheduler.WakaTimeInsights") as mock_waka_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()) as mock_write,
            patch("src.scheduler.take_git_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_waka_cls.return_value.get_weekly_stats.return_value = _waka_article()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        sources = {a.source for a in mock_write.call_args.kwargs["articles"]}
        assert "wakatime:lubot" in sources  # enriched with coding stats
        assert any(s.startswith("git:") for s in sources)  # plus the real commit

    @pytest.mark.asyncio
    async def test_wakatime_renders_stat_card(self, db_session):
        """wakatime category renders its own stat-card screenshot, not the login URL."""
        from src.wakatime_insights import WeeklyStats

        stats = WeeklyStats(
            days_active=7,
            total_seconds=210600,
            by_language={"Python": 116340},
            by_project={"LuBot": 207840},
            ai_sessions=9,
            ai_prompt_events=580,
            ai_input_tokens=924597335,
            ai_cost=2650.57,
            start_date="2026-06-07",
            end_date="2026-06-13",
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Building in Public", "sources_key": "wakatime", "description": "test"},
            ),
            patch("src.scheduler.WakaTimeInsights") as mock_waka_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch(
                "src.scheduler.take_wakatime_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/wakatime-card.png"),
            ) as mock_card,
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_url_shot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock) as mock_gen,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_waka = mock_waka_cls.return_value
            mock_waka.get_weekly_stats.return_value = _waka_article()
            mock_waka.weekly_stats = stats
            mock_waka.include_costs = True
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report

            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        mock_card.assert_called_once()  # rendered the stat card
        mock_url_shot.assert_not_called()  # never hit the login-walled URL
        mock_gen.assert_not_called()  # no need for the AI-image fallback
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.image_path == "/tmp/wakatime-card.png"


class TestMyAgentScreenshots:
    """My Agent posts (both variants) should screenshot staging.lubot.ai."""

    @pytest.mark.asyncio
    async def test_my_agent_screenshots_staging(self, db_session):
        """my_agent screenshots staging.lubot.ai."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "My Agent", "sources_key": "my_agent", "description": "test"},
            ),
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_screenshot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_screenshot.return_value = MagicMock(path="/tmp/staging.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            await pipeline.generate_post(target_date=date(2026, 3, 23))

        mock_screenshot.assert_called_once_with("https://staging.lubot.ai")

    @pytest.mark.asyncio
    async def test_my_agent_git_uses_git_screenshot(self, db_session):
        """my_agent_git uses take_git_screenshot instead of staging URL."""
        git_article = ScrapedArticle(
            title="Add stock fundamental analysis",
            url="https://git.lubot.ai/lubot/services-agent-api",
            summary="Feature area: stock\nLines changed: +40/-5",
            source="git:lubot-staging-services-agent-api",
            published_at=None,
            source_priority=0,
        )
        writer_result = _make_writer_result()

        from src.git_insights import GitCommit

        mock_commit = GitCommit(
            hash="abc1234",
            date=datetime(2026, 3, 22),
            message="Add stock fundamental analysis",
            files_changed=3,
            lines_added=40,
            lines_deleted=5,
            changed_files=["src/stock/fundamental.py", "tests/test_fundamental.py"],
        )

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "My Agent Build", "sources_key": "my_agent_git", "description": "test"},
            ),
            patch.object(Pipeline, "_get_git_article", return_value=git_article),
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_git_screenshot", new_callable=AsyncMock) as mock_git_shot,
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_screenshot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_git_shot.return_value = MagicMock(path="/tmp/git-commit.png")

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            # Set up the git insights with best_commit
            pipeline._git_insights = MagicMock()
            pipeline._git_insights.best_commit = mock_commit
            await pipeline.generate_post(target_date=date(2026, 3, 23))

        mock_git_shot.assert_called_once()
        mock_screenshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_generic_category_screenshots_article_url(self, db_session):
        """Generic scraper categories (not special-cased) screenshot the article URL."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "AI Gadgets", "sources_key": "ai_gadgets", "description": "test"},
            ),
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_screenshot,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_screenshot.return_value = MagicMock(path="/tmp/article.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            await pipeline.generate_post(target_date=date(2026, 3, 23))

        # Should use the article URL, not staging
        mock_screenshot.assert_called_once_with("https://example.com/ai-chip")

    @pytest.mark.asyncio
    async def test_insight_categories_use_branded_card(self, db_session):
        """tech_talk / biohacker / Investing Principle render the insight card, not a screenshot."""
        for sources_key, name in [("tech_talk", "Tech Talk"), ("biohacker", "Biohacker"), ("stock_talk", "Investing")]:
            articles = _make_articles()
            writer_result = _make_writer_result()
            writer_result.card_headline = "a sharp one liner"

            with (
                patch(
                    "src.scheduler.get_todays_topic",
                    return_value={"name": name, "sources_key": sources_key, "description": "test"},
                ),
                patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
                patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
                patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
                patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_url_shot,
                patch("src.scheduler.take_insight_screenshot", new_callable=AsyncMock) as mock_card,
                patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
                patch("src.scheduler.SelfLearner") as mock_learner_cls,
                patch("src.scheduler.KnowledgeBase"),
            ):
                mock_card.return_value = MagicMock(path="/tmp/insight.png")
                mock_dedup = MagicMock()
                mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
                mock_dedup.record_url = MagicMock()
                mock_dedup_cls.return_value = mock_dedup
                mock_learner = MagicMock()
                mock_report = MagicMock()
                mock_report.format_for_writer.return_value = ""
                mock_learner.generate_performance_report.return_value = mock_report
                mock_learner_cls.return_value = mock_learner

                await Pipeline(session=db_session).generate_post(target_date=date(2026, 3, 23))

            # Insight card rendered with the writer's pull-quote; no third-party/staging screenshot
            mock_card.assert_called_once()
            assert mock_card.call_args[0][0] == "a sharp one liner"
            mock_url_shot.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_news_uses_branded_headline_card(self, db_session):
        """ai_news renders a branded headline card, NOT a third-party site screenshot (Phase 2.12 A)."""
        articles = _make_articles()
        writer_result = _make_writer_result()

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "AI News", "sources_key": "ai_news", "description": "test"},
            ),
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock) as mock_url_shot,
            patch("src.scheduler.take_headline_screenshot", new_callable=AsyncMock) as mock_card,
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_card.return_value = MagicMock(path="/tmp/headline.png")

            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
            mock_dedup.record_url = MagicMock()
            mock_dedup_cls.return_value = mock_dedup

            mock_learner = MagicMock()
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner.generate_performance_report.return_value = mock_report
            mock_learner_cls.return_value = mock_learner

            pipeline = Pipeline(session=db_session)
            await pipeline.generate_post(target_date=date(2026, 3, 23))

        # Branded card used; the third-party article URL is NOT screenshotted
        mock_card.assert_called_once()
        mock_url_shot.assert_not_called()


class TestRecentPostsMemory:
    """Anti-repeat: the writer is fed this category's recent posts to avoid repeating itself."""

    def _add(self, session, category, text):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category=category,
            topic_title="t",
            post_text=text,
            status="pending",
        )
        session.add(post)
        session.flush()
        return post

    def test_returns_category_posts_newest_first(self, db_session):
        self._add(db_session, "biohacker", "oldest")
        self._add(db_session, "biohacker", "middle")
        self._add(db_session, "biohacker", "newest")
        recent = Pipeline(session=db_session)._get_recent_posts("biohacker", limit=2)
        assert recent == ["newest", "middle"]  # newest first, capped at limit

    def test_filters_by_category(self, db_session):
        self._add(db_session, "biohacker", "bio post")
        self._add(db_session, "ai_news", "news post")
        recent = Pipeline(session=db_session)._get_recent_posts("biohacker")
        assert recent == ["bio post"]

    def test_empty_when_no_history(self, db_session):
        assert Pipeline(session=db_session)._get_recent_posts("biohacker") == []


class TestApprovalWorkflow:
    def test_approve_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        approve_post(db_session, post.id)
        db_session.refresh(post)
        assert post.status == "approved"

    def test_reject_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        reject_post(db_session, post.id)
        db_session.refresh(post)
        assert post.status == "rejected"

    def test_cannot_approve_rejected_post(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="rejected",
        )
        db_session.add(post)
        db_session.flush()

        result = approve_post(db_session, post.id)
        assert result is False

    def test_approve_nonexistent_post(self, db_session):
        result = approve_post(db_session, 99999)
        assert result is False


# ---------------------------------------------------------------------------
# Publish approved posts
# ---------------------------------------------------------------------------


class TestPublishApproved:
    @pytest.mark.asyncio
    async def test_publishes_approved_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="My post text",
            image_path="/tmp/image.png",
            status="approved",
        )
        db_session.add(post)
        db_session.flush()

        mock_publisher = AsyncMock()
        mock_publisher.publish_images = AsyncMock(return_value="urn:li:share:pub123")
        mock_publisher.platform_name = "linkedin"

        with (
            patch("src.scheduler.get_publisher", return_value=mock_publisher),
            patch("src.scheduler.os.path.exists", return_value=True),
            patch("builtins.open", MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"img")))),
        ):
            count = await publish_approved_posts(db_session, access_token="test", person_urn="urn:li:person:x")

        assert count >= 1
        db_session.refresh(post)
        assert post.status == "published"
        mock_publisher.publish_images.assert_awaited()  # card (+ any extra images) sent as a carousel

    @pytest.mark.asyncio
    async def test_publishes_card_plus_extra_photo_as_carousel(self, db_session):
        """A post with extra_image_paths sends the card + the photo together (2 images)."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="biohacker",
            topic_title="Coffee",
            post_text="glass cups only",
            image_path="/tmp/card.png",
            extra_image_paths=["/tmp/photo.jpg"],
            status="approved",
        )
        db_session.add(post)
        db_session.flush()

        mock_publisher = AsyncMock()
        mock_publisher.publish_images = AsyncMock(return_value="urn:li:share:x")
        mock_publisher.platform_name = "linkedin"

        with (
            patch("src.scheduler.get_publisher", return_value=mock_publisher),
            patch("src.scheduler.os.path.exists", return_value=True),
            patch("builtins.open", MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"img")))),
        ):
            await publish_approved_posts(db_session, access_token="t", person_urn="urn:li:person:x")

        # publish_images was called with BOTH images (card + photo)
        _, kwargs = mock_publisher.publish_images.call_args
        images = kwargs.get("images") or mock_publisher.publish_images.call_args.args[1]
        assert len(images) == 2

    @pytest.mark.asyncio
    async def test_skips_non_approved_posts(self, db_session):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
        )
        db_session.add(post)
        db_session.flush()

        count = await publish_approved_posts(db_session, access_token="test", person_urn="urn:li:person:x")
        assert count == 0


class TestBookGrounding:
    """RAG: grounded categories get book concepts; others do not (Phase 2.8 / 15c-6)."""

    def _common_mocks(self, stack):
        articles = _make_articles()
        stack.enter_context(patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles))
        dedup = MagicMock()
        dedup.check_article = AsyncMock(return_value=MagicMock(is_duplicate=False))
        dedup.record_url = MagicMock()
        stack.enter_context(patch("src.scheduler.DuplicateChecker", return_value=dedup))
        stack.enter_context(
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png"))
        )
        stack.enter_context(
            patch(
                "src.scheduler.take_insight_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/i.png"),
            )
        )
        stack.enter_context(
            patch(
                "src.scheduler.take_headline_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/h.png"),
            )
        )
        stack.enter_context(patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=None))
        learner = stack.enter_context(patch("src.scheduler.SelfLearner")).return_value
        report = MagicMock()
        report.format_for_writer.return_value = ""
        learner.generate_performance_report.return_value = report

    @pytest.mark.asyncio
    async def test_grounded_category_injects_concepts(self, db_session):
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.scheduler.get_todays_topic",
                    return_value={"name": "Tech Talk", "sources_key": "tech_talk", "description": "x"},
                )
            )
            self._common_mocks(stack)
            kb = stack.enter_context(patch("src.scheduler.KnowledgeBase")).return_value
            kb.search.return_value = [MagicMock(text="Partitioning splits data across nodes.")]
            mock_write = stack.enter_context(
                patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result())
            )
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 16))

        assert result.success is True
        kb.search.assert_called_once()
        assert mock_write.call_args.kwargs["book_concepts"] == ["Partitioning splits data across nodes."]

    @pytest.mark.asyncio
    async def test_ungrounded_category_no_kb(self, db_session):
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.scheduler.get_todays_topic",
                    return_value={"name": "Biohacker", "sources_key": "biohacker", "description": "x"},
                )
            )
            self._common_mocks(stack)
            mock_kb = stack.enter_context(patch("src.scheduler.KnowledgeBase"))
            mock_write = stack.enter_context(
                patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result())
            )
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 16))

        assert result.success is True
        mock_kb.assert_not_called()  # biohacker never touches the knowledge base
        assert mock_write.call_args.kwargs["book_concepts"] == []

    @pytest.mark.asyncio
    async def test_kb_failure_is_non_fatal(self, db_session):
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.scheduler.get_todays_topic",
                    return_value={"name": "AI News", "sources_key": "ai_news", "description": "x"},
                )
            )
            self._common_mocks(stack)
            kb = stack.enter_context(patch("src.scheduler.KnowledgeBase")).return_value
            kb.search.side_effect = RuntimeError("NIM down")
            mock_write = stack.enter_context(
                patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result())
            )
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 16))

        assert result.success is True  # KB hiccup must not break the post
        assert mock_write.call_args.kwargs["book_concepts"] == []


class TestStockPipeline:
    """market_pulse uses StockInsights (yfinance), not the web scraper (Phase 2.10)."""

    @pytest.mark.asyncio
    async def test_market_pulse_uses_stock_insights(self, db_session):
        stock_article = ScrapedArticle(
            title="Market week: S&P 500 closed +1.0%",
            url="",
            summary="THIS WEEK IN THE MARKET: real numbers",
            source="stock:market",
            published_at=None,
            source_priority=0,
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Market Pulse", "sources_key": "market_pulse", "description": "test"},
            ),
            patch.object(Pipeline, "_get_stock_article", return_value=stock_article) as mock_stock,
            patch("src.scheduler.PodcastInsights") as mock_pod_cls,
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock) as mock_scrape,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.take_card_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_pod_cls.return_value.get_episode_article.return_value = None  # no podcast (offline)
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        mock_stock.assert_called_once()
        mock_scrape.assert_not_called()  # uses yfinance, not the web scraper

    @pytest.mark.asyncio
    async def test_market_pulse_fails_gracefully(self, db_session):
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Market Pulse", "sources_key": "market_pulse", "description": "test"},
            ),
            patch.object(Pipeline, "_get_stock_article", return_value=None),
        ):
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is False
        assert "market" in result.error.lower()

    @pytest.mark.asyncio
    async def test_market_pulse_passes_podcast_context_to_writer(self, db_session):
        """Phase 2.10b: the distilled podcast bullets reach write_post as podcast_context."""
        stock_article = ScrapedArticle(
            title="Market week",
            url="",
            summary="S&P 500 closed 7,503.45, +1.0% on the week.",
            source="stock:market",
            published_at=None,
        )
        podcast_article = ScrapedArticle(
            title="Ep 469",
            url="https://x/ep",
            summary="- breadth is narrow\n- rotation debate",
            source="Animal Spirits",
            published_at=None,
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Market Pulse", "sources_key": "market_pulse", "description": "test"},
            ),
            patch.object(Pipeline, "_get_stock_article", return_value=stock_article),
            patch("src.scheduler.PodcastInsights") as mock_pod_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()) as mock_write,
            patch("src.scheduler.take_card_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_pod_cls.return_value.get_episode_article.return_value = podcast_article
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        ctx = mock_write.call_args.kwargs["podcast_context"]
        assert "- breadth is narrow" in ctx and "- rotation debate" in ctx  # bullets present
        assert "chart shown with this post plots" in ctx  # writer told what's charted (C2)

    @pytest.mark.asyncio
    async def test_market_pulse_non_fatal_when_podcast_fails(self, db_session):
        """A podcast/transcription failure must NOT break the post — falls back to yfinance only."""
        stock_article = ScrapedArticle(
            title="Market week",
            url="",
            summary="S&P 500 closed 7,503.45, +1.0% on the week.",
            source="stock:market",
            published_at=None,
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Market Pulse", "sources_key": "market_pulse", "description": "test"},
            ),
            patch.object(Pipeline, "_get_stock_article", return_value=stock_article),
            patch("src.scheduler.PodcastInsights") as mock_pod_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()) as mock_write,
            patch("src.scheduler.take_card_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=None),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_pod_cls.return_value.get_episode_article.side_effect = Exception("openrouter down")
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True  # podcast failure does not break the post
        assert mock_write.call_args.kwargs["podcast_context"] is None

    @pytest.mark.asyncio
    async def test_podcast_theme_drives_chart_symbols(self, db_session):
        """Phase 2.10c: the podcast theme selects the symbols yfinance/chart use."""
        podcast_article = ScrapedArticle(
            title="Ep",
            url="u",
            summary="- oil prices ignored the geopolitical alarm bells this week",
            source="RiskReversal Pod",
            published_at=None,
        )
        captured = {}

        def fake_get_stock(self, indices=None):
            captured["indices"] = indices
            self._stock = MagicMock(market_week=None)  # skip the screenshot branch
            return ScrapedArticle(
                title="Market week",
                url="",
                summary="S&P 500 closed 7,503.45, +1.0%.",
                source="stock:market",
                published_at=None,
            )

        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Market Pulse", "sources_key": "market_pulse", "description": "test"},
            ),
            patch("src.scheduler.PodcastInsights") as mock_pod_cls,
            patch.object(Pipeline, "_get_stock_article", fake_get_stock),
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()),
            patch("src.scheduler.generate_image", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/g.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
        ):
            mock_pod_cls.return_value.get_episode_article.return_value = podcast_article
            mock_report = MagicMock()
            mock_report.format_for_writer.return_value = ""
            mock_learner_cls.return_value.generate_performance_report.return_value = mock_report
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 14))

        assert result.success is True
        assert captured["indices"] is not None
        assert "CL=F" in captured["indices"]  # oil theme -> crude charted
        assert "^GSPC" in captured["indices"]  # always anchored


class TestDevTrackPipeline:
    """Phase 2.11: Building in Public uses the DevTrack weekly report (primary) + luxury card."""

    @pytest.mark.asyncio
    async def test_uses_devtrack_report_and_card(self, db_session):
        from src.devtrack_insights import DevTrackReport

        report = DevTrackReport(
            period_label="Week 25",
            date_range="Jun 15 to Jun 21, 2026",
            total_hours=81.6,
            code_hours=54.9,
            commits=82,
            lines_added=25922,
            lines_deleted=3949,
            files_changed=128,
            tests_added=428,
            ai_sessions=18,
            ai_output_tokens=25298310,
            days_worked="7 of 7",
            momentum="-8.9h (-14%)",
        )
        art = ScrapedArticle(
            title="Build week: Week 25",
            url="",
            summary="MY BUILD WEEK: 81.6h, 82 commits",
            source="devtrack:weekly",
            published_at=None,
        )
        with (
            patch(
                "src.scheduler.get_todays_topic",
                return_value={"name": "Building in Public", "sources_key": "wakatime", "description": "test"},
            ),
            patch("src.scheduler.DevTrackInsights") as mdt,
            patch(
                "src.scheduler.take_devtrack_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/dt.png"),
            ) as mshot,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=_make_writer_result()) as mwrite,
            patch("src.scheduler.SelfLearner") as mlearn,
        ):
            inst = mdt.return_value
            inst.get_weekly_report.return_value = art
            inst.report = report
            mreport = MagicMock()
            mreport.format_for_writer.return_value = ""
            mlearn.return_value.generate_performance_report.return_value = mreport
            result = await Pipeline(session=db_session).generate_post(target_date=date(2026, 6, 23))

        assert result.success is True
        assert mwrite.call_args.kwargs["articles"][0].source == "devtrack:weekly"  # DevTrack summary used
        mshot.assert_awaited_once()  # luxury DevTrack card rendered


# ---------------------------------------------------------------------------
# Multi-platform publish fan-out (Phase 2.18 G) — publishers mocked
# ---------------------------------------------------------------------------


class TestPublishFanout:
    def _approved(self, db_session, cat="ai_news"):
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category=cat,
            topic_title="t",
            post_text="body text",
            source_url="https://example.com/x",
            status="approved",
            image_path=None,
        )
        db_session.add(post)
        db_session.flush()
        return post

    def _li(self, urn="urn:li:share:1"):
        m = MagicMock()
        m.publish_text = AsyncMock(return_value=urn)
        m.publish_image = AsyncMock(return_value=urn)
        return m

    def _x(self):
        m = MagicMock()
        m.publish_text = AsyncMock(return_value="x123")
        m.publish_image = AsyncMock(return_value="x123")
        m.reply = AsyncMock(return_value="x124")
        return m

    @pytest.mark.asyncio
    async def test_fans_out_to_linkedin_and_x(self, db_session):
        post = self._approved(db_session)
        li, xp = self._li(), self._x()
        with (
            patch("src.scheduler.x_client.x_configured", return_value=True),
            patch("src.scheduler.get_publisher", side_effect=lambda p, **k: li if p == "linkedin" else xp),
        ):
            n = await publish_approved_posts(db_session, "tok", "urn")
        assert n == 1
        assert db_session.query(PublisherPost).filter_by(id=post.id).first().status == "published"
        platforms = {d.platform for d in db_session.query(PublisherDestination).filter_by(post_id=post.id).all()}
        assert platforms == {"linkedin", "x"}
        xp.reply.assert_awaited_once()  # self-reply link posted

    @pytest.mark.asyncio
    async def test_x_not_configured_is_linkedin_only(self, db_session):
        post = self._approved(db_session)
        li = self._li("urn:li:share:2")
        with (
            patch("src.scheduler.x_client.x_configured", return_value=False),
            patch("src.scheduler.get_publisher", return_value=li),
        ):
            n = await publish_approved_posts(db_session, "tok", "urn")
        assert n == 1
        platforms = {d.platform for d in db_session.query(PublisherDestination).filter_by(post_id=post.id).all()}
        assert platforms == {"linkedin"}

    @pytest.mark.asyncio
    async def test_idempotent_skips_already_published_platform(self, db_session):
        post = self._approved(db_session)
        db_session.add(
            PublisherDestination(
                post_id=post.id,
                platform="linkedin",
                platform_post_urn="old",
                status="published",
                published_at=datetime.now(UTC),
            )
        )
        db_session.flush()
        li, xp = self._li(), self._x()
        with (
            patch("src.scheduler.x_client.x_configured", return_value=True),
            patch("src.scheduler.get_publisher", side_effect=lambda p, **k: li if p == "linkedin" else xp),
        ):
            await publish_approved_posts(db_session, "tok", "urn")
        li.publish_text.assert_not_called()  # already published -> skipped (no double-post)
        xp.publish_text.assert_awaited_once()  # x newly published

    @pytest.mark.asyncio
    async def test_non_fatal_partial_failure_retries_later(self, db_session):
        post = self._approved(db_session)
        li = self._li("urn:li:share:3")
        xp = self._x()
        xp.publish_text = AsyncMock(side_effect=RuntimeError("x down"))
        with (
            patch("src.scheduler.x_client.x_configured", return_value=True),
            patch("src.scheduler.get_publisher", side_effect=lambda p, **k: li if p == "linkedin" else xp),
        ):
            n = await publish_approved_posts(db_session, "tok", "urn")
        # linkedin succeeded; x failed -> post stays approved for retry; only LI destination
        platforms = {
            d.platform
            for d in db_session.query(PublisherDestination).filter_by(post_id=post.id, status="published").all()
        }
        assert platforms == {"linkedin"}
        assert db_session.query(PublisherPost).filter_by(id=post.id).first().status == "approved"
        assert n == 0
