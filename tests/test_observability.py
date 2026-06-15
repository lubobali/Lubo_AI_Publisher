"""Tests for Langfuse observability module.

Mock all Langfuse API calls — never send real traces in tests.
Keep internal logic real: imports, exports, client initialization.
"""

import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherPost
from src.post_processor import process_post
from src.scraper import ScrapedArticle
from src.writer import WriterResult

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher")


class TestObservabilityModule:
    def test_module_exports_observe(self):
        """observability.py must re-export langfuse.observe."""
        from src.observability import observe

        assert callable(observe)

    def test_module_exports_get_client(self):
        """observability.py must re-export langfuse.get_client."""
        from src.observability import get_client

        assert callable(get_client)

    def test_get_client_returns_langfuse_instance(self):
        """get_client() should return a Langfuse client (mocked, no real API call)."""
        with patch("langfuse.get_client") as mock_get_client:
            mock_get_client.return_value = "mock-client"
            from src import observability

            # Re-call through the module
            client = observability.get_client()
            assert client is not None


class TestLangfuseTraceIdColumn:
    def test_publisher_post_has_langfuse_trace_id_attribute(self):
        """PublisherPost model must have a langfuse_trace_id column."""
        mapper = sa_inspect(PublisherPost)
        column_names = {col.key for col in mapper.column_attrs}
        assert "langfuse_trace_id" in column_names

    def test_langfuse_trace_id_is_nullable(self):
        """langfuse_trace_id must be nullable (existing posts don't have it)."""
        mapper = sa_inspect(PublisherPost)
        col = mapper.columns["langfuse_trace_id"]
        assert col.nullable is True

    def test_langfuse_trace_id_is_string_100(self):
        """langfuse_trace_id must be String(100)."""
        mapper = sa_inspect(PublisherPost)
        col = mapper.columns["langfuse_trace_id"]
        assert col.type.length == 100


# ---------------------------------------------------------------------------
# Step 2: Root trace + Writer generation
# ---------------------------------------------------------------------------


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
    """Pin the daily topic to a scraper-based category so pipeline tracing tests are
    deterministic regardless of which category the real rotation lands on for a date."""
    with patch(
        "src.scheduler.get_todays_topic",
        return_value={"name": "AI News", "sources_key": "ai_news", "description": "test"},
    ):
        yield


def _make_articles():
    return [
        ScrapedArticle(
            title="AI Breaks New Ground",
            url="https://example.com/ai-ground",
            summary="Major breakthrough.",
            source="TechCrunch",
            published_at=datetime.now(UTC) - timedelta(hours=6),
        ),
    ]


def _make_writer_result():
    return WriterResult(
        post_text="Just tested the new AI model. Wild results. What do you think?",
        screenshot_url="https://example.com/ai-ground",
        hashtags=["#AI", "#DataEngineering"],
    )


class TestPipelineTraceId:
    """Pipeline.generate_post() must store langfuse_trace_id on the post."""

    @pytest.mark.asyncio
    async def test_generate_post_stores_trace_id(self, db_session):
        """After a successful pipeline run, the post should have a langfuse_trace_id."""
        from src.scheduler import Pipeline

        articles = _make_articles()
        writer_result = _make_writer_result()

        mock_langfuse = MagicMock()
        mock_langfuse.get_current_trace_id.return_value = "abc123def456"

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/shot.png"),
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
            patch("src.scheduler.get_client", return_value=mock_langfuse),
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
            result = await pipeline.generate_post(target_date=date(2026, 3, 23))

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.langfuse_trace_id == "abc123def456"

    @pytest.mark.asyncio
    async def test_trace_id_is_none_when_langfuse_unavailable(self, db_session):
        """If Langfuse returns None for trace_id, post still saves (trace_id is nullable)."""
        from src.scheduler import Pipeline

        articles = _make_articles()
        writer_result = _make_writer_result()

        mock_langfuse = MagicMock()
        mock_langfuse.get_current_trace_id.return_value = None

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch(
                "src.scheduler.take_screenshot",
                new_callable=AsyncMock,
                return_value=MagicMock(path="/tmp/shot.png"),
            ),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
            patch("src.scheduler.get_client", return_value=mock_langfuse),
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

        assert result.success is True
        post = db_session.query(PublisherPost).filter_by(id=result.post_id).first()
        assert post.langfuse_trace_id is None


