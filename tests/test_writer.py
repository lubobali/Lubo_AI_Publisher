"""Tests for AI writer — prompt assembly, LLM call, response parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scraper import ScrapedArticle
from src.writer import (
    CarouselResult,
    WriterResult,
    build_carousel_system_prompt,
    build_carousel_user_prompt,
    build_system_prompt,
    build_user_prompt,
    get_llm_client,
    load_voice_rules,
    load_voice_samples,
    parse_carousel_response,
    parse_response,
    write_carousel,
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

    def test_forbids_invented_numbers(self):
        prompt = build_system_prompt().lower()
        assert "never invent a specific number" in prompt or "made up number" in prompt

    def test_forbids_invented_tools(self):
        prompt = build_system_prompt().lower()
        assert "tool" in prompt and ("didnt actually use" in prompt or "did not actually use" in prompt)

    def test_forbids_fabricated_setup_claims(self):
        """Never invent a personal hardware/infra claim ('I self-host X at N tok/s')."""
        prompt = build_system_prompt().lower()
        assert "self-host" in prompt
        assert "tokens per second" in prompt or "on my own hardware" in prompt
        # and it must appear in the carousel prompt too (convert + write_carousel reuse the base)
        from src.writer import build_carousel_system_prompt

        assert "self-host" in build_carousel_system_prompt().lower()

    def test_forbids_markdown(self):
        prompt = build_system_prompt().lower()
        assert "no markdown" in prompt or "plain text only" in prompt

    def test_forbids_em_dashes_and_arrows(self):
        prompt = build_system_prompt().lower()
        assert "plain human typing" in prompt
        assert "em-dash" in prompt or "em dash" in prompt
        assert "arrow" in prompt


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


class TestPodcastContext:
    """Phase 2.10b: distilled podcast bullets injected as the Market Pulse angle."""

    _BULLETS = "- breadth is narrow, few names carry the index\n- debate on rotating into value"

    def test_injected_when_present(self):
        prompt = build_user_prompt(
            topic_name="Market Pulse",
            topic_description="weekly market read",
            articles=SAMPLE_ARTICLES,
            podcast_context=self._BULLETS,
        )
        assert "breadth is narrow" in prompt
        assert "recent market thinking" in prompt.lower()

    def test_guardrails_present(self):
        prompt = build_user_prompt(
            topic_name="Market Pulse",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            podcast_context=self._BULLETS,
        ).lower()
        # never name/quote the show, no number from it, casual voice, may ignore
        assert "never name" in prompt and ("podcast" in prompt or "show" in prompt)
        assert "no number" in prompt
        assert "ignore" in prompt

    def test_no_block_without_context(self):
        prompt = build_user_prompt(
            topic_name="Market Pulse",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
        ).lower()
        assert "recent market thinking" not in prompt

    def test_empty_context_no_block(self):
        prompt = build_user_prompt(
            topic_name="Market Pulse",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            podcast_context="",
        ).lower()
        assert "recent market thinking" not in prompt


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

    def test_tech_talk_is_opinion_not_project(self):
        """Tech Talk must be opinion/expertise, never a claimed personal project."""
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="senior take on a DE topic",
            articles=SAMPLE_ARTICLES,
        ).lower()
        assert "do not claim you personally built" in prompt
        assert "opinion" in prompt

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
        assert "100% NVIDIA powered" in prompt
        assert "Hetzner" in prompt
        assert "do NOT invent features" in prompt

    def test_non_my_agent_topics_no_features_list(self):
        prompt = build_user_prompt(
            topic_name="AI News",
            topic_description="Hot AI news",
            articles=SAMPLE_ARTICLES,
        )
        assert "ONLY use features from this list" not in prompt
        assert "100% NVIDIA powered" not in prompt

    def test_my_agent_build_prompt_is_story_not_metrics(self):
        """My Agent Build leads with the DECISION/lesson, and keeps line-count metrics OUT of the
        text (they live on the card) — never a changelog/metrics dump."""
        prompt = build_user_prompt(
            topic_name="My Agent Build",
            topic_description="What Lubo built this week",
            articles=SAMPLE_ARTICLES,
        )
        assert "THIS WEEK I BUILT" in prompt
        assert "do NOT put those numbers in the text" in prompt  # metrics stay on the card
        assert "judgment" in prompt.lower()  # senior framing, not a status update
        assert "stats get their own" not in prompt  # the old metrics-forcing rule is gone

    def test_my_agent_build_prompt_no_marketing_features(self):
        """My Agent Build should NOT include the marketing features list."""
        prompt = build_user_prompt(
            topic_name="My Agent Build",
            topic_description="What Lubo built this week",
            articles=SAMPLE_ARTICLES,
        )
        assert "100% NVIDIA powered" not in prompt
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


class TestBiohackerBlock:
    """Phase F: biohacker posts get Lubo's longevity philosophy block."""

    def _prompt(self):
        return build_user_prompt(
            topic_name="Biohacker",
            topic_description="longevity and health optimization",
            articles=SAMPLE_ARTICLES,
        )

    def test_has_biohacker_block(self):
        assert "BIOHACKER" in self._prompt()

    def test_frames_brief_as_worldview_not_template(self):
        """The brief must be a base to think from, NOT a checklist to recite."""
        p = self._prompt().lower()
        assert "worldview" in p or "think from" in p
        assert "not a checklist" in p or "not a template" in p
        assert "do not copy" in p or "do not list" in p

    def test_forces_one_idea_and_variety(self):
        p = self._prompt().lower()
        assert "one idea" in p
        assert "different facet" in p or "rotate" in p

    def test_leads_with_removing_harm(self):
        p = self._prompt().lower()
        assert "removing harm" in p or "stop" in p

    def test_includes_age_framework(self):
        p = self._prompt().lower()
        assert "biological age" in p and "chronological age" in p

    def test_includes_longevity_tests_tiers(self):
        p = self._prompt().lower()
        assert "free" in p and "epigenetic" in p

    def test_credibility_is_sparing_not_a_crutch(self):
        p = self._prompt()
        assert "46" in p  # true: 46, feels 35
        assert "crutch" in p.lower() or "sparingly" in p.lower()
        assert "do not repeat it" in p.lower() or "not as a crutch" in p.lower()

    def test_no_hard_biohacking_product_pitch(self):
        # LuBot has no biohacking feature yet; the block must say so
        assert "biohacking feature yet" in self._prompt()

    def test_not_present_for_other_topics(self):
        prompt = build_user_prompt(topic_name="Tech Talk", topic_description="x", articles=SAMPLE_ARTICLES)
        assert "BIOHACKER / LONGEVITY post" not in prompt


