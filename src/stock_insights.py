"""Stock Insights — pull real market data via yfinance, build a weekly market pulse.

Reads recent index closes from yfinance and converts a week of market activity
into ScrapedArticle format so the pipeline can generate "Market Pulse" Stock Talk
posts grounded in REAL numbers — index levels, weekly % moves, the standout
mover. This is the truthful backbone for Stock Talk, exactly like git_insights
and wakatime_insights are for the build categories.

NOT financial advice — just real numbers + Lubo's take (enforced in the writer).
The yfinance call lives in one method (_fetch_closes) so tests mock the network
boundary while the aggregation/formatting stays pure and fully tested.
"""

import logging
from dataclasses import dataclass, field

from src.observability import get_client, observe
from src.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

# Indices tracked for the weekly market pulse (yfinance symbol -> display name).
DEFAULT_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
}
DEFAULT_PERIOD = "5d"  # roughly one trading week


@dataclass
class IndexStat:
    """One index's week: latest close, week-over-week move, and daily closes (for charts)."""

    symbol: str
    name: str
    last_close: float
    week_change_pct: float
    closes: list[float] = field(default_factory=list)


@dataclass
class MarketWeek:
    """A week of market activity across tracked indices — the writer's source of truth."""

    indices: list[IndexStat] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""

    @property
    def best(self) -> IndexStat | None:
        return max(self.indices, key=lambda i: i.week_change_pct) if self.indices else None

    @property
    def worst(self) -> IndexStat | None:
        return min(self.indices, key=lambda i: i.week_change_pct) if self.indices else None


def _pct_change(closes: list[float]) -> float:
    """Percent change from the first to the last close. 0.0 if first is zero/missing."""
    if len(closes) < 2 or not closes[0]:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0] * 100


def _fmt_pct(pct: float) -> str:
    """Format a percent move with an explicit sign: +1.2% / -0.8%."""
    return f"{'+' if pct >= 0 else ''}{pct:.1f}%"


def _build_index_stat(symbol: str, name: str, closes: list[float]) -> IndexStat | None:
    """Build an IndexStat from a list of daily closes. None if not enough data."""
    if len(closes) < 2:
        return None
    return IndexStat(
        symbol=symbol,
        name=name,
        last_close=round(closes[-1], 2),
        week_change_pct=round(_pct_change(closes), 2),
        closes=[round(c, 2) for c in closes],
    )


def _build_title(week: MarketWeek) -> str:
    """Title anchored on the S&P 500 (or the first index if S&P is missing)."""
    lead = next((i for i in week.indices if i.symbol == "^GSPC"), None) or week.indices[0]
    return f"Market week: {lead.name} closed {_fmt_pct(lead.week_change_pct)}"


def _build_summary(week: MarketWeek) -> str:
    """Real-numbers summary for the writer (anti-hallucination + no-advice)."""
    parts = [f"THIS WEEK IN THE MARKET ({week.start_date} to {week.end_date}):"]
    for i in week.indices:
        parts.append(f"  - {i.name}: closed {i.last_close:,.2f}, {_fmt_pct(i.week_change_pct)} on the week")

    best, worst = week.best, week.worst
    if best and worst and best.symbol != worst.symbol:
        parts += [
            "",
            f"Strongest: {best.name} {_fmt_pct(best.week_change_pct)}. "
            f"Weakest: {worst.name} {_fmt_pct(worst.week_change_pct)}.",
        ]

    parts += [
        "",
        "IMPORTANT: These are REAL closing levels and weekly % moves from market data. "
        "Use them EXACTLY as given. Do NOT invent any other number, price, or percentage. "
        "This is NOT financial advice — no buy/sell calls, no predictions, no price targets. "
        "Write a calm, data-driven take as someone who built an AI stock advisor.",
    ]
    return "\n".join(parts)


def build_stock_screenshot_fields(week: MarketWeek) -> dict:
    """Adapt a MarketWeek into kwargs for take_stock_screenshot (keeps formatting DRY)."""
    return {
        "indices": [
            {"name": i.name, "last_close": i.last_close, "pct": i.week_change_pct, "closes": i.closes}
            for i in week.indices
        ],
        "date_range": f"{week.start_date} to {week.end_date}",
    }


class StockInsights:
    """Pulls real index data from yfinance and builds a weekly market-pulse article."""

    def __init__(
        self,
        indices: dict[str, str] | None = None,
        period: str = DEFAULT_PERIOD,
    ):
        self.indices = indices or DEFAULT_INDICES
        self.period = period
        self.market_week: MarketWeek | None = None

    @observe()
    def get_market_pulse(self) -> ScrapedArticle | None:
        """Fetch the week's index closes and return a market-pulse ScrapedArticle.

        Returns None if the market data can't be fetched (caller falls back to a
        scraped finance article so the post still generates).
        """
        try:
            closes_by_symbol, start_date, end_date = self._fetch_closes()
        except Exception as e:
            logger.warning("yfinance fetch for market pulse failed: %s", e)
            return None

        indices: list[IndexStat] = []
        for symbol, name in self.indices.items():
            stat = _build_index_stat(symbol, name, closes_by_symbol.get(symbol, []))
            if stat:
                indices.append(stat)

        if not indices:
            logger.info("No usable market data for the weekly pulse")
            return None

        week = MarketWeek(indices=indices, start_date=start_date, end_date=end_date)
        self.market_week = week

        try:
            get_client().score_current_trace(
                name="market_data_quality",
                value=min(1.0, len(indices) / len(self.indices)),
                data_type="NUMERIC",
                comment=f"{len(indices)}/{len(self.indices)} indices fetched",
            )
        except Exception:
            logger.debug("Langfuse market_data_quality scoring failed", exc_info=True)

        return ScrapedArticle(
            title=_build_title(week),
            url="",  # no source webpage — image is a rendered Card B, never a URL screenshot
            summary=_build_summary(week),
            source="stock:market",
            published_at=None,
            source_priority=0,  # own real data = top priority
        )

    def _fetch_closes(self) -> tuple[dict[str, list[float]], str, str]:
        """Fetch daily closes per index from yfinance. The ONLY network boundary.

        Returns ({symbol: [closes...]}, start_date, end_date). yfinance is imported
        lazily so the dependency is only touched when a stock post is generated.
        """
        import yfinance as yf

        symbols = list(self.indices.keys())
        df = yf.download(symbols, period=self.period, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}, "", ""

        close = df["Close"]
        out: dict[str, list[float]] = {}
        for symbol in symbols:
            try:
                series = close[symbol] if hasattr(close, "columns") else close
                values = [float(x) for x in series.dropna().tolist()]
            except Exception:
                values = []
            if values:
                out[symbol] = values

        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        return out, (dates[0] if dates else ""), (dates[-1] if dates else "")