class TestWriterGeneration:
    """write_post() must report model, tokens, and metadata to Langfuse."""

    @pytest.mark.asyncio
    async def test_write_post_calls_update_current_generation(self):
        """After LLM call, write_post should update Langfuse with model/token info."""
        from src.writer import write_post

        mock_msg = MagicMock()
        mock_msg.content = (
            '{"post_text": "Great AI news today. What do you think?", "screenshot_url": null, "hashtags": ["#AI"]}'
        )
        mock_msg.reasoning_content = None

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 500
        mock_usage.completion_tokens = 200

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_msg)]
        mock_response.usage = mock_usage
        mock_response.model = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()

        with (
            patch("src.writer.get_llm_client", return_value=mock_client),
            patch("src.writer.get_client", return_value=mock_langfuse),
        ):
            result = await write_post(
                topic_name="AI News",
                topic_description="Latest AI developments",
                articles=_make_articles(),
            )

        assert result is not None
        mock_langfuse.update_current_generation.assert_called_once()
        call_kwargs = mock_langfuse.update_current_generation.call_args[1]
        assert call_kwargs["model"] == "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        assert call_kwargs["usage_details"]["input"] == 500
        assert call_kwargs["usage_details"]["output"] == 200

    @pytest.mark.asyncio
    async def test_write_post_succeeds_when_langfuse_fails(self):
        """If Langfuse update raises, write_post should still return the result."""
        from src.writer import write_post

        mock_msg = MagicMock()
        mock_msg.content = (
            '{"post_text": "Another great day in AI. Thoughts?", "screenshot_url": null, "hashtags": ["#AI"]}'
        )
        mock_msg.reasoning_content = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_msg)]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        mock_response.model = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()
        mock_langfuse.update_current_generation.side_effect = Exception("Langfuse down")

        with (
            patch("src.writer.get_llm_client", return_value=mock_client),
            patch("src.writer.get_client", return_value=mock_langfuse),
        ):
            result = await write_post(
                topic_name="AI News",
                topic_description="Latest",
                articles=_make_articles(),
            )

        # Post should still be returned despite Langfuse failure
        assert result is not None
        assert "AI" in result.post_text


# ---------------------------------------------------------------------------
# Step 3: Post-processor compliance scoring
# ---------------------------------------------------------------------------


class TestCalculateComplianceScore:
    """calculate_compliance_score() is pure logic — no mocks needed."""

    def test_perfect_post_scores_one(self):
        """Zero fixes → compliance 1.0 (perfect)."""
        from src.post_processor import calculate_compliance_score

        assert calculate_compliance_score(0) == 1.0

    def test_all_fixes_scores_zero(self):
        """All 6 fix categories triggered → compliance 0.0."""
        from src.post_processor import calculate_compliance_score

        assert calculate_compliance_score(6) == 0.0

    def test_half_fixes_scores_half(self):
        """3 out of 6 categories → compliance 0.5."""
        from src.post_processor import calculate_compliance_score

        assert calculate_compliance_score(3) == 0.5

    def test_one_fix_scores_five_sixths(self):
        """1 fix → compliance ~0.833."""
        from src.post_processor import calculate_compliance_score

        score = calculate_compliance_score(1)
        assert abs(score - (5 / 6)) < 0.001

    def test_negative_fixes_clamp_to_one(self):
        """Negative input should still return 1.0."""
        from src.post_processor import calculate_compliance_score

        assert calculate_compliance_score(-1) == 1.0

    def test_more_than_max_clamps_to_zero(self):
        """More than 6 should clamp to 0.0, not go negative."""
        from src.post_processor import calculate_compliance_score

        assert calculate_compliance_score(10) == 0.0