class TestAntiRepeatMemory:
    """Recent posts are fed back so the writer never runs the same play twice (all topics)."""

    _RECENT = [
        "Been at this 5 years. Im 46 and feel 35. Stop seed oils today.",
        "Morning light is the most underrated free biohack. What is yours?",
    ]

    def test_block_present_with_recent_posts(self):
        prompt = build_user_prompt(
            topic_name="Biohacker",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            recent_posts=self._RECENT,
        )
        assert "YOUR RECENT POSTS IN THIS CATEGORY" in prompt
        assert "Morning light is the most underrated" in prompt  # the actual past text is shown

    def test_block_warns_against_repeating_age_and_hooks(self):
        prompt = build_user_prompt(
            topic_name="Biohacker",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            recent_posts=self._RECENT,
        ).lower()
        assert "anti-repeat" in prompt
        assert "do not reuse the same opening" in prompt
        assert "age or years" in prompt  # specifically guards the credibility line

    def test_no_block_without_recent_posts(self):
        prompt = build_user_prompt(topic_name="Biohacker", topic_description="x", articles=SAMPLE_ARTICLES)
        assert "YOUR RECENT POSTS IN THIS CATEGORY" not in prompt

    def test_works_for_any_topic(self):
        prompt = build_user_prompt(
            topic_name="Tech Talk",
            topic_description="x",
            articles=SAMPLE_ARTICLES,
            recent_posts=["A past tech take about databases."],
        )
        assert "YOUR RECENT POSTS IN THIS CATEGORY" in prompt


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

    def test_parse_extracts_card_headline(self):
        raw = '{"post_text": "Long post body.", "card_headline": "Ship the boring parts"}'
        result = parse_response(raw)
        assert result.card_headline == "Ship the boring parts"

    def test_parse_card_headline_defaults_empty(self):
        raw = '{"post_text": "No card headline here."}'
        result = parse_response(raw)
        assert result.card_headline == ""


