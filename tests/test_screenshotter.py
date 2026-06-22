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
    _build_stock_html,
    _build_stock_html_lwc,
    _build_wakatime_html,
    _sparkline_svg,
    build_filename,
    take_screenshot,
    take_wakatime_screenshot,
)

# Sample WakaTime stat-card inputs reused across tests
_LANGS = [("Python", "32h 19m", 55.0), ("TypeScript", "15h 57m", 27.0)]
_PROJECTS = [("LuBot", "57h 44m", 99.0)]

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


# ---------------------------------------------------------------------------
# WakaTime stat-card screenshot (Phase 2.75 / 15p)
# ---------------------------------------------------------------------------


class TestBuildWakatimeHtml:
    """Pure HTML builder for the building-in-public stat card."""

    def test_includes_headline_numbers(self):
        html = _build_wakatime_html(
            total_time="58h 33m",
            days_active=7,
            languages=_LANGS,
            projects=_PROJECTS,
            ai_sessions=9,
            ai_prompts=580,
            ai_tokens=924597335,
            ai_cost=2650.57,
            momentum="up 345%",
            date_range="Jun 07 – Jun 13",
        )
        assert "58h 33m" in html
        assert "Python" in html and "TypeScript" in html
        assert "LuBot" in html
        assert "580" in html  # prompts
        assert "Jun 07 – Jun 13" in html

    def test_shows_cost_when_provided(self):
        html = _build_wakatime_html(
            total_time="58h 33m",
            days_active=7,
            languages=_LANGS,
            projects=_PROJECTS,
            ai_sessions=9,
            ai_prompts=580,
            ai_tokens=1000,
            ai_cost=2650.57,
            momentum="",
            date_range="",
        )
        assert "$" in html
        assert "2,65" in html  # cost rendered with a thousands separator (rounded dollars)

    def test_hides_cost_when_none(self):
        html = _build_wakatime_html(
            total_time="58h 33m",
            days_active=7,
            languages=_LANGS,
            projects=_PROJECTS,
            ai_sessions=9,
            ai_prompts=580,
            ai_tokens=1000,
            ai_cost=None,
            momentum="",
            date_range="",
        )
        assert "$" not in html

    def test_momentum_shown_only_when_present(self):
        with_m = _build_wakatime_html(
            total_time="1h",
            days_active=1,
            languages=_LANGS,
            projects=_PROJECTS,
            ai_sessions=1,
            ai_prompts=1,
            ai_tokens=1,
            ai_cost=None,
            momentum="up 345%",
            date_range="",
        )
        without_m = _build_wakatime_html(
            total_time="1h",
            days_active=1,
            languages=_LANGS,
            projects=_PROJECTS,
            ai_sessions=1,
            ai_prompts=1,
            ai_tokens=1,
            ai_cost=None,
            momentum="",
            date_range="",
        )
        assert "up 345%" in with_m
        assert "345%" not in without_m

    def test_escapes_language_names(self):
        html = _build_wakatime_html(
            total_time="1h",
            days_active=1,
            languages=[("<script>", "1h", 100.0)],
            projects=_PROJECTS,
            ai_sessions=1,
            ai_prompts=1,
            ai_tokens=1,
            ai_cost=None,
            momentum="",
            date_range="",
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


def _mock_playwright():
    """Build a fully-mocked async_playwright context returning a page that screenshots bytes."""
    mock_page = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"x" * 50000)
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