class TestProcessPostComplianceScoring:
    """process_post() must report fix metadata and compliance score to Langfuse."""

    def test_process_post_reports_fixes_metadata(self):
        """process_post should report which fixes were applied to Langfuse."""
        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            # Text with dashes and apostrophes — triggers 2 fix categories
            text = "AI is wild \u2014 don't you think? " * 20
            process_post(text, ["#AI"])

        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert "dashes_stripped" in metadata
        assert "apostrophes_fixed" in metadata
        assert "total_fixes" in metadata
        assert metadata["dashes_stripped"] is True
        assert metadata["apostrophes_fixed"] is True

    def test_process_post_submits_compliance_score(self):
        """process_post should submit llm_compliance score to Langfuse."""
        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            # Clean text — no fixes needed. Short paragraphs, no trailing space.
            text = "AI is changing everything.\n\nNo dashes here.\n\nWhat do you think?"
            process_post(text, ["#AI"])

        mock_langfuse.score_current_trace.assert_called_once()
        call_kwargs = mock_langfuse.score_current_trace.call_args[1]
        assert call_kwargs["name"] == "llm_compliance"
        assert call_kwargs["value"] == 1.0  # no fixes → perfect score

    def test_process_post_compliance_score_reflects_fixes(self):
        """Compliance score should decrease when fixes are applied."""
        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            # Text with em dash + apostrophe → 2 categories triggered → score ~0.667
            text = "This is wild \u2014 don't miss it. " * 20
            process_post(text, ["#AI"])

        call_kwargs = mock_langfuse.score_current_trace.call_args[1]
        score = call_kwargs["value"]
        assert score < 1.0  # not perfect
        assert score > 0.0  # not all broken

    def test_process_post_works_when_langfuse_fails(self):
        """process_post must still return correct results if Langfuse raises."""
        mock_langfuse = MagicMock()
        mock_langfuse.update_current_span.side_effect = Exception("Langfuse down")

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            text = "AI is wild \u2014 don't stop. " * 20
            result_text, result_tags = process_post(text, ["#AI"])

        assert "\u2014" not in result_text  # dashes still stripped
        assert "don't" not in result_text  # apostrophes still stripped

    def test_process_post_return_type_unchanged(self):
        """process_post must still return tuple[str, list[str]] — no breaking changes."""
        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            result = process_post("Hello world. " * 10, ["#AI"])

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)


# ---------------------------------------------------------------------------
# Step 4: Embedding + Dedup tracing
# ---------------------------------------------------------------------------


class TestEmbeddingGenerationTracing:
    """get_embedding() must report model and dimensions to Langfuse as a generation."""

    @pytest.mark.asyncio
    async def test_get_embedding_reports_generation_metadata(self):
        """After embedding API call, Langfuse should get model name + dimensions."""
        from src.duplicate_checker import DuplicateChecker

        mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()

        with (
            patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client),
            patch("src.duplicate_checker.get_client", return_value=mock_langfuse),
        ):
            checker = DuplicateChecker(session=None)
            await checker.get_embedding("Test text about AI")

        mock_langfuse.update_current_generation.assert_called_once()
        call_kwargs = mock_langfuse.update_current_generation.call_args[1]
        assert call_kwargs["model"] == "nvidia/nv-embedqa-e5-v5"
        assert call_kwargs["metadata"]["input_length"] == len("Test text about AI")
        assert call_kwargs["metadata"]["embedding_dimensions"] == 5

    @pytest.mark.asyncio
    async def test_get_embedding_returns_vector_unchanged(self):
        """@observe decorator must not alter the return value."""
        from src.duplicate_checker import DuplicateChecker

        mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()

        with (
            patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client),
            patch("src.duplicate_checker.get_client", return_value=mock_langfuse),
        ):
            checker = DuplicateChecker(session=None)
            result = await checker.get_embedding("Test text")

        assert result == mock_embedding


