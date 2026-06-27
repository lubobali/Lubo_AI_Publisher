"""Tests for Market Pulse luxury card layouts (Phase 2.10e) — pure builders + rotation.

The real render is verified live; here we assert each builder emits valid HTML with the
data + injected engine lib, the rotation cycles, and the headline numbers match the
series exactly (the chart<->post integrity contract).
"""

import pytest

from src import cards

SERIES = [
    {
        "name": "Semiconductors",
        "last_close": 659.88,
        "pct": 6.4,
        "closes": [600, 620, 640, 659.88],
        "ohlc": [[600, 605, 598, 603], [603, 622, 601, 620], [620, 645, 618, 640], [640, 662, 638, 659.88]],
        "volume": [10, 12, 9, 15],
    },
    {"name": "S&P 500", "last_close": 7500.58, "pct": 0.9, "closes": [7450, 7480, 7510, 7500.58]},
    {"name": "Crude Oil", "last_close": 75.07, "pct": -7.0, "closes": [81, 79, 77, 75.07]},
]


class TestLayoutBuilders:
    @pytest.mark.parametrize("layout", cards.LAYOUTS, ids=[layout["name"] for layout in cards.LAYOUTS])
    def test_builder_emits_html_with_lib(self, layout):
        html = layout["builder"](SERIES, "2026-05-19 to 2026-06-18", cards.PALETTES[0], "/*ENGINE_LIB*/")
        assert "<html" in html
        assert "/*ENGINE_LIB*/" in html  # the engine lib is injected
        assert "Market Pulse" in html

    def test_candlestick_handles_missing_ohlc(self):
        s = [{"name": "X", "last_close": 100.0, "pct": 1.0, "closes": [98, 99, 100]}]
        html = cards.build_candlestick_pro(s, "r", cards.PALETTES[1], "/*LIB*/")
        assert "addCandlestickSeries" in html  # renders even without real OHLC (derived)


class TestHeadlineCard:
    """Phase 2.12 A: branded headline card for ai_news (no third-party screenshots)."""

    def test_emits_html_with_headline_source_and_kicker(self):
        html = cards.build_headline_card(
            "How agents are transforming work", source="openai.com", date_range="2026-06-25"
        )
        assert "<html" in html
        assert "How agents are transforming work" in html
        assert "openai.com" in html
        assert "AI News" in html  # the kicker

    def test_escapes_headline(self):
        html = cards.build_headline_card("Tools & <agents> win", source="x.com")
        assert "&amp;" in html and "&lt;agents&gt;" in html
        assert "<agents>" not in html  # not raw HTML

    def test_optional_dek_included(self):
        html = cards.build_headline_card("Title", source="x.com", dek="A short summary line.")
        assert "A short summary line." in html

    def test_works_with_only_a_headline(self):
        html = cards.build_headline_card("Just a title")
        assert "<html" in html and "Just a title" in html


class TestDesignSystemFoundation:
    """Phase 2.16 E1: embedded fonts + brand palette + deterministic texture helpers."""

    def test_font_css_embeds_fonts(self):
        css = cards._font_css()
        assert "@font-face" in css
        assert "Fraunces" in css and "Grotesk" in css
        assert "data:font/woff2;base64," in css

    def test_font_css_cached_and_deterministic(self):
        assert cards._font_css() == cards._font_css()

    def test_brand_palette_core_keys(self):
        for key in ("blue", "blue_dk", "steel", "accent", "bg", "text", "headline", "footer"):
            assert key in cards.BRAND
        assert cards.BRAND["blue"] == "#4f8cf0"  # logo blue, not gold

    def test_grain_deterministic_fixed_seed(self):
        first, second = cards._grain(), cards._grain()
        assert first == second  # no randomness
        assert "seed='11'" in first
        assert "mix-blend-mode:overlay" in first

    def test_grain_opacity_param(self):
        assert "opacity:0.12" in cards._grain(0.12)

    def test_vignette_is_inset_overlay(self):
        assert "inset" in cards._vignette()


