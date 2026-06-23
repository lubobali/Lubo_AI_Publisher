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
