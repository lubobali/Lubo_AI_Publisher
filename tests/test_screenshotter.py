"""Tests for Playwright screenshotter — URL capture at LinkedIn-optimal size."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.screenshotter import (
    MIN_SCREENSHOT_BYTES,
    SCREENSHOT_DIR,
    SCREENSHOT_HEIGHT,
    SCREENSHOT_WIDTH,
    ScreenshotResult,
    build_filename,
    take_screenshot,
)

# ---------------------------------------------------------------------------
# ScreenshotResult dataclass
# ---------------------------------------------------------------------------


class TestScreenshotResult:
    def test_create_result(self):
        result = ScreenshotResult(
            path="/tmp/screenshot.png",
            url="https://example.com",
            width=1200,
            height=627,
        )
        assert result.path == "/tmp/screenshot.png"
        assert result.url == "https://example.com"
        assert result.width == 1200
        assert result.height == 627

    def test_result_fields(self):
        result = ScreenshotResult(
            path="/tmp/test.png",
            url="https://example.com",
            width=1200,
            height=627,
        )
        assert isinstance(result.path, str)
        assert isinstance(result.url, str)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_screenshot_dimensions(self):
        assert SCREENSHOT_WIDTH == 1200
        assert SCREENSHOT_HEIGHT == 627

    def test_screenshot_dir_is_path(self):
        assert isinstance(SCREENSHOT_DIR, Path)


# ---------------------------------------------------------------------------
# Filename builder
# ---------------------------------------------------------------------------


class TestBuildFilename:
    def test_filename_contains_timestamp(self):
        name = build_filename("https://techcrunch.com/article/ai-news")
        assert name.endswith(".png")
        # Should have date-like pattern
        assert "2026" in name or "20" in name

    def test_filename_contains_domain(self):
        name = build_filename("https://techcrunch.com/article/ai-news")
        assert "techcrunch" in name.lower()

    def test_filename_sanitizes_special_chars(self):
        name = build_filename("https://example.com/path?q=hello&x=1")
        # No slashes, question marks, or ampersands in filename
        assert "/" not in name
        assert "?" not in name
        assert "&" not in name

    def test_filename_unique_per_call(self):
        name1 = build_filename("https://example.com/a")
        name2 = build_filename("https://example.com/b")
        assert name1 != name2


# ---------------------------------------------------------------------------
# take_screenshot — main function (all Playwright mocked)
# ---------------------------------------------------------------------------


class TestTakeScreenshot:
    @pytest.mark.asyncio
    async def test_returns_screenshot_result(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            result = await take_screenshot("https://example.com/article")

            assert isinstance(result, ScreenshotResult)
            assert result.url == "https://example.com/article"
            assert result.width == SCREENSHOT_WIDTH
            assert result.height == SCREENSHOT_HEIGHT
            assert result.path.endswith(".png")

    @pytest.mark.asyncio
    async def test_sets_viewport_to_linkedin_dimensions(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://example.com")

            # Verify context was created with correct viewport
            mock_browser.new_context.assert_called_once()
            call_kwargs = mock_browser.new_context.call_args[1]
            assert call_kwargs["viewport"]["width"] == SCREENSHOT_WIDTH
            assert call_kwargs["viewport"]["height"] == SCREENSHOT_HEIGHT

    @pytest.mark.asyncio
    async def test_enables_dark_mode(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://example.com", dark_mode=True)

            call_kwargs = mock_browser.new_context.call_args[1]
            assert call_kwargs["color_scheme"] == "dark"

    @pytest.mark.asyncio
    async def test_light_mode_default(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://example.com", dark_mode=False)

            call_kwargs = mock_browser.new_context.call_args[1]
            assert call_kwargs["color_scheme"] == "light"

    @pytest.mark.asyncio
    async def test_navigates_to_url(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://techcrunch.com/article")

            mock_page.goto.assert_called_once()
            call_args = mock_page.goto.call_args
            assert call_args[0][0] == "https://techcrunch.com/article"

    @pytest.mark.asyncio
    async def test_saves_screenshot_bytes(self):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 50000
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=fake_png)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        tmp_dir = Path("/tmp/pub-screenshots-test")
        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", tmp_dir),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            result = await take_screenshot("https://example.com")

            # Verify file was written
            saved_path = Path(result.path)
            assert saved_path.exists()
            assert saved_path.read_bytes() == fake_png

            # Cleanup
            saved_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_closes_browser_on_success(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://example.com")

            mock_browser.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_navigation_failure(self):
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            result = await take_screenshot("https://bad-url.example.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_closes_browser_on_failure(self):
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Timeout"))

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://bad-url.example.com")
            mock_browser.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_waits_for_page_load(self):
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            await take_screenshot("https://example.com")

            # Should use domcontentloaded then try networkidle
            call_kwargs = mock_page.goto.call_args[1]
            assert call_kwargs["wait_until"] == "domcontentloaded"
            # Should wait for network + render after navigation
            mock_page.wait_for_load_state.assert_called_once()
            assert mock_page.wait_for_timeout.call_count >= 1

    @pytest.mark.asyncio
    async def test_rejects_tiny_screenshot_as_placeholder(self):
        """Screenshots under MIN_SCREENSHOT_BYTES are likely broken placeholders."""
        tiny_bytes = b"x" * 5000  # 5KB — under threshold

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=tiny_bytes)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            result = await take_screenshot("https://fake-url.com/broken")

        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_large_screenshot(self):
        """Screenshots over MIN_SCREENSHOT_BYTES are real captures."""
        large_bytes = b"x" * 50000  # 50KB — over threshold

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=large_bytes)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_apw.return_value = mock_cm

            result = await take_screenshot("https://example.com/real-page")

        assert result is not None

    def test_min_screenshot_bytes_constant(self):
        assert MIN_SCREENSHOT_BYTES == 10_000