class TestTakeWakatimeScreenshot:
    """Thin Playwright wrapper around the stat-card HTML."""

    @pytest.mark.asyncio
    async def test_returns_screenshot_result(self):
        with (
            patch("src.screenshotter.async_playwright") as mock_apw,
            patch("src.screenshotter.SCREENSHOT_DIR", Path("/tmp/pub-screenshots-test")),
        ):
            mock_apw.return_value = _mock_playwright()
            result = await take_wakatime_screenshot(
                total_time="58h 33m",
                days_active=7,
                languages=_LANGS,
                projects=_PROJECTS,
                ai_sessions=9,
                ai_prompts=580,
                ai_tokens=924597335,
                ai_cost=2650.57,
                momentum="up 345%",
                date_range="Jun 07 – Jun 13",
            )
        assert isinstance(result, ScreenshotResult)
        assert result.path.endswith(".png")
        assert result.width == SCREENSHOT_WIDTH
        assert result.height == SCREENSHOT_HEIGHT

    @pytest.mark.asyncio
    async def test_returns_none_on_playwright_error(self):
        with patch("src.screenshotter.async_playwright", side_effect=RuntimeError("no browser")):
            result = await take_wakatime_screenshot(
                total_time="1h",
                days_active=1,
                languages=_LANGS,
                projects=_PROJECTS,
                ai_sessions=1,
                ai_prompts=1,
                ai_tokens=1,
                ai_cost=None,
                momentum="",
                date_range="",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Stock Talk market card (Phase 2.10)
# ---------------------------------------------------------------------------

_INDICES = [
    {"name": "S&P 500", "last_close": 7503.45, "pct": 1.0, "closes": [7400.0, 7460.0, 7503.45]},
    {"name": "Nasdaq", "last_close": 26465.46, "pct": 2.2, "closes": [25900.0, 26200.0, 26465.46]},
    {"name": "Dow Jones", "last_close": 51680.02, "pct": -0.8, "closes": [52100.0, 51900.0, 51680.02]},
]


class TestSparklineSvg:
    def test_empty_for_too_few_points(self):
        assert _sparkline_svg([7400.0]) == ""

    def test_has_polyline_for_valid_series(self):
        svg = _sparkline_svg([1.0, 2.0, 3.0])
        assert "<polyline" in svg and "<svg" in svg

    def test_green_when_up_red_when_down(self):
        assert "#a6e3a1" in _sparkline_svg([1.0, 2.0, 3.0])  # up -> green
        assert "#f38ba8" in _sparkline_svg([3.0, 2.0, 1.0])  # down -> red


class TestBuildStockHtml:
    def test_contains_index_names_and_closes(self):
        html = _build_stock_html(_INDICES, "2026-06-15 to 2026-06-18")
        assert "S&amp;P 500" in html  # html-escaped
        assert "7,503.45" in html
        assert "26,465.46" in html

    def test_shows_signed_percentages(self):
        html = _build_stock_html(_INDICES, "range")
        assert "+1.0%" in html
        assert "-0.8%" in html  # Dow down

    def test_renders_a_chart_per_index(self):
        html = _build_stock_html(_INDICES, "range")
        assert html.count("<polyline") == 3

    def test_date_range_present(self):
        assert "2026-06-15 to 2026-06-18" in _build_stock_html(_INDICES, "2026-06-15 to 2026-06-18")

    def test_uses_logo_img_when_provided(self):
        html = _build_stock_html(_INDICES, "range", logo_uri="data:image/png;base64,XYZ")
        assert 'class="logo"' in html and "data:image/png;base64,XYZ" in html

    def test_falls_back_to_wordmark_without_logo(self):
        html = _build_stock_html(_INDICES, "range")
        assert "wordmark" in html


class TestBuildStockHtmlLwc:
    def test_attribution_logo_disabled(self):
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="/*lib*/")
        assert "attributionLogo: false" in html

    def test_has_text_credit(self):
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="/*lib*/")
        assert "TradingView Lightweight Charts" in html

    def test_has_chart_container_per_index(self):
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="/*lib*/")
        assert 'id="chart_0"' in html and 'id="chart_1"' in html and 'id="chart_2"' in html

    def test_injects_lib_and_data(self):
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="/*MYLIB*/")
        assert "/*MYLIB*/" in html
        assert "7503.45" in html  # close value present in injected DATA json

    def test_shows_levels_and_signed_pct(self):
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="x")
        assert "7,503.45" in html
        assert "+1.0%" in html and "-0.8%" in html

    def test_sets_explicit_locale(self):
        # The container chromium has no system locale; without an explicit one,
        # Lightweight Charts throws "Incorrect locale information" and draws nothing.
        html = _build_stock_html_lwc(_INDICES, "range", lib_js="x")
        assert "locale: 'en-US'" in html