class TestCheckArticleSpanTracing:
    """check_article() must report dedup metadata to Langfuse as a span."""

    @pytest.mark.asyncio
    async def test_check_article_reports_metadata_on_pass(self, db_session):
        """Non-duplicate article should report is_duplicate=False, caught_by=None."""
        from src.duplicate_checker import DuplicateChecker

        mock_embedding = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]
        mock_api_client = AsyncMock()
        mock_api_client.embeddings.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()

        with (
            patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_api_client),
            patch("src.duplicate_checker.get_client", return_value=mock_langfuse),
        ):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/unique-article-999",
                title="Completely unique article about quantum AI",
                category="tech_talk",
                published_at=datetime.now(UTC) - timedelta(days=1),
            )

        assert result.is_duplicate is False
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["is_duplicate"] is False
        assert metadata["caught_by"] is None
        assert metadata["category"] == "tech_talk"
        assert metadata["embedding_available"] is True

    @pytest.mark.asyncio
    async def test_check_article_reports_metadata_on_url_duplicate(self, db_session):
        """URL-duplicate article should report caught_by='url_dedup'."""
        from src.duplicate_checker import DuplicateChecker
        from src.models import PublisherScrapedUrl

        # Insert a known URL into DB
        db_session.add(PublisherScrapedUrl(url="https://example.com/already-seen"))
        db_session.flush()

        mock_langfuse = MagicMock()

        with patch("src.duplicate_checker.get_client", return_value=mock_langfuse):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/already-seen",
                title="Some title",
                category="ai_news",
            )

        assert result.is_duplicate is True
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["is_duplicate"] is True
        assert metadata["caught_by"] == "url_dedup"