class TestUniversalFrame:
    """Phase 2.16 E2: the constant frame chrome + signature."""

    def test_signature_is_blue_wordmark_with_dash(self):
        sig = cards._signature()
        assert "Lubo Bali" in sig
        assert cards.BRAND["blue"] in sig
        assert "<svg" in sig  # the front dash
        assert "Grotesk" in sig

    def test_frame_has_chrome_and_body(self):
        html = cards._frame(
            kicker="Investing Principle",
            body="<div id='interior'>BODY</div>",
            disclaimer="Not financial advice · LuBot",
            folio="No. 27 · June 27, 2026",
            logo_uri="data:image/png;base64,AAAA",
        )
        assert "<html" in html
        assert "Investing Principle" in html  # kicker
        assert "BODY" in html  # the interior slot
        assert "No. 27 · June 27, 2026" in html  # folio
        assert "Not financial advice · LuBot" in html  # disclaimer
        assert "@font-face" in html  # fonts embedded
        assert "data:image/png;base64,AAAA" in html  # logo
        assert cards.BRAND["accent"] in html  # accent rail/kicker

    def test_frame_escapes_kicker_and_disclaimer(self):
        html = cards._frame(kicker="A & B", body="x", disclaimer="<n>", folio="")
        assert "A &amp; B" in html and "&lt;n&gt;" in html

    def test_frame_injects_chart_engine_and_script(self):
        html = cards._frame(
            kicker="Market Pulse", body="<div id='c'></div>", disclaimer="d", lib_js="/*ENGINE*/", script="/*SETUP*/"
        )
        assert "/*ENGINE*/" in html and "/*SETUP*/" in html


class TestInsightCard:
    """Phase 2.12 A: editorial pull-quote card for opinion categories (no screenshots)."""

    def test_emits_html_with_quote_kicker_and_attribution(self):
        html = cards.build_insight_card(
            "Most AI demos die in production because nobody owns the boring parts",
            kicker="Tech Talk",
            date_range="2026-06-26",
        )
        assert "<html" in html
        assert "Most AI demos die in production" in html
        assert "Tech Talk" in html  # kicker
        assert "Lubo Bali" in html  # default attribution

    def test_escapes_headline(self):
        html = cards.build_insight_card("Risk & <reward> are not the same", kicker="Investing Principle")
        assert "&amp;" in html and "&lt;reward&gt;" in html
        assert "<reward>" not in html

    def test_attribution_optional(self):
        html = cards.build_insight_card("Sleep is the cheapest performance drug", attribution="")
        assert "Lubo Bali" not in html
        assert "Sleep is the cheapest performance drug" in html


class TestSelectCardLayout:
    def test_rotates_through_all(self):
        names = [cards.select_card_layout(i)["name"] for i in range(len(cards.LAYOUTS))]
        assert len(set(names)) == len(cards.LAYOUTS)

    def test_wraps_around(self):
        assert cards.select_card_layout(0) == cards.select_card_layout(len(cards.LAYOUTS))

    def test_engine_is_valid(self):
        for layout in cards.LAYOUTS:
            assert layout["engine"] in ("echarts", "lwc", "html")

    def test_has_expanded_catalog(self):
        assert len(cards.LAYOUTS) >= 11  # Batch B expanded the catalog
        assert len({layout["name"] for layout in cards.LAYOUTS}) == len(cards.LAYOUTS)  # unique names


class TestNumberIntegrity:
    """Card numbers MUST equal the series values, identically formatted (Lubo's hard req)."""

    def test_candlestick_headline_matches_series(self):
        html = cards.build_candlestick_pro(SERIES, "r", cards.PALETTES[1], "/*LIB*/")
        assert "659.88" in html  # last_close, 2 decimals
        assert "+6.4%" in html  # pct, 1 decimal, signed — same as the summary format

    def test_pct_values_present_for_all_layouts(self):
        # every layout embeds the real series pct (6.4) — never a different number
        for layout in cards.LAYOUTS:
            html = layout["builder"](SERIES, "r", cards.PALETTES[0], "/*LIB*/")
            assert "6.4" in html
