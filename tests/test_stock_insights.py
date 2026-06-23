"""Tests for stock_insights — yfinance market data -> weekly market-pulse article."""

from unittest.mock import patch

from src.scraper import ScrapedArticle
from src.stock_insights import (
    DEFAULT_INDICES,
    SYMBOL_MAP,
    MarketWeek,
    StockInsights,
    _build_index_stat,
    _build_summary,
    _build_title,
    _fmt_pct,
    _pct_change,
    select_chart_symbols,
)


class TestSelectChartSymbols:
    """C1: pick real yfinance symbols matching the podcast theme (keyword match)."""

    def test_empty_or_none_defaults_to_indices(self):
        assert select_chart_symbols("") == DEFAULT_INDICES
        assert select_chart_symbols(None) == DEFAULT_INDICES

    def test_no_theme_match_defaults_to_indices(self):
        assert select_chart_symbols("- the hosts chatted about weekend plans") == DEFAULT_INDICES

    def test_oil_theme_leads_crude_sp_as_context(self):
        syms = select_chart_symbols("- oil prices ignored the geopolitical risk this week")
        assert next(iter(syms)) == "CL=F"  # the theme instrument LEADS
        assert syms["^GSPC"] == "S&P 500"  # S&P present as context
        assert list(syms).index("^GSPC") == len(syms) - 1  # ...and it is LAST
        assert len(syms) <= 3

    def test_real_episode_themes_lead_with_semis(self):
        # the actual Animal Spirits EP.469 distillation: AI surge, semis, EM, oil
        bullets = (
            "- the AI surge may end in a crash or pullback\n"
            "- semiconductor demand from AI is boosting emerging markets\n"
            "- oil prices ignore geopolitical alarm bells"
        )
        syms = select_chart_symbols(bullets)
        assert next(iter(syms)) == "SMH"  # semiconductors leads, NOT S&P
        assert syms["^GSPC"] == "S&P 500"  # S&P last for context
        assert len(syms) == 3

    def test_caps_at_three(self):
        syms = select_chart_symbols("oil gold semis emerging markets bonds vix dollar small caps")
        assert len(syms) == 3
        assert "^GSPC" in syms  # 2 theme instruments + S&P context

    def test_only_returns_curated_real_symbols(self):
        valid = {"^GSPC"} | {sym for sym, _name, _kw in SYMBOL_MAP} | set(DEFAULT_INDICES)
        assert set(select_chart_symbols("semis and oil and rates and gold")) <= valid

    def test_sp_is_context_last_not_lead(self):
        syms = select_chart_symbols("- gold had a strong week")
        assert next(iter(syms)) == "GC=F"  # theme leads
        assert list(syms)[-1] == "^GSPC"  # S&P is the context panel, last

    def test_ai_theme_charts_semis_not_nasdaq(self):
        # AI/tech talk should surface Semis (the tradeable AI proxy), NOT another index
        syms = select_chart_symbols("- the AI trade keeps powering the market this week")
        assert "SMH" in syms
        assert "^IXIC" not in syms  # Nasdaq demoted off the AI keyword

    def test_specific_instruments_beat_broad_indices(self):
        # gold + oil should win the slots over an explicit nasdaq mention (indices last)
        syms = select_chart_symbols("- gold and oil ran while nasdaq drifted")
        assert "GC=F" in syms and "CL=F" in syms
        assert "^IXIC" not in syms  # capped: 2 specific instruments + S&P