class TestSourceQualityScore:
    """Pipeline must submit source_quality score based on duplicates skipped."""

    @pytest.mark.asyncio
    async def test_source_quality_all_fresh(self, db_session):
        """First article passes → score 1.0 (0 duplicates out of N scraped)."""
        from src.scheduler import Pipeline

        articles = _make_articles()  # 1 article
        writer_result = _make_writer_result()
        mock_langfuse = MagicMock()

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
            patch("src.scheduler.get_client", return_value=mock_langfuse),
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

        assert result.success is True
        # Find the source_quality score call among all score_current_trace calls
        score_calls = [
            c for c in mock_langfuse.score_current_trace.call_args_list if c[1].get("name") == "source_quality"
        ]
        assert len(score_calls) == 1
        assert score_calls[0][1]["value"] == 1.0

    @pytest.mark.asyncio
    async def test_source_quality_some_duplicates(self, db_session):
        """2 of 4 articles are duplicates → score 0.5."""
        from src.scheduler import Pipeline

        articles = [
            ScrapedArticle(
                title=f"Article {i}",
                url=f"https://example.com/art-{i}",
                summary="Summary",
                source="Test",
                published_at=datetime.now(UTC) - timedelta(hours=i),
            )
            for i in range(4)
        ]
        writer_result = _make_writer_result()
        mock_langfuse = MagicMock()

        dup = MagicMock(is_duplicate=True, reason="duplicate")
        not_dup = MagicMock(is_duplicate=False)

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.write_post", new_callable=AsyncMock, return_value=writer_result),
            patch("src.scheduler.take_screenshot", new_callable=AsyncMock, return_value=MagicMock(path="/tmp/s.png")),
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
            patch("src.scheduler.get_client", return_value=mock_langfuse),
        ):
            mock_dedup = MagicMock()
            mock_dedup.check_article = AsyncMock(side_effect=[dup, dup, not_dup])
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
        score_calls = [
            c for c in mock_langfuse.score_current_trace.call_args_list if c[1].get("name") == "source_quality"
        ]
        assert len(score_calls) == 1
        assert score_calls[0][1]["value"] == 0.5  # 2 dups out of 4 scraped

    @pytest.mark.asyncio
    async def test_source_quality_all_duplicates(self, db_session):
        """All 3 articles are duplicates → score 0.0."""
        from src.scheduler import Pipeline

        articles = [
            ScrapedArticle(
                title=f"Dup {i}",
                url=f"https://example.com/dup-{i}",
                summary="Dup",
                source="Test",
                published_at=datetime.now(UTC) - timedelta(hours=i),
            )
            for i in range(3)
        ]
        mock_langfuse = MagicMock()
        dup = MagicMock(is_duplicate=True, reason="duplicate")

        with (
            patch("src.scheduler.scrape_topic", new_callable=AsyncMock, return_value=articles),
            patch("src.scheduler.DuplicateChecker") as mock_dedup_cls,
            patch("src.scheduler.SelfLearner") as mock_learner_cls,
            patch("src.scheduler.get_client", return_value=mock_langfuse),
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
        score_calls = [
            c for c in mock_langfuse.score_current_trace.call_args_list if c[1].get("name") == "source_quality"
        ]
        assert len(score_calls) == 1
        assert score_calls[0][1]["value"] == 0.0


class TestDedupLangfuseResilience:
    """Dedup functions must still work correctly when Langfuse is unavailable."""

    @pytest.mark.asyncio
    async def test_get_embedding_works_when_langfuse_fails(self):
        """get_embedding returns valid vector even if Langfuse raises."""
        from src.duplicate_checker import DuplicateChecker

        mock_embedding = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()
        mock_langfuse.update_current_generation.side_effect = Exception("Langfuse down")

        with (
            patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_client),
            patch("src.duplicate_checker.get_client", return_value=mock_langfuse),
        ):
            checker = DuplicateChecker(session=None)
            result = await checker.get_embedding("Test text")

        assert result == mock_embedding

    @pytest.mark.asyncio
    async def test_check_article_works_when_langfuse_fails(self, db_session):
        """check_article returns correct DuplicateResult even if Langfuse raises."""
        from src.duplicate_checker import DuplicateChecker

        mock_embedding = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=mock_embedding)]
        mock_api_client = AsyncMock()
        mock_api_client.embeddings.create = AsyncMock(return_value=mock_response)

        mock_langfuse = MagicMock()
        mock_langfuse.update_current_span.side_effect = Exception("Langfuse down")
        mock_langfuse.update_current_generation.side_effect = Exception("Langfuse down")

        with (
            patch("src.duplicate_checker.AsyncOpenAI", return_value=mock_api_client),
            patch("src.duplicate_checker.get_client", return_value=mock_langfuse),
        ):
            checker = DuplicateChecker(db_session)
            result = await checker.check_article(
                url="https://example.com/resilience-test-unique",
                title="Unique resilience test article",
                category="tech_talk",
                published_at=datetime.now(UTC) - timedelta(days=1),
            )

        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Step 5: Scraper + Screenshot tracing
# ---------------------------------------------------------------------------


class TestScraperSpanTracing:
    """scrape_topic() must report source metrics to Langfuse."""

    @pytest.mark.asyncio
    async def test_scrape_topic_reports_metadata(self):
        """Successful scrape should report category, sources, and article counts."""
        from src.scraper import scrape_topic

        sample_rss = """<?xml version="1.0"?>
        <rss><channel>
            <item><title>AI News 1</title><link>https://example.com/ai1</link></item>
            <item><title>AI News 2</title><link>https://example.com/ai2</link></item>
        </channel></rss>"""

        mock_langfuse = MagicMock()

        with (
            patch("src.scraper.fetch_url", new_callable=AsyncMock, return_value=sample_rss),
            patch("src.scraper.get_client", return_value=mock_langfuse),
        ):
            articles = await scrape_topic("ai_news")

        assert len(articles) > 0
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["category"] == "ai_news"
        assert metadata["sources_attempted"] > 0
        assert metadata["sources_fetched"] > 0
        assert metadata["articles_after_ranking"] == len(articles)

    @pytest.mark.asyncio
    async def test_scrape_topic_tracks_fetch_failures(self):
        """When all sources fail to fetch, metadata reflects the failures."""
        from src.scraper import scrape_topic

        mock_langfuse = MagicMock()

        with (
            patch("src.scraper.fetch_url", new_callable=AsyncMock, return_value=None),
            patch("src.scraper.get_client", return_value=mock_langfuse),
        ):
            articles = await scrape_topic("ai_news")

        assert articles == []
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["sources_attempted"] > 0
        assert metadata["sources_fetched"] == 0
        assert metadata["articles_after_ranking"] == 0


