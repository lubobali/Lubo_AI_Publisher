"""Tests for AI writer — prompt assembly, LLM call, response parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scraper import ScrapedArticle
from src.writer import (
    WriterResult,
    build_system_prompt,
    build_user_prompt,
    get_llm_client,
    load_voice_rules,
    load_voice_samples,
    parse_response,
    write_post,
)

# ---------------------------------------------------------------------------
# WriterResult dataclass
# ---------------------------------------------------------------------------


class TestWriterResult:
    def test_create_result(self):
        result = WriterResult(
            post_text="Just tested something cool.",
            screenshot_url="https://example.com/screenshot",
            hashtags=["#AI", "#DataEngineering"],
        )
        assert result.post_text == "Just tested something cool."
        assert result.screenshot_url == "https://example.com/screenshot"
        assert len(result.hashtags) == 2

    def test_result_without_screenshot(self):
        result = WriterResult(
            post_text="Short post.",
            screenshot_url=None,
            hashtags=["#AI"],
        )
        assert result.screenshot_url is None

    def test_result_fields_are_correct_types(self):
        result = WriterResult(
            post_text="Text",
            screenshot_url="https://example.com",
            hashtags=["#tag"],
        )
        assert isinstance(result.post_text, str)
        assert isinstance(result.hashtags, list)


# ---------------------------------------------------------------------------
# Voice rules loading
# ---------------------------------------------------------------------------


class TestLoadVoiceRules:
    def test_returns_dict(self):
        rules = load_voice_rules()
        assert isinstance(rules, dict)

    def test_has_core_sections(self):
        rules = load_voice_rules()
        assert "core_voice" in rules
        assert "structure" in rules
        assert "do" in rules
        assert "do_not" in rules
        assert "hashtag_rules" in rules

    def test_has_topic_specific_rules(self):
        rules = load_voice_rules()
        assert "topic_specific" in rules
        assert "biohacker" in rules["topic_specific"]
        assert "my_agent" in rules["topic_specific"]


# ---------------------------------------------------------------------------
# Voice samples loading
# ---------------------------------------------------------------------------


class TestLoadVoiceSamples:
    def test_returns_string(self):
        samples = load_voice_samples()
        assert isinstance(samples, str)

    def test_contains_sample_posts(self):
        samples = load_voice_samples()
        assert "SAMPLE 1" in samples
        assert len(samples) > 500  # Should have substantial content


# ---------------------------------------------------------------------------
# System prompt building
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_contains_voice_rules(self):
        prompt = build_system_prompt()
        assert "casual" in prompt.lower() or "Casual" in prompt
        assert "ESL" in prompt

    def test_contains_do_not_rules(self):
        prompt = build_system_prompt()
        assert "AI slop" in prompt or "slop" in prompt.lower()
        assert "LinkedIn influencer" in prompt or "influencer" in prompt.lower()

    def test_contains_voice_samples(self):
        prompt = build_system_prompt()
        # Should include actual post content from samples
        assert "LuBot" in prompt or "lubot" in prompt.lower()

    def test_contains_structure_rules(self):
        prompt = build_system_prompt()
        assert "hook" in prompt.lower()
        assert "400" in prompt  # min chars
        assert "1500" in prompt  # max chars

    def test_contains_hashtag_rules(self):
        prompt = build_system_prompt()
        assert "hashtag" in prompt.lower()

    def test_always_i_never_we_rule(self):
        prompt = build_system_prompt()
        assert "never" in prompt.lower() and "we" in prompt.lower()

    def test_json_output_instruction(self):
        prompt = build_system_prompt()
        assert "JSON" in prompt or "json" in prompt

    def test_contains_anti_fabrication_rule(self):
        prompt = build_system_prompt()
        assert "do not invent" in prompt.lower() or "never fabricate" in prompt.lower()


# ---------------------------------------------------------------------------
# User prompt building
# ---------------------------------------------------------------------------

SAMPLE_ARTICLES = [
    ScrapedArticle(
        title="New AI Model Breaks Records",
        url="https://techcrunch.com/new-ai-model",
        summary="A new model achieves state of the art on all benchmarks.",
        source="TechCrunch",
        published_at=None,
    ),
    ScrapedArticle(
        title="NVIDIA Ships New Chip",
        url="https://theverge.com/nvidia-chip",
        summary="NVIDIA announced their latest GPU architecture today.",
        source="The Verge",
        published_at=None,
    ),
]


class TestBookConcepts:
    """RAG book concepts injected as low-priority background (Phase 2.8 / 15c-6)."""

    _CONCEPTS = ["Partitioning splits data across nodes so writes scale horizontally."]

    def test_injected_when_present(self):
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="something Lubo built",
            articles=SAMPLE_ARTICLES,
            book_concepts=self._CONCEPTS,
        )
        assert "Partitioning splits data across nodes" in prompt

    def test_guardrails_present_with_concepts(self):
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            book_concepts=self._CONCEPTS,
        ).lower()
        # never name the book, never claim false experience, may ignore it
        assert "do not name" in prompt or "not name or cite" in prompt
        assert "unless you actually did" in prompt
        assert "ignore it" in prompt

    def test_no_block_without_concepts(self):
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
        ).lower()
        assert "unless you actually did" not in prompt

    def test_empty_concepts_no_block(self):
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            book_concepts=[],
        ).lower()
        assert "unless you actually did" not in prompt


class TestBuildUserPrompt:
    def test_contains_topic(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news, quick post about tools/announcements",
            articles=SAMPLE_ARTICLES,
        )
        assert "AI News" in prompt

    def test_contains_article_titles(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=SAMPLE_ARTICLES,
        )
        assert "New AI Model Breaks Records" in prompt
        assert "NVIDIA Ships New Chip" in prompt

    def test_contains_article_urls(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=SAMPLE_ARTICLES,
        )
        assert "techcrunch.com" in prompt
        assert "theverge.com" in prompt

    def test_contains_article_summaries(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=SAMPLE_ARTICLES,
        )
        assert "state of the art" in prompt or "benchmarks" in prompt

    def test_handles_empty_articles(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=[],
        )
        assert "AI News" in prompt  # Should still include topic

    def test_includes_topic_specific_rules_for_biohacker(self):
        prompt = build_user_prompt(
            topic_name="Biohacker",
            topic_description="Biohacking, supplements, longevity",
            articles=SAMPLE_ARTICLES,
        )
        # Biohacker rules should prohibit fabrication, not encourage it
        assert "do not invent" in prompt.lower() or "do not fabricate" in prompt.lower()

    def test_biohacker_prompt_no_fabrication_instruction(self):
        """Biohacker prompt must NOT tell the LLM to write from personal experience."""
        prompt = build_user_prompt(
            topic_name="Biohacker",
            topic_description="Biohacking, supplements, longevity",
            articles=SAMPLE_ARTICLES,
        )
        assert "write from personal experience" not in prompt.lower()
        assert "lubo actually does biohacking" not in prompt.lower()
        assert "i tried this" not in prompt.lower()

    def test_includes_topic_specific_rules_for_my_agent(self):
        prompt = build_user_prompt(
            topic_name="My Agent",
            topic_description="LuBot showcase",
            articles=SAMPLE_ARTICLES,
        )
        assert "LuBot" in prompt

    def test_my_agent_prompt_includes_real_features(self):
        prompt = build_user_prompt(
            topic_name="My Agent",
            topic_description="LuBot showcase",
            articles=SAMPLE_ARTICLES,
        )
        assert "ONLY use features from this list" in prompt
        assert "Multi-model orchestration" in prompt
        assert "Hetzner" in prompt
        assert "do NOT invent features" in prompt

    def test_non_my_agent_topics_no_features_list(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=SAMPLE_ARTICLES,
        )
        assert "ONLY use features from this list" not in prompt
        assert "Multi-model orchestration" not in prompt

    def test_my_agent_build_prompt_includes_git_context(self):
        """My Agent Build posts include build log instructions."""
        prompt = build_user_prompt(
            topic_name="My Agent Build",
            topic_description="What Lubo built this week",
            articles=SAMPLE_ARTICLES,
        )
        assert "BUILD LOG" in prompt
        assert "THIS WEEK I BUILT" in prompt
        assert "EXACT numbers" in prompt

    def test_my_agent_build_prompt_no_marketing_features(self):
        """My Agent Build should NOT include the marketing features list."""
        prompt = build_user_prompt(
            topic_name="My Agent Build",
            topic_description="What Lubo built this week",
            articles=SAMPLE_ARTICLES,
        )
        assert "Multi-model orchestration" not in prompt
        assert "ONLY use features from this list" not in prompt

    def test_building_in_public_prompt_includes_stats_rules(self):
        """Building in Public posts get WakaTime-stats build-in-public instructions."""
        prompt = build_user_prompt(
            topic_name="Building in Public",
            topic_description="Lubo's real coding week",
            articles=SAMPLE_ARTICLES,
        )
        assert "BUILDING IN PUBLIC" in prompt
        assert "EXACT numbers" in prompt
        assert "WakaTime" in prompt

    def test_my_agent_build_includes_topic_specific_rules(self):
        """My Agent Build has its own topic-specific rules in voice_rules.yaml."""
        rules = load_voice_rules()
        assert "my_agent_build" in rules["topic_specific"]
        build_rules = rules["topic_specific"]["my_agent_build"]
        assert any("BUILD LOG" in r for r in build_rules)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_parse_valid_json(self):
        raw = '{"post_text": "Just built something cool.", "screenshot_url": "https://example.com/page", "hashtags": ["#AI", "#Tech"]}'
        result = parse_response(raw)
        assert isinstance(result, WriterResult)
        assert result.post_text == "Just built something cool."
        assert result.screenshot_url == "https://example.com/page"
        assert result.hashtags == ["#AI", "#Tech"]

    def test_parse_json_in_markdown_block(self):
        raw = '```json\n{"post_text": "Hello world.", "screenshot_url": null, "hashtags": ["#AI"]}\n```'
        result = parse_response(raw)
        assert result.post_text == "Hello world."

    def test_parse_missing_screenshot_url(self):
        raw = '{"post_text": "No screenshot needed.", "hashtags": ["#AI"]}'
        result = parse_response(raw)
        assert result.screenshot_url is None

    def test_parse_missing_hashtags_defaults_empty(self):
        raw = '{"post_text": "No hashtags."}'
        result = parse_response(raw)
        assert result.hashtags == []

    def test_parse_invalid_json_returns_none(self):
        raw = "This is not JSON at all, just a regular post."
        result = parse_response(raw)
        assert result is None

    def test_parse_empty_post_text_returns_none(self):
        raw = '{"post_text": "", "hashtags": ["#AI"]}'
        result = parse_response(raw)
        assert result is None

    # --- Bug 1: screenshot_url "null" string normalization ---

    @pytest.mark.parametrize(
        "null_value",
        ["null", "none", "None", "NULL", "NONE", ""],
    )
    def test_parse_normalizes_null_screenshot_url(self, null_value):
        import json

        raw = json.dumps({"post_text": "Great post.", "screenshot_url": null_value, "hashtags": ["#AI"]})
        result = parse_response(raw)
        assert result is not None
        assert result.screenshot_url is None

    def test_parse_keeps_valid_screenshot_url(self):
        raw = '{"post_text": "Great post.", "screenshot_url": "https://example.com/page", "hashtags": ["#AI"]}'
        result = parse_response(raw)
        assert result.screenshot_url == "https://example.com/page"

    def test_parse_json_null_screenshot_url_is_none(self):
        raw = '{"post_text": "Great post.", "screenshot_url": null, "hashtags": ["#AI"]}'
        result = parse_response(raw)
        assert result.screenshot_url is None

    # --- Bug 3: JSON surrounded by non-JSON text ---

    @pytest.mark.parametrize(
        "prefix,suffix",
        [
            ("**", ""),
            ("**", "**"),
            ("Here is the response:\n", ""),
            ("", "\n\nNote: this is a good post"),
            ("Sure! Here you go:\n\n", "\n\nLet me know if you need changes."),
        ],
    )
    def test_parse_strips_surrounding_text(self, prefix, suffix):
        json_body = '{"post_text": "Test post content.", "screenshot_url": null, "hashtags": ["#AI"]}'
        raw = f"{prefix}{json_body}{suffix}"
        result = parse_response(raw)
        assert result is not None
        assert result.post_text == "Test post content."

    def test_parse_plain_text_extracts_as_post(self):
        """When model ignores JSON format and writes plain text, extract it as post_text."""
        raw = (
            "Just tested the new NVIDIA model on our codebase.\n"
            "The results were insane — 3x faster inference.\n\n"
            "Who else is trying this?\n\n"
            "#AI #NVIDIA #MachineLearning"
        )
        result = parse_response(raw)
        assert result is not None
        assert "NVIDIA model" in result.post_text
        assert "#AI" in result.hashtags

    def test_parse_plain_text_with_title_prefix(self):
        """Model writes **Title**\\n\\nBody — still extract."""
        raw = (
            "**Tech Talk: Building a Real-Time System**\n\n"
            "Ever felt like a magic box engineer? I sure have.\n"
            "Built something cool this week.\n\n"
            "#TechTalk #Engineering"
        )
        result = parse_response(raw)
        assert result is not None
        assert "magic box" in result.post_text

    def test_parse_short_garbage_returns_none(self):
        raw = "ok"
        result = parse_response(raw)
        assert result is None

    def test_parse_plain_text_extracts_hashtags(self):
        raw = "Great post about AI.\n\n#AI #DataEngineering #NVIDIA"
        result = parse_response(raw)
        assert result is not None
        assert "#AI" in result.hashtags
        assert "#DataEngineering" in result.hashtags
        assert "#NVIDIA" in result.hashtags
        # Hashtags should NOT remain in post_text
        assert "#AI" not in result.post_text
        assert "#NVIDIA" not in result.post_text

    def test_parse_plain_text_no_hashtags(self):
        raw = "Just a regular post about building things.\nNo hashtags here, just vibes."
        result = parse_response(raw)
        assert result is not None
        assert result.hashtags == []

    def test_parse_json_strips_hashtags_from_post_text(self):
        """When LLM puts hashtags in post_text despite instructions, strip them."""
        raw = '{"post_text": "Great post about AI.\\n\\n#AI #DataEngineering #NVIDIA", "screenshot_url": null, "hashtags": ["#AI", "#DataEngineering", "#NVIDIA"]}'
        result = parse_response(raw)
        assert result is not None
        assert "#AI" not in result.post_text
        assert "#NVIDIA" not in result.post_text
        assert "Great post about AI." in result.post_text

    def test_parse_plain_text_deduplicates_hashtags(self):
        """Hashtags mentioned multiple times should appear once in the array."""
        raw = "AI is wild. #AI is changing everything.\n\n#AI #DataEngineering #AI"
        result = parse_response(raw)
        assert result is not None
        assert result.hashtags.count("#AI") == 1


# ---------------------------------------------------------------------------
# get_llm_client — NVIDIA NIM client config
# ---------------------------------------------------------------------------


class TestGetLlmClient:
    def test_client_has_increased_max_retries(self):
        import os

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}):
            client = get_llm_client()
            assert client.max_retries == 5

    def test_client_has_timeout(self):
        import os

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}):
            client = get_llm_client()
            assert client.timeout is not None


# ---------------------------------------------------------------------------
# write_post — full orchestration (LLM mocked)
# ---------------------------------------------------------------------------


class TestWritePost:
    @pytest.mark.asyncio
    async def test_returns_writer_result(self):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"post_text": "AI just got wild. New model dropped and its faster than anything.", "screenshot_url": "https://techcrunch.com/new-ai-model", "hashtags": ["#AI", "#DataEngineering", "#NVIDIA"]}'
                )
            )
        ]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            result = await write_post(
                topic_name="AI News",
                topic_description="Hot AI news",
                articles=SAMPLE_ARTICLES,
            )

            assert isinstance(result, WriterResult)
            assert len(result.post_text) > 0
            assert len(result.hashtags) > 0

    @pytest.mark.asyncio
    async def test_calls_llm_with_system_and_user_messages(self):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content='{"post_text": "Test post.", "screenshot_url": null, "hashtags": ["#AI"]}')
            )
        ]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            await write_post(
                topic_name="AI News",
                topic_description="Hot AI news",
                articles=SAMPLE_ARTICLES,
            )

            # Verify LLM was called with messages
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            messages = call_kwargs["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_uses_correct_model(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"post_text": "Test.", "hashtags": ["#AI"]}'))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            await write_post(
                topic_name="AI News",
                topic_description="Hot AI news",
                articles=SAMPLE_ARTICLES,
            )

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert "nemotron" in call_kwargs["model"].lower()

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API Error"))

        with patch("src.writer.get_llm_client", return_value=mock_client):
            result = await write_post(
                topic_name="AI News",
                topic_description="Hot AI news",
                articles=SAMPLE_ARTICLES,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unparseable_response(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Sorry, I cannot help with that."))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            result = await write_post(
                topic_name="AI News",
                topic_description="Hot AI news",
                articles=SAMPLE_ARTICLES,
            )
            assert result is None
