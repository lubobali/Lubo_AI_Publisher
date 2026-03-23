"""Tests for AI image generator — NVIDIA Stable Diffusion XL fallback images."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.image_generator import GeneratedImage, build_image_prompt, generate_image

# ---------------------------------------------------------------------------
# build_image_prompt — topic-specific prompts
# ---------------------------------------------------------------------------


class TestBuildImagePrompt:
    def test_returns_string(self):
        prompt = build_image_prompt("ai_news", "New AI model released")
        assert isinstance(prompt, str)
        assert len(prompt) > 20

    def test_includes_topic_title(self):
        prompt = build_image_prompt("ai_news", "NVIDIA breaks records")
        assert "NVIDIA breaks records" in prompt

    def test_ai_news_has_neural_network_style(self):
        prompt = build_image_prompt("ai_news", "test")
        assert "neural network" in prompt.lower()

    def test_biohacker_has_science_style(self):
        prompt = build_image_prompt("biohacker", "test")
        assert "lab" in prompt.lower() or "health" in prompt.lower() or "scientific" in prompt.lower()

    def test_tech_talk_has_code_style(self):
        prompt = build_image_prompt("tech_talk", "test")
        assert "code" in prompt.lower() or "terminal" in prompt.lower()

    def test_unknown_category_uses_default(self):
        prompt = build_image_prompt("unknown_category", "test")
        assert "technology" in prompt.lower()

    def test_truncates_long_titles(self):
        long_title = "A" * 200
        prompt = build_image_prompt("ai_news", long_title)
        # Should not include the full 200 char title
        assert len(prompt) < 300

    def test_includes_post_text_context(self):
        prompt = build_image_prompt(
            "ai_news",
            "NVIDIA GPU Launch",
            post_text="Just tested the new NVIDIA H200 GPU. 3x faster inference than H100.",
        )
        assert "NVIDIA" in prompt
        assert "GPU" in prompt or "inference" in prompt


# ---------------------------------------------------------------------------
# generate_image — NVIDIA API (mocked)
# ---------------------------------------------------------------------------


class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_successful_generation(self, tmp_path):
        fake_image = b"\x89PNG fake image bytes"
        fake_b64 = base64.b64encode(fake_image).decode()

        mock_response = MagicMock()
        mock_response.json.return_value = {"artifacts": [{"base64": fake_b64}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("src.image_generator.httpx.AsyncClient", return_value=mock_client),
            patch("src.image_generator.IMAGE_DIR", tmp_path),
            patch.dict("os.environ", {"NVIDIA_API_KEY": "test-key"}),
        ):
            result = await generate_image("ai_news", "Test AI Article", post_text="A great post about AI")

        assert result is not None
        assert isinstance(result, GeneratedImage)
        assert "generated-ai_news" in result.path
        assert result.prompt  # has a prompt

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        with patch.dict("os.environ", {"NVIDIA_API_KEY": ""}, clear=False):
            result = await generate_image("ai_news", "Test")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("API down"))

        with (
            patch("src.image_generator.httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"NVIDIA_API_KEY": "test-key"}),
        ):
            result = await generate_image("ai_news", "Test")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_artifacts_returns_none(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"artifacts": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("src.image_generator.httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"NVIDIA_API_KEY": "test-key"}),
        ):
            result = await generate_image("ai_news", "Test")

        assert result is None