class TestScreenshotSpanTracing:
    """take_screenshot() must report success/failure metadata to Langfuse."""

    def _make_playwright_mocks(self, screenshot_bytes=b"x" * 50000):
        """Build standard Playwright mock chain for take_screenshot tests."""
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=screenshot_bytes)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        return mock_cm, mock_page, mock_browser

    @pytest.mark.asyncio
    async def test_take_screenshot_reports_success_metadata(self):
        """Successful screenshot reports success=True and size to Langfuse."""
        from pathlib import Path

        from src.screenshotter import take_screenshot

        mock_cm, mock_page, mock_browser = self._make_playwright_mocks(b"x" * 50000)
        mock_langfuse = MagicMock()

        with (
            patch("src.screenshotter.async_playwright", return_value=mock_cm),
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
            patch("src.screenshotter.get_client", return_value=mock_langfuse),
        ):
            result = await take_screenshot("https://example.com/article")

        assert result is not None
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["url"] == "https://example.com/article"
        assert metadata["success"] is True
        assert metadata["screenshot_size_bytes"] == 50000
        assert metadata["error_detected"] is False

    @pytest.mark.asyncio
    async def test_take_screenshot_reports_failure_metadata(self):
        """Navigation failure reports success=False and failed_reason to Langfuse."""
        from pathlib import Path

        from src.screenshotter import take_screenshot

        mock_cm, mock_page, mock_browser = self._make_playwright_mocks()
        mock_page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))
        mock_langfuse = MagicMock()

        with (
            patch("src.screenshotter.async_playwright", return_value=mock_cm),
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
            patch("src.screenshotter.get_client", return_value=mock_langfuse),
        ):
            result = await take_screenshot("https://bad-url.example.com")

        assert result is None
        mock_langfuse.update_current_span.assert_called_once()
        metadata = mock_langfuse.update_current_span.call_args[1]["metadata"]
        assert metadata["success"] is False
        assert metadata["failed_reason"] is not None

    @pytest.mark.asyncio
    async def test_take_screenshot_works_when_langfuse_fails(self):
        """Screenshot still works if Langfuse raises."""
        from pathlib import Path

        from src.screenshotter import take_screenshot

        mock_cm, mock_page, mock_browser = self._make_playwright_mocks(b"x" * 50000)
        mock_langfuse = MagicMock()
        mock_langfuse.update_current_span.side_effect = Exception("Langfuse down")

        with (
            patch("src.screenshotter.async_playwright", return_value=mock_cm),
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
            patch("src.screenshotter.get_client", return_value=mock_langfuse),
        ):
            result = await take_screenshot("https://example.com/article")

        assert result is not None
        assert result.url == "https://example.com/article"


class TestScraperScreenshotResilience:
    """Scraper and screenshot must work when Langfuse is unavailable."""

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_scrape_topic_works_when_langfuse_fails(self):
        """scrape_topic still returns articles when Langfuse raises."""
        from src.scraper import scrape_topic

        sample_rss = """<?xml version="1.0"?>
        <rss><channel>
            <item><title>Test</title><link>https://example.com/test</link></item>
        </channel></rss>"""

        mock_langfuse = MagicMock()
        mock_langfuse.update_current_span.side_effect = Exception("Langfuse down")

        with (
            patch("src.scraper.fetch_url", new_callable=AsyncMock, return_value=sample_rss),
            patch("src.scraper.get_client", return_value=mock_langfuse),
        ):
            articles = await scrape_topic("ai_news")

        assert len(articles) > 0