class TestDeriveCardHeadline:
    def test_uses_first_sentence_trimmed(self):
        from src.writer import derive_card_headline

        text = "Time in the market beats timing the market. The rest is noise."
        assert derive_card_headline(text) == "Time in the market beats timing the market"

    def test_caps_word_count(self):
        from src.writer import derive_card_headline

        text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
        assert len(derive_card_headline(text, max_words=5).split()) == 5

    def test_empty_text_safe(self):
        from src.writer import derive_card_headline

        assert derive_card_headline("") == ""

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
    def test_client_retries_then_fails_over(self):
        import os

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}):
            client = get_llm_client()
            # Lowered 5 -> 2 so we fail over to OpenRouter sooner than burning retries on a 429
            assert client.max_retries == 2

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
    async def test_uses_generous_token_budget_for_reasoning_models(self):
        # Reasoning models burn output tokens on thinking; too small a cap -> empty
        # content. Guard the fix: the budget must stay well above a tiny cap.
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"post_text": "Test.", "hashtags": ["#AI"]}'))]
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            await write_post(topic_name="AI News", topic_description="x", articles=SAMPLE_ARTICLES)

        assert mock_client.chat.completions.create.call_args[1]["max_tokens"] >= 6000

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


def _fake_ok_response(content='{"post_text": "calm long term market take", "hashtags": ["#Investing"]}'):
    """A successful chat-completion response mock."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    resp.model = "test-model"
    return resp


def _fake_empty_response():
    """A response whose content AND reasoning_content are empty."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=None, reasoning_content=None))]
    resp.usage = MagicMock(prompt_tokens=5, completion_tokens=0)
    resp.model = "test-model"
    return resp


class TestProviderFallback:
    """NIM-primary / OpenRouter-fallback in write_post."""

    @pytest.mark.asyncio
    async def test_falls_back_to_openrouter_when_nim_fails(self):
        nim = AsyncMock()
        nim.chat.completions.create = AsyncMock(side_effect=Exception("429 rate limit"))
        orc = AsyncMock()
        orc.chat.completions.create = AsyncMock(return_value=_fake_ok_response())
        with (
            patch("src.writer.get_llm_client", return_value=nim),
            patch("src.writer.get_fallback_client", return_value=orc),
        ):
            result = await write_post("AI News", "desc", SAMPLE_ARTICLES)
        assert isinstance(result, WriterResult)
        nim.chat.completions.create.assert_awaited_once()
        orc.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_when_nim_returns_empty(self):
        nim = AsyncMock()
        nim.chat.completions.create = AsyncMock(return_value=_fake_empty_response())
        orc = AsyncMock()
        orc.chat.completions.create = AsyncMock(return_value=_fake_ok_response())
        with (
            patch("src.writer.get_llm_client", return_value=nim),
            patch("src.writer.get_fallback_client", return_value=orc),
        ):
            result = await write_post("AI News", "desc", SAMPLE_ARTICLES)
        assert isinstance(result, WriterResult)
        orc.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_both_providers_fail(self):
        nim = AsyncMock()
        nim.chat.completions.create = AsyncMock(side_effect=Exception("nim down"))
        orc = AsyncMock()
        orc.chat.completions.create = AsyncMock(side_effect=Exception("or down"))
        with (
            patch("src.writer.get_llm_client", return_value=nim),
            patch("src.writer.get_fallback_client", return_value=orc),
        ):
            result = await write_post("AI News", "desc", SAMPLE_ARTICLES)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_fallback_when_unconfigured(self):
        nim = AsyncMock()
        nim.chat.completions.create = AsyncMock(side_effect=Exception("nim down"))
        with (
            patch("src.writer.get_llm_client", return_value=nim),
            patch("src.writer.get_fallback_client", return_value=None),
        ):
            result = await write_post("AI News", "desc", SAMPLE_ARTICLES)
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_nim_and_skips_fallback_on_success(self):
        nim = AsyncMock()
        nim.chat.completions.create = AsyncMock(return_value=_fake_ok_response())
        orc = AsyncMock()
        orc.chat.completions.create = AsyncMock(return_value=_fake_ok_response())
        with (
            patch("src.writer.get_llm_client", return_value=nim),
            patch("src.writer.get_fallback_client", return_value=orc),
        ):
            result = await write_post("AI News", "desc", SAMPLE_ARTICLES)
        assert isinstance(result, WriterResult)
        orc.chat.completions.create.assert_not_awaited()

    def test_fallback_client_none_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from src.writer import get_fallback_client

        assert get_fallback_client() is None

    def test_fallback_client_built_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        from src.writer import get_fallback_client

        assert get_fallback_client() is not None


# ---------------------------------------------------------------------------
# Carousels (Phase 2.21) — prompt assembly, parsing, orchestration
# ---------------------------------------------------------------------------