# A realistic week of closes (mocked yfinance output: {symbol: [daily closes]})
CLOSES = {
    "^GSPC": [7400.0, 7420.0, 7460.0, 7503.54],  # +1.40%
    "^IXIC": [26000.0, 26100.0, 26300.0, 26456.47],  # +1.76%
    "^DJI": [44000.0, 43950.0, 44010.0, 44050.0],  # +0.11%
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPctChange:
    def test_positive(self):
        assert round(_pct_change([100.0, 110.0]), 1) == 10.0

    def test_negative(self):
        assert round(_pct_change([100.0, 90.0]), 1) == -10.0

    def test_too_few_points(self):
        assert _pct_change([100.0]) == 0.0

    def test_zero_first(self):
        assert _pct_change([0.0, 50.0]) == 0.0


class TestFmtPct:
    def test_positive_has_plus(self):
        assert _fmt_pct(1.23) == "+1.2%"

    def test_negative_has_minus(self):
        assert _fmt_pct(-0.85) == "-0.8%"


class TestBuildIndexStat:
    def test_builds_stat(self):
        s = _build_index_stat("^GSPC", "S&P 500", CLOSES["^GSPC"])
        assert s is not None
        assert s.name == "S&P 500"
        assert s.last_close == 7503.54
        assert round(s.week_change_pct, 1) == 1.4
        assert s.closes[-1] == 7503.54

    def test_none_when_too_few_closes(self):
        assert _build_index_stat("^GSPC", "S&P 500", [7400.0]) is None

    def test_attaches_real_ohlc_and_volume(self):
        ohlcv = {"ohlc": [[10, 11, 9, 10], [10, 12, 10, 11], [11, 13, 11, 12]], "volume": [5, 6, 7]}
        stat = _build_index_stat("X", "X", [10, 11, 12], ohlcv)
        assert stat.ohlc[-1] == [11, 13, 11, 12]
        assert stat.volume == [5.0, 6.0, 7.0]

    def test_ohlc_defaults_empty_without_data(self):
        stat = _build_index_stat("X", "X", [10, 11, 12])
        assert stat.ohlc == [] and stat.volume == []


class TestBuildSummaryAndTitle:
    def _week(self):
        stats = [_build_index_stat(s, StockInsights().indices[s], c) for s, c in CLOSES.items()]
        return MarketWeek(indices=[s for s in stats if s], start_date="2026-06-15", end_date="2026-06-18")

    def test_summary_has_real_numbers(self):
        text = _build_summary(self._week())
        assert "7,503.54" in text
        assert "+1.4%" in text  # S&P weekly move
        assert "2026-06-15 to 2026-06-18" in text

    def test_summary_forbids_advice_and_invention(self):
        text = _build_summary(self._week()).lower()
        assert "not financial advice" in text
        assert "do not invent" in text

    def test_summary_calls_out_best_and_worst(self):
        text = _build_summary(self._week())
        assert "Strongest: Nasdaq" in text
        assert "Weakest: Dow Jones" in text

    def test_title_anchors_on_sp500(self):
        assert _build_title(self._week()).startswith("Market week: S&P 500")

    def test_best_worst_properties(self):
        w = self._week()
        assert w.best.name == "Nasdaq"
        assert w.worst.name == "Dow Jones"


# ---------------------------------------------------------------------------
# get_market_pulse (yfinance boundary mocked)
# ---------------------------------------------------------------------------


class TestGetMarketPulse:
    @patch.object(StockInsights, "_fetch_closes")
    def test_returns_scraped_article(self, mock_fetch):
        mock_fetch.return_value = (CLOSES, "2026-06-15", "2026-06-18")
        art = StockInsights().get_market_pulse()
        assert isinstance(art, ScrapedArticle)
        assert art.source == "stock:market"
        assert art.source_priority == 0
        assert "S&P 500" in art.title

    @patch.object(StockInsights, "_fetch_closes")
    def test_article_summary_has_exact_numbers(self, mock_fetch):
        mock_fetch.return_value = (CLOSES, "2026-06-15", "2026-06-18")
        art = StockInsights().get_market_pulse()
        assert "7,503.54" in art.summary
        assert "26,456.47" in art.summary

    @patch.object(StockInsights, "_fetch_closes")
    def test_sets_market_week_for_screenshot(self, mock_fetch):
        mock_fetch.return_value = (CLOSES, "2026-06-15", "2026-06-18")
        si = StockInsights()
        si.get_market_pulse()
        assert si.market_week is not None
        assert len(si.market_week.indices) == 3

    @patch.object(StockInsights, "_fetch_closes")
    def test_none_when_no_data(self, mock_fetch):
        mock_fetch.return_value = ({}, "", "")
        assert StockInsights().get_market_pulse() is None

    @patch.object(StockInsights, "_fetch_closes")
    def test_none_on_fetch_exception(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("yfinance down")
        assert StockInsights().get_market_pulse() is None

    @patch.object(StockInsights, "_fetch_closes")
    def test_skips_indices_with_insufficient_data(self, mock_fetch):
        mock_fetch.return_value = ({"^GSPC": CLOSES["^GSPC"], "^IXIC": [26000.0]}, "2026-06-15", "2026-06-18")
        art = StockInsights().get_market_pulse()
        assert art is not None
        assert "S&P 500" in art.summary
        assert "Nasdaq" not in art.summary  # only 1 close -> dropped


class TestBuildStockScreenshotFields:
    def test_maps_market_week_to_card_kwargs(self):
        from src.stock_insights import build_stock_screenshot_fields

        stats = [_build_index_stat(s, StockInsights().indices[s], c) for s, c in CLOSES.items()]
        week = MarketWeek(indices=[s for s in stats if s], start_date="2026-06-15", end_date="2026-06-18")
        fields = build_stock_screenshot_fields(week)
        assert fields["date_range"] == "2026-06-15 to 2026-06-18"
        assert len(fields["indices"]) == 3
        first = fields["indices"][0]
        assert {"name", "last_close", "pct", "closes", "ohlc", "volume"} <= set(first)
        assert first["name"] == "S&P 500"