# ---------------------------------------------------------------------------
# Step 6: Human approval scoring
# ---------------------------------------------------------------------------


class TestHumanApprovalScoring:
    """approve_post/reject_post API routes must submit human_approval score to Langfuse."""

    def test_approve_post_submits_score(self, db_session):
        """Approving a post with a trace_id should score it 1.0 in Langfuse."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
            langfuse_trace_id="trace-abc-123",
        )
        db_session.add(post)
        db_session.flush()

        mock_langfuse = MagicMock()

        with patch("src.api.get_client", return_value=mock_langfuse):
            from fastapi.testclient import TestClient

            from src.api import app, get_db_session

            def override():
                yield db_session

            app.dependency_overrides[get_db_session] = override
            client = TestClient(app)
            resp = client.post(f"/api/posts/{post.id}/approve")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_langfuse.create_score.assert_called_once()
        call_kwargs = mock_langfuse.create_score.call_args[1]
        assert call_kwargs["trace_id"] == "trace-abc-123"
        assert call_kwargs["name"] == "human_approval"
        assert call_kwargs["value"] == 1.0

    def test_reject_post_submits_score(self, db_session):
        """Rejecting a post with a trace_id should score it 0.0 in Langfuse."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
            langfuse_trace_id="trace-xyz-789",
        )
        db_session.add(post)
        db_session.flush()

        mock_langfuse = MagicMock()

        with patch("src.api.get_client", return_value=mock_langfuse):
            from fastapi.testclient import TestClient

            from src.api import app, get_db_session

            def override():
                yield db_session

            app.dependency_overrides[get_db_session] = override
            client = TestClient(app)
            resp = client.post(f"/api/posts/{post.id}/reject")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_langfuse.create_score.assert_called_once()
        call_kwargs = mock_langfuse.create_score.call_args[1]
        assert call_kwargs["trace_id"] == "trace-xyz-789"
        assert call_kwargs["name"] == "human_approval"
        assert call_kwargs["value"] == 0.0

    def test_approve_without_trace_id_skips_langfuse(self, db_session):
        """Posts without langfuse_trace_id should not call Langfuse."""
        post = PublisherPost(
            posted_at=datetime.now(UTC),
            topic_category="ai_news",
            topic_title="Test",
            post_text="text",
            status="pending",
            langfuse_trace_id=None,
        )
        db_session.add(post)
        db_session.flush()

        mock_langfuse = MagicMock()

        with patch("src.api.get_client", return_value=mock_langfuse):
            from fastapi.testclient import TestClient

            from src.api import app, get_db_session

            def override():
                yield db_session

            app.dependency_overrides[get_db_session] = override
            client = TestClient(app)
            resp = client.post(f"/api/posts/{post.id}/approve")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_langfuse.create_score.assert_not_called()


# ---------------------------------------------------------------------------
# Step 7: Prompt versioning
# ---------------------------------------------------------------------------