_CAROUSEL_JSON = (
    '{"caption": "Most people get morning light wrong. Here is the fix. Swipe.", '
    '"hook": "Morning light beats most supplements", '
    '"points": ["Get sun in your eyes within 30 min of waking", '
    '"It anchors your circadian clock for the day", "Free, and it works better than a pill"], '
    '"cta": "I build in public at lubot.ai", "hashtags": ["#Biohacking", "#Health"]}'
)


class TestCarouselPrompts:
    def test_system_prompt_keeps_voice_and_adds_carousel_format(self):
        p = build_carousel_system_prompt()
        assert "Lubo Bali" in p  # reuses the full voice base
        assert "CAROUSEL MODE" in p and '"points"' in p  # carousel override + JSON shape
        assert "NEVER uses apostrophes" in p  # ESL rule carried over

    def test_user_prompt_reuses_material_and_overrides_ask(self):
        u = build_carousel_user_prompt(topic_name="Biohacker", topic_description="health", articles=SAMPLE_ARTICLES)
        assert "Biohacker" in u  # topic material reused
        assert "CAROUSEL" in u  # closing ask overridden


class TestParseCarousel:
    def test_parses_valid_carousel(self):
        r = parse_carousel_response(_CAROUSEL_JSON)
        assert isinstance(r, CarouselResult)
        assert r.hook == "Morning light beats most supplements"
        assert len(r.points) == 3
        assert r.cta and r.caption
        assert r.hashtags == ["#Biohacking", "#Health"]

    def test_extracts_json_from_reasoning_noise(self):
        noisy = "Let me think about this...\n\n" + _CAROUSEL_JSON + "\n\nThat should work."
        r = parse_carousel_response(noisy)
        assert r is not None and len(r.points) == 3

    def test_strips_code_fence(self):
        r = parse_carousel_response("```json\n" + _CAROUSEL_JSON + "\n```")
        assert r is not None and r.hook

    def test_rejects_too_few_points(self):
        bad = '{"hook": "x", "points": ["only one"], "cta": "y", "caption": "z"}'
        assert parse_carousel_response(bad) is None

    def test_rejects_missing_hook(self):
        bad = '{"hook": "", "points": ["a", "b", "c"], "cta": "y"}'
        assert parse_carousel_response(bad) is None

    def test_caps_points_at_five(self):
        many = '{"hook": "h", "points": ["1","2","3","4","5","6","7"], "cta": "c", "caption": "cap"}'
        r = parse_carousel_response(many)
        assert r is not None and len(r.points) == 5

    def test_caption_falls_back_to_hook(self):
        no_cap = '{"hook": "the hook", "points": ["a", "b"], "cta": "c"}'
        r = parse_carousel_response(no_cap)
        assert r is not None and r.caption == "the hook"

    def test_unparseable_returns_none(self):
        assert parse_carousel_response("no json here at all") is None


class TestWriteCarousel:
    @pytest.mark.asyncio
    async def test_returns_carousel_result(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_CAROUSEL_JSON))]
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.writer.get_llm_client", return_value=mock_client):
            r = await write_carousel(topic_name="Biohacker", topic_description="health", articles=SAMPLE_ARTICLES)
        assert isinstance(r, CarouselResult)
        assert r.hook and len(r.points) >= 2

    @pytest.mark.asyncio
    async def test_returns_none_when_provider_fails(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))
        with (
            patch("src.writer.get_llm_client", return_value=mock_client),
            patch("src.writer.get_fallback_client", return_value=None),
        ):
            r = await write_carousel(topic_name="Biohacker", topic_description="health", articles=SAMPLE_ARTICLES)
        assert r is None


class TestCarouselFromText:
    """Phase 2.25 — reshape an existing post into a carousel (Convert to carousel button)."""

    @pytest.mark.asyncio
    async def test_reshapes_existing_post(self):
        from src.writer import carousel_from_text

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_CAROUSEL_JSON))]
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        with patch("src.writer.get_llm_client", return_value=mock_client):
            r = await carousel_from_text(
                "Morning light is underrated. Get sun early. It sets your clock. Free and powerful.",
                "Biohacker",
                hashtags=["#Biohacking"],
            )
        assert isinstance(r, CarouselResult)
        assert r.hook and len(r.points) >= 2
        # the prompt must carry the existing post text (reshape, not invent)
        sent = mock_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Morning light is underrated" in sent
        assert "invent nothing new" in sent.lower()