class TestPromptVersioning:
    """write_post() must include a prompt_version hash in Langfuse metadata."""

    @pytest.mark.asyncio
    async def test_write_post_includes_prompt_version(self):
        """Langfuse metadata should contain prompt_version (8-char hex hash)."""
        from src.writer import write_post

        mock_msg = MagicMock()
        mock_msg.content = (
            '{"post_text": "AI is moving fast. What do you think?", "screenshot_url": null, "hashtags": ["#AI"]}'
        )
        mock_msg.reasoning_content = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_msg)]
        mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=200)
        mock_response.model = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_langfuse = MagicMock()

        with (
            patch("src.writer.get_llm_client", return_value=mock_client),
            patch("src.writer.get_client", return_value=mock_langfuse),
        ):
            await write_post(
                topic_name="AI News",
                topic_description="Latest",
                articles=_make_articles(),
            )

        call_kwargs = mock_langfuse.update_current_generation.call_args[1]
        assert "prompt_version" in call_kwargs["metadata"]
        pv = call_kwargs["metadata"]["prompt_version"]
        assert isinstance(pv, str)
        assert len(pv) == 8  # 8-char hex

    def test_prompt_hash_is_deterministic(self):
        """Same prompt text should produce same hash."""
        from src.writer import hash_prompt

        h1 = hash_prompt("test prompt")
        h2 = hash_prompt("test prompt")
        assert h1 == h2

    def test_prompt_hash_changes_with_content(self):
        """Different prompt text should produce different hash."""
        from src.writer import hash_prompt

        h1 = hash_prompt("prompt version A")
        h2 = hash_prompt("prompt version B")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Step 8: Validation + Parse quality scoring
# ---------------------------------------------------------------------------


class TestValidationScoring:
    """validate_post() must submit a validation score to Langfuse."""

    def test_validate_post_scores_pass(self):
        """Passing validation → score 1.0."""
        from src.post_processor import validate_post

        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            ok, reason = validate_post("A" * 450 + " what do you think?")

        assert ok is True
        mock_langfuse.score_current_trace.assert_called()
        # Find the validation score (not llm_compliance)
        val_calls = [c for c in mock_langfuse.score_current_trace.call_args_list if c[1].get("name") == "validation"]
        assert len(val_calls) == 1
        assert val_calls[0][1]["value"] == 1.0

    def test_validate_post_scores_fail(self):
        """Failing validation → score 0.0 with reason."""
        from src.post_processor import validate_post

        mock_langfuse = MagicMock()

        with patch("src.post_processor.get_client", return_value=mock_langfuse):
            ok, reason = validate_post("Too short")

        assert ok is False
        val_calls = [c for c in mock_langfuse.score_current_trace.call_args_list if c[1].get("name") == "validation"]
        assert len(val_calls) == 1
        assert val_calls[0][1]["value"] == 0.0
        assert "short" in val_calls[0][1]["comment"].lower()


class TestParseQualityScoring:
    """parse_response() must submit parse_quality score to Langfuse."""

    def test_clean_json_scores_one(self):
        """Clean JSON parse → score 1.0."""
        from src.writer import parse_response

        mock_langfuse = MagicMock()

        with patch("src.writer.get_client", return_value=mock_langfuse):
            result = parse_response('{"post_text": "Hello world test post.", "hashtags": ["#AI"]}')

        assert result is not None
        mock_langfuse.score_current_trace.assert_called_once()
        call_kwargs = mock_langfuse.score_current_trace.call_args[1]
        assert call_kwargs["name"] == "parse_quality"
        assert call_kwargs["value"] == 1.0

    def test_plain_text_fallback_scores_low(self):
        """Plain text fallback → score 0.3."""
        from src.writer import parse_response

        mock_langfuse = MagicMock()

        with patch("src.writer.get_client", return_value=mock_langfuse):
            result = parse_response(
                "This is just plain text without any JSON formatting at all, longer than fifty chars for sure."
            )

        assert result is not None
        mock_langfuse.score_current_trace.assert_called_once()
        call_kwargs = mock_langfuse.score_current_trace.call_args[1]
        assert call_kwargs["name"] == "parse_quality"
        assert call_kwargs["value"] == 0.3

    def test_failed_parse_scores_zero(self):
        """Unparseable response → score 0.0."""
        from src.writer import parse_response

        mock_langfuse = MagicMock()

        with patch("src.writer.get_client", return_value=mock_langfuse):
            result = parse_response("x")  # too short for plain text

        assert result is None
        mock_langfuse.score_current_trace.assert_called_once()
        call_kwargs = mock_langfuse.score_current_trace.call_args[1]
        assert call_kwargs["name"] == "parse_quality"
        assert call_kwargs["value"] == 0.0
