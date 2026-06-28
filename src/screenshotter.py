"""Playwright screenshot engine — captures web pages at LinkedIn-optimal size."""

import contextlib
import html as html_lib
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from src.observability import get_client, observe

logger = logging.getLogger(__name__)

SCREENSHOT_WIDTH = 1200
SCREENSHOT_HEIGHT = 627
SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
NAVIGATION_TIMEOUT_MS = 30000
MIN_SCREENSHOT_BYTES = 10_000  # 10KB — anything smaller is likely a broken placeholder


@dataclass
class ScreenshotResult:
    """Result of a successful screenshot capture."""

    path: str
    url: str
    width: int
    height: int


def build_filename(url: str) -> str:
    """Build a unique, filesystem-safe filename from a URL."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    # Take the path, strip leading slash, replace non-alphanumeric chars
    path_part = parsed.path.strip("/")
    slug = re.sub(r"[^a-zA-Z0-9]", "-", f"{domain}-{path_part}")
    # Truncate slug to avoid overly long filenames
    slug = slug[:80].rstrip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{slug}.png"


def _report_screenshot_metadata(
    url: str,
    success: bool,
    error_detected: bool,
    failed_reason: str | None,
    screenshot_size_bytes: int,
) -> None:
    """Report screenshot results to Langfuse."""
    try:
        get_client().update_current_span(
            metadata={
                "url": url,
                "success": success,
                "error_detected": error_detected,
                "failed_reason": failed_reason,
                "screenshot_size_bytes": screenshot_size_bytes,
            }
        )
    except Exception:
        logger.debug("Langfuse screenshot reporting failed", exc_info=True)


@observe()
async def take_screenshot(
    url: str,
    dark_mode: bool = True,
    timeout_ms: int = NAVIGATION_TIMEOUT_MS,
) -> ScreenshotResult | None:
    """Navigate to URL and capture a screenshot at LinkedIn-optimal dimensions.

    Returns ScreenshotResult on success, None on failure.
    Browser is always closed, even on error.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                color_scheme="dark" if dark_mode else "light",
                device_scale_factor=1.5,
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Wait for images and assets to load (some sites never reach networkidle)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=10000)
            await page.wait_for_timeout(1000)

            # Detect error pages (400, 403, 404, 503, etc.)
            page_text = await page.evaluate("document.body?.innerText || ''")
            error_signals = [
                "400",
                "403",
                "404",
                "500",
                "502",
                "503",
                "Bad Request",
                "Forbidden",
                "Not Found",
                "Access Denied",
                "blocked by",
                "security policies",
                "something went wrong",
                "Apologies, but",
                "Page not found",
                "Server Error",
                "Internal Server Error",
                "Refresh the page",
            ]
            if any(signal in page_text[:1000] for signal in error_signals) and len(page_text) < 2000:
                logger.warning("Error page detected for %s — skipping", url)
                _report_screenshot_metadata(url, False, True, "error_page_detected", 0)
                return None

            # Remove cookie banners, popups, sticky navs, notification bars
            await page.evaluate("""
                const selectors = [
                    '#cookie-consent', '#gdpr-banner', '#onetrust-consent-sdk',
                    '.cookie-banner', '.cookie-consent', '.consent-banner',
                    '[class*="cookie"]', '[id*="cookie"]', '[id*="consent"]',
                    '.sticky-footer', '.sticky-header',
                    '[class*="popup"]', '[class*="modal"]', '[class*="overlay"]',
                    '[class*="newsletter"]', '[class*="subscribe"]',
                    '[class*="banner"]', '[class*="notification"]',
                    '[class*="cta-modal"]', '[class*="bottom-bar"]',
                    '.tp-backdrop', '.tp-modal',
                    '[class*="video-player"]', '[class*="unmute"]',
                    '[class*="ad-"]', '[class*="advert"]',
                    'iframe[src*="ads"]', '[id*="ad-"]',
                ];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                });
                // Also try clicking dismiss/accept buttons
                const buttons = document.querySelectorAll(
                    'button[class*="dismiss"], button[class*="close"], button[class*="accept"]'
                );
                buttons.forEach(b => b.click());
            """)

            # Scroll past nav bar to show article content
            await page.evaluate("window.scrollBy(0, 80)")
            await page.wait_for_timeout(500)

            filename = build_filename(url)
            filepath = SCREENSHOT_DIR / filename

            screenshot_bytes = await page.screenshot(full_page=False)

            # Reject tiny screenshots — likely broken placeholder pages
            if len(screenshot_bytes) < MIN_SCREENSHOT_BYTES:
                logger.warning(
                    "Screenshot too small (%d bytes) for %s — likely placeholder",
                    len(screenshot_bytes),
                    url,
                )
                _report_screenshot_metadata(url, False, True, "screenshot_too_small", len(screenshot_bytes))
                return None

            filepath.write_bytes(screenshot_bytes)

            logger.info("Screenshot saved: %s", filepath)
            _report_screenshot_metadata(url, True, False, None, len(screenshot_bytes))
            return ScreenshotResult(
                path=str(filepath),
                url=url,
                width=SCREENSHOT_WIDTH,
                height=SCREENSHOT_HEIGHT,
            )
        except Exception as e:
            logger.warning("Screenshot failed for %s: %s", url, e)
            _report_screenshot_metadata(url, False, False, str(e), 0)
            return None
        finally:
            await browser.close()


async def take_lubot_screenshot() -> ScreenshotResult | None:
    """Screenshot lubot.ai after clicking Start — SPA needs interaction to render.

    Returns ScreenshotResult on success, None on failure.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    url = "https://lubot.ai"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                color_scheme="dark",
                device_scale_factor=1.5,
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=10000)

            # Click "Start LuBot" to render the actual UI
            with contextlib.suppress(Exception):
                await page.click("text=Start LuBot", timeout=5000)
                await page.wait_for_timeout(3000)  # Let the UI render

            filename = build_filename(url)
            filepath = SCREENSHOT_DIR / filename
            screenshot_bytes = await page.screenshot(full_page=False)

            if len(screenshot_bytes) < MIN_SCREENSHOT_BYTES:
                logger.warning("LuBot screenshot too small (%d bytes)", len(screenshot_bytes))
                return None

            filepath.write_bytes(screenshot_bytes)
            logger.info("LuBot screenshot saved: %s", filepath)
            return ScreenshotResult(
                path=str(filepath),
                url=url,
                width=SCREENSHOT_WIDTH,
                height=SCREENSHOT_HEIGHT,
            )
        except Exception as e:
            logger.warning("LuBot screenshot failed: %s", e)
            return None
        finally:
            await browser.close()


async def take_git_screenshot(
    commit_message: str,
    lines_added: int,
    lines_deleted: int,
    files_changed: int,
    changed_files: list[str],
    commit_hash: str = "",
    commit_date: str = "",
    issue: int | None = None,
) -> ScreenshotResult | None:
    """Render the luxury My Agent Build card (Phase 2.16 E5) from REAL git data — replaces the
    old terminal-style screenshot. Non-fatal: returns None on failure so the caller falls back."""
    from src import cards

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    commit = {
        "message": commit_message,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "files_changed": files_changed or len(changed_files),
        "hash": commit_hash,
    }
    page_html = cards.build_build_card(
        commit,
        date_range=commit_date,
        issue=issue,
        logo_uri=cards._logo_data_uri(),
        brand=cards.topic_brand("my_agent_git"),
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT}, device_scale_factor=2
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(400)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-git-commit.png"
                filepath.write_bytes(await page.screenshot(full_page=False))
                logger.info("My Agent Build card saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath),
                    url=f"card:build:{commit_hash}",
                    width=SCREENSHOT_WIDTH,
                    height=SCREENSHOT_HEIGHT,
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Build card screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _build_wakatime_html(
    total_time: str,
    days_active: int,
    languages: list[tuple[str, str, float]],
    projects: list[tuple[str, str, float]],
    ai_sessions: int,
    ai_prompts: int,
    ai_tokens: int,
    ai_cost: float | None,
    momentum: str,
    date_range: str,
) -> str:
    """Build the building-in-public stat-card HTML. Pure — no I/O, no Playwright."""

    def _rows(items: list[tuple[str, str, float]]) -> str:
        out = ""
        for name, detail, pct in items:
            width = max(2.0, min(100.0, pct))
            out += (
                '<div class="row">'
                f'<div class="rlabel">{html_lib.escape(name)}</div>'
                '<div class="track"><div class="fill" '
                f'style="width:{width:.0f}%"></div></div>'
                f'<div class="rdetail">{html_lib.escape(detail)} · {pct:.0f}%</div>'
                "</div>\n"
            )
        return out

    if momentum:
        up = momentum.strip().lower().startswith("up")
        arrow = "↑" if up else "↓"
        badge_color = "#a6e3a1" if up else "#f38ba8"
        momentum_badge = (
            f'<span class="badge" style="background:{badge_color}">'
            f"{arrow} {html_lib.escape(momentum)} vs last week</span>"
        )
    else:
        momentum_badge = ""
    cost_stat = (
        f'<div class="stat"><div class="snum">${ai_cost:,.0f}</div><div class="slabel">AI agent cost</div></div>'
        if ai_cost is not None
        else ""
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ margin:0; padding:0; background:#1e1e2e;
    font-family:'Inter','Segoe UI','Helvetica Neue',sans-serif; color:#cdd6f4; }}
.card {{ margin:24px; padding:32px 36px; background:#11111b;
    border:1px solid #313244; border-radius:16px; }}
.head {{ display:flex; align-items:center; justify-content:space-between; }}
.kicker {{ color:#89b4fa; font-size:13px; font-weight:700; letter-spacing:2px;
    text-transform:uppercase; }}
.range {{ color:#6c7086; font-size:13px; margin-top:2px; }}
.badge {{ background:#a6e3a1; color:#11111b; font-weight:700; font-size:13px;
    padding:6px 12px; border-radius:999px; }}
.hero {{ margin:20px 0 8px; }}
.htime {{ font-size:52px; font-weight:800; color:#f9e2af; line-height:1; }}
.hsub {{ color:#a6adc8; font-size:15px; margin-top:6px; }}
.section {{ color:#6c7086; font-size:12px; text-transform:uppercase;
    letter-spacing:1px; margin:22px 0 10px; }}
.row {{ display:flex; align-items:center; gap:12px; margin:7px 0; }}
.rlabel {{ width:120px; font-size:14px; color:#cdd6f4; font-weight:600; }}
.track {{ flex:1; height:12px; background:#181825; border-radius:6px; overflow:hidden; }}
.fill {{ height:100%; background:linear-gradient(90deg,#89b4fa,#cba6f7); }}
.rdetail {{ width:150px; text-align:right; font-size:13px; color:#a6adc8; }}
.stats {{ display:flex; gap:28px; margin-top:14px;
    padding-top:18px; border-top:1px solid #313244; }}
.stat {{ }}
.snum {{ font-size:24px; font-weight:800; color:#cba6f7; }}
.slabel {{ font-size:12px; color:#6c7086; margin-top:2px; }}
</style></head><body>
<div class="card">
    <div class="head">
        <div>
            <div class="kicker">Building in Public</div>
            <div class="range">{html_lib.escape(date_range)}</div>
        </div>
        {momentum_badge}
    </div>
    <div class="hero">
        <div class="htime">{html_lib.escape(total_time)}</div>
        <div class="hsub">coded this week · {days_active} active days · built with AI agents</div>
    </div>
    <div class="section">Languages</div>
    {_rows(languages)}
    <div class="section">Projects</div>
    {_rows(projects)}
    <div class="stats">
        <div class="stat"><div class="snum">{ai_sessions}</div>
            <div class="slabel">AI sessions</div></div>
        <div class="stat"><div class="snum">{ai_prompts}</div>
            <div class="slabel">prompts</div></div>
        <div class="stat"><div class="snum">{ai_tokens:,}</div>
            <div class="slabel">input tokens</div></div>
        {cost_stat}
    </div>
</div>
</body></html>"""


async def take_wakatime_screenshot(
    total_time: str,
    days_active: int,
    languages: list[tuple[str, str, float]],
    projects: list[tuple[str, str, float]],
    ai_sessions: int,
    ai_prompts: int,
    ai_tokens: int,
    ai_cost: float | None = None,
    momentum: str = "",
    date_range: str = "",
) -> ScreenshotResult | None:
    """Render the building-in-public stat card to a PNG with Playwright."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    page_html = _build_wakatime_html(
        total_time=total_time,
        days_active=days_active,
        languages=languages,
        projects=projects,
        ai_sessions=ai_sessions,
        ai_prompts=ai_prompts,
        ai_tokens=ai_tokens,
        ai_cost=ai_cost,
        momentum=momentum,
        date_range=date_range,
    )

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                    device_scale_factor=1.5,
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
                await page.wait_for_timeout(500)

                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-wakatime-week.png"
                filepath.write_bytes(await page.screenshot(full_page=False))

                logger.info("WakaTime screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath),
                    url="wakatime:week",
                    width=SCREENSHOT_WIDTH,
                    height=SCREENSHOT_HEIGHT,
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("WakaTime screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Stock Talk market card (Phase 2.10) — a real-looking fintech dashboard rendered
# from live yfinance numbers. Owned + truthful: our render of real data + our logo.
# ---------------------------------------------------------------------------

_LOGO_PATH = Path(__file__).parent.parent / "static" / "assets" / "lubot-logo.png"


def _sparkline_svg(closes: list[float], width: int = 300, height: int = 80) -> str:
    """Build a real line-chart SVG from daily closes. Green if up on the week, else red."""
    if len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    rng = (hi - lo) or 1.0
    n = len(closes)
    pad = 6

    def px(i: int) -> float:
        return pad + i * (width - 2 * pad) / (n - 1)

    def py(v: float) -> float:
        return pad + (height - 2 * pad) * (1 - (v - lo) / rng)

    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(closes))
    up = closes[-1] >= closes[0]
    color = "#a6e3a1" if up else "#f38ba8"
    area = f"{pad:.1f},{height - pad:.1f} {pts} {width - pad:.1f},{height - pad:.1f}"
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'style="width:100%;height:100%;display:block">'
        f'<polygon points="{area}" fill="{color}" opacity="0.10"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def _logo_data_uri() -> str:
    """Read the LuBot logo PNG and return it as a base64 data URI (offline-safe)."""
    try:
        import base64

        return "data:image/png;base64," + base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    except Exception:
        logger.debug("LuBot logo not found at %s", _LOGO_PATH, exc_info=True)
        return ""


def _build_stock_html(indices: list[dict], date_range: str, logo_uri: str = "") -> str:
    """Build the Market Pulse stat-card HTML. Pure — no I/O, no Playwright.

    indices: [{"name": str, "last_close": float, "pct": float, "closes": [float]}]
    """
    cols = ""
    for ix in indices:
        up = ix["pct"] >= 0
        color = "#a6e3a1" if up else "#f38ba8"
        sign = "+" if up else ""
        cols += (
            '<div class="idx">'
            f'<div class="iname">{html_lib.escape(str(ix["name"]))}</div>'
            f'<div class="iclose">{ix["last_close"]:,.2f}</div>'
            f'<div class="ipct" style="color:{color}">{sign}{ix["pct"]:.1f}%</div>'
            f'<div class="chart">{_sparkline_svg(list(ix["closes"]))}</div>'
            "</div>\n"
        )
    brand = f'<img class="logo" src="{logo_uri}"/>' if logo_uri else '<div class="wordmark">LuBot</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ margin:0; padding:0; background:#0b0f17;
    font-family:'Inter','Segoe UI','Helvetica Neue',sans-serif; color:#e6edf3; }}
.card {{ margin:22px; padding:30px 36px; background:#0e1420;
    border:1px solid #1c2333; border-radius:16px;
    display:flex; flex-direction:column; min-height:583px; box-sizing:border-box; }}
.head {{ display:flex; align-items:center; justify-content:space-between; }}
.kicker {{ color:#4ea1ff; font-size:15px; font-weight:700; letter-spacing:2px;
    text-transform:uppercase; }}
.range {{ color:#5b6675; font-size:14px; margin-top:4px; }}
.logo {{ height:56px; width:auto; opacity:0.95; }}
.wordmark {{ color:#4ea1ff; font-weight:800; font-size:24px; letter-spacing:1px; }}
.board {{ display:flex; gap:22px; margin-top:28px; flex:1; }}
.idx {{ flex:1; display:flex; flex-direction:column;
    background:#0b1019; border:1px solid #1c2333; border-radius:12px;
    padding:22px 22px 20px; }}
.iname {{ color:#9aa7b8; font-size:14px; font-weight:700; letter-spacing:1px;
    text-transform:uppercase; }}
.iclose {{ font-size:34px; font-weight:800; color:#f0f6fc; margin-top:10px; line-height:1; }}
.ipct {{ font-size:18px; font-weight:700; margin-top:8px; }}
.chart {{ margin-top:18px; flex:1; min-height:150px; }}
.foot {{ margin-top:22px; padding-top:16px; border-top:1px solid #1c2333;
    color:#5b6675; font-size:13px; letter-spacing:1px; text-transform:uppercase; }}
</style></head><body>
<div class="card">
    <div class="head">
        <div>
            <div class="kicker">Market Pulse</div>
            <div class="range">{html_lib.escape(date_range)}</div>
        </div>
        {brand}
    </div>
    <div class="board">
        {cols}
    </div>
    <div class="foot">Weekly close · real market data</div>
</div>
</body></html>"""


_LWC_PATH = Path(__file__).parent.parent / "static" / "vendor" / "lightweight-charts.js"

# Chart style rotation (Phase 2.10d) — each week's Card B uses a different chart TYPE
# and COLOR palette so consecutive Market Pulse posts look genuinely distinct. All
# types render from the daily closes we already have (no extra data); up/down stays
# semantic. Candlestick is deferred (needs OHLC we don't store).
CHART_STYLES = [
    {"name": "area-teal", "type": "area", "up": "#26a69a", "down": "#ef5350", "accent": "#4ea1ff", "grid": "#161d2b"},
    {"name": "line-amber", "type": "line", "up": "#3b82f6", "down": "#f59e0b", "accent": "#60a5fa", "grid": "#17202e"},
    {
        "name": "baseline-violet",
        "type": "baseline",
        "up": "#a78bfa",
        "down": "#fb7185",
        "accent": "#c084fc",
        "grid": "#1d1a2e",
    },
    {"name": "area-cyan", "type": "area", "up": "#22d3ee", "down": "#f472b6", "accent": "#67e8f9", "grid": "#0f2430"},
    {
        "name": "line-emerald",
        "type": "line",
        "up": "#34d399",
        "down": "#fb923c",
        "accent": "#6ee7b7",
        "grid": "#13251c",
    },
    {
        "name": "baseline-sky",
        "type": "baseline",
        "up": "#38bdf8",
        "down": "#f87171",
        "accent": "#7dd3fc",
        "grid": "#11202e",
    },
]


def select_chart_style(week: int) -> dict:
    """Pick this week's chart style (type + palette), round-robin by week number."""
    return CHART_STYLES[week % len(CHART_STYLES)]


_STOCK_LWC_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body { margin:0; padding:0; background:#0b0f17;
    font-family:'Inter','Segoe UI','Helvetica Neue',sans-serif; color:#e6edf3; }
.card { margin:22px; padding:30px 36px; background:#0e1420;
    border:1px solid #1c2333; border-radius:16px;
    display:flex; flex-direction:column; min-height:583px; box-sizing:border-box; }
.head { display:flex; align-items:center; justify-content:space-between; }
.kicker { color:__ACCENT__; font-size:15px; font-weight:700; letter-spacing:2px; text-transform:uppercase; }
.range { color:#5b6675; font-size:14px; margin-top:4px; }
.logo { height:56px; width:auto; opacity:0.95; }
.wordmark { color:__ACCENT__; font-weight:800; font-size:24px; letter-spacing:1px; }
.board { display:flex; gap:22px; margin-top:28px; flex:1; }
.idx { flex:1; display:flex; flex-direction:column;
    background:#0b1019; border:1px solid #1c2333; border-radius:12px; padding:22px 22px 18px; }
.iname { color:#9aa7b8; font-size:14px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }
.iclose { font-size:34px; font-weight:800; color:#f0f6fc; margin-top:10px; line-height:1; }
.ipct { font-size:18px; font-weight:700; margin-top:8px; }
.chart { margin-top:18px; flex:1; min-height:250px; }
.foot { margin-top:22px; padding-top:16px; border-top:1px solid #1c2333;
    color:#5b6675; font-size:13px; letter-spacing:1px; text-transform:uppercase; }
</style></head><body>
<div class="card">
    <div class="head">
        <div><div class="kicker">Market Pulse</div><div class="range">__RANGE__</div></div>
        __BRAND__
    </div>
    <div class="board">__PANELS__</div>
    <div class="foot">30-day trend · weekly close · real market data · charts: TradingView Lightweight Charts</div>
</div>
<script>__LIB__</script>
<script>
const PANELS = __DATA__;
const STYLE = __STYLE__;
function hexA(h, a) { var n = parseInt(h.slice(1), 16); return 'rgba(' + ((n>>16)&255) + ',' + ((n>>8)&255) + ',' + (n&255) + ',' + a + ')'; }
window.addEventListener('load', function () {
    PANELS.forEach(function (p, idx) {
        var el = document.getElementById('chart_' + idx);
        var chart = LightweightCharts.createChart(el, {
            width: el.clientWidth, height: el.clientHeight,
            localization: { locale: 'en-US' },
            layout: { background: { type: 'solid', color: 'rgba(0,0,0,0)' }, textColor: '#5b6675', fontSize: 12, fontFamily: 'Inter, sans-serif', attributionLogo: false },
            grid: { vertLines: { visible: false }, horzLines: { color: STYLE.grid } },
            rightPriceScale: { borderVisible: false },
            timeScale: { visible: false },
            crosshair: { mode: 0 },
            handleScroll: false, handleScale: false
        });
        var up = p.pct >= 0;
        var color = up ? STYLE.up : STYLE.down;
        var base = 1700000000;
        var data = p.closes.map(function (v, i) { return { time: base + i * 86400, value: v }; });
        var series;
        if (STYLE.type === 'line') {
            series = chart.addLineSeries({ color: color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
            series.setData(data);
        } else if (STYLE.type === 'baseline') {
            series = chart.addBaselineSeries({
                baseValue: { type: 'price', price: p.closes[0] },
                topLineColor: STYLE.up, topFillColor1: hexA(STYLE.up, 0.28), topFillColor2: hexA(STYLE.up, 0.02),
                bottomLineColor: STYLE.down, bottomFillColor1: hexA(STYLE.down, 0.02), bottomFillColor2: hexA(STYLE.down, 0.28),
                lineWidth: 2, priceLineVisible: false, lastValueVisible: false
            });
            series.setData(data);
        } else {
            series = chart.addAreaSeries({ lineColor: color, lineWidth: 2, topColor: hexA(color, 0.30), bottomColor: 'rgba(0,0,0,0)', priceLineVisible: false, lastValueVisible: false });
            series.setData(data);
        }
        chart.timeScale().fitContent();
    });
});
</script>
</body></html>"""


def _lwc_lib() -> str:
    """Read the vendored Lightweight Charts library (offline-safe)."""
    try:
        return _LWC_PATH.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Lightweight Charts lib not found at %s", _LWC_PATH, exc_info=True)
        return ""


def _build_stock_html_lwc(
    indices: list[dict], date_range: str, logo_uri: str = "", lib_js: str = "", style: dict | None = None
) -> str:
    """Build the Market Pulse card using TradingView Lightweight Charts. Pure (lib_js injected).

    `style` (Phase 2.10d) sets the chart TYPE + COLOR palette; defaults to the first style.
    """
    style = style or CHART_STYLES[0]
    panels = ""
    for i, ix in enumerate(indices):
        up = ix["pct"] >= 0
        color = "#26a69a" if up else "#ef5350"
        sign = "+" if up else ""
        panels += (
            '<div class="idx">'
            f'<div class="iname">{html_lib.escape(str(ix["name"]))}</div>'
            f'<div class="iclose">{ix["last_close"]:,.2f}</div>'
            f'<div class="ipct" style="color:{color}">{sign}{ix["pct"]:.1f}%</div>'
            f'<div class="chart" id="chart_{i}"></div>'
            "</div>\n"
        )
    brand = f'<img class="logo" src="{logo_uri}"/>' if logo_uri else '<div class="wordmark">LuBot</div>'
    data = json.dumps(
        [
            {"name": ix["name"], "last_close": ix["last_close"], "pct": ix["pct"], "closes": list(ix["closes"])}
            for ix in indices
        ]
    )
    return (
        _STOCK_LWC_TEMPLATE.replace("__RANGE__", html_lib.escape(date_range))
        .replace("__BRAND__", brand)
        .replace("__PANELS__", panels)
        .replace("__ACCENT__", style["accent"])
        .replace("__LIB__", lib_js)
        .replace("__DATA__", data)
        .replace("__STYLE__", json.dumps(style))
    )


async def take_stock_lwc_screenshot(
    indices: list[dict], date_range: str = "", style: dict | None = None
) -> ScreenshotResult | None:
    """Render the Market Pulse card with TradingView Lightweight Charts to a PNG.

    `style` (Phase 2.10d) selects the chart type + palette for this week's card.
    Falls back to the hand-built SVG card if the vendored library is unavailable.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    lib_js = _lwc_lib()
    if not lib_js:
        logger.info("Lightweight Charts lib missing — falling back to SVG market card")
        return await take_stock_screenshot(indices, date_range)
    page_html = _build_stock_html_lwc(indices, date_range, logo_uri=_logo_data_uri(), lib_js=lib_js, style=style)

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                    device_scale_factor=1.5,
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(1200)  # let Lightweight Charts render

                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-stock-lwc.png"
                filepath.write_bytes(await page.screenshot(full_page=False))

                logger.info("Stock LWC screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath), url="stock:market", width=SCREENSHOT_WIDTH, height=SCREENSHOT_HEIGHT
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Stock LWC screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def take_card_screenshot(
    indices: list[dict], date_range: str = "", layout_index: int = 0
) -> ScreenshotResult | None:
    """Render a rotating LUXURY Market Pulse card (Phase 2.10e) to a PNG.

    Picks the layout via cards.select_card_layout(layout_index) — rotates per post over
    the catalog (ECharts variety + TradingView candlestick). Injects the layout's engine
    lib. NON-FATAL: falls back to the prior LWC card, then the SVG card.
    """
    from src import cards

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    layout = cards.select_card_layout(layout_index)
    engine = layout["engine"]
    if engine == "lwc":
        lib_js = cards.lwc_lib()
    elif engine == "echarts":
        lib_js = cards.echarts_lib()
    else:  # "html" layouts (e.g. scoreboard) need no chart lib
        lib_js = ""
    if engine != "html" and not lib_js:
        logger.info("Card engine lib missing for %s — falling back to LWC card", layout["name"])
        return await take_stock_lwc_screenshot(indices, date_range)

    # Unified brand chart palette (Phase 2.16 E4): blue-steel chrome, market-standard
    # green/red up/down. Chart TYPE still rotates per post (variety); only colors unify.
    page_html = layout["builder"](indices, date_range, cards.CHART_COLORS, lib_js, cards._logo_data_uri())

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                    device_scale_factor=2,
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(1200)  # let the chart engine paint

                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-card-{layout['name']}.png"
                filepath.write_bytes(await page.screenshot(full_page=False))
                logger.info("Card screenshot (%s) saved: %s", layout["name"], filepath)
                return ScreenshotResult(
                    path=str(filepath), url="stock:market", width=SCREENSHOT_WIDTH, height=SCREENSHOT_HEIGHT
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Card screenshot (%s) failed: %s — falling back", layout["name"], e)
        return await take_stock_lwc_screenshot(indices, date_range)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def take_headline_screenshot(
    headline: str,
    source: str = "",
    date_range: str = "",
    dek: str = "",
    kicker: str = "AI News",
    issue: int | None = None,
) -> ScreenshotResult | None:
    """Render the branded headline card (Phase 2.16 E) to a PNG. Non-fatal.

    Used for ai_news instead of screenshotting a third-party article page (which looks
    generic and leaks nav/login junk). Returns None on failure so the caller can fall back.
    """
    from src import cards

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page_html = cards.build_headline_card(
        headline,
        source=source,
        date_range=date_range,
        dek=dek,
        kicker=kicker,
        issue=issue,
        logo_uri=cards._logo_data_uri(),
        brand=cards.topic_brand("ai_news"),
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT}, device_scale_factor=2
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(400)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-headline.png"
                filepath.write_bytes(await page.screenshot(full_page=False))
                logger.info("Headline card screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath), url="card:headline", width=SCREENSHOT_WIDTH, height=SCREENSHOT_HEIGHT
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Headline card screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def take_insight_screenshot(
    headline: str,
    kicker: str = "Insight",
    date_range: str = "",
    disclaimer: str = "Honest takes on tech",
    issue: int | None = None,
    category: str = "",
    layout_index: int = 0,
) -> ScreenshotResult | None:
    """Render the branded INSIGHT card (Phase 2.16 E) to a PNG. Non-fatal.

    Used for opinion categories (tech_talk, biohacker, Investing Principle) instead of
    screenshotting a third-party article or the staging site. `category` selects the topic
    color world; `layout_index` rotates the composition per post. Returns None on failure.
    """
    from src import cards

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page_html = cards.build_insight_card(
        headline,
        layout=layout_index,
        kicker=kicker,
        date_range=date_range,
        disclaimer=disclaimer,
        issue=issue,
        logo_uri=cards._logo_data_uri(),
        brand=cards.topic_brand(category),
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT}, device_scale_factor=2
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(400)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-insight.png"
                filepath.write_bytes(await page.screenshot(full_page=False))
                logger.info("Insight card screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath), url="card:insight", width=SCREENSHOT_WIDTH, height=SCREENSHOT_HEIGHT
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Insight card screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def take_devtrack_screenshot(metrics: dict, date_range: str = "") -> ScreenshotResult | None:
    """Render the luxury Building-in-Public stat-card (Phase 2.11) to a PNG. Non-fatal."""
    from src import cards

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page_html = cards.build_devtrack_card(
        metrics,
        date_range,
        cards.CHART_COLORS,
        logo_uri=cards._logo_data_uri(),
        brand=cards.topic_brand("wakatime"),
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT}, device_scale_factor=2
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="networkidle")
                await page.wait_for_timeout(500)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-devtrack.png"
                filepath.write_bytes(await page.screenshot(full_page=False))
                logger.info("DevTrack card screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath), url="devtrack:weekly", width=SCREENSHOT_WIDTH, height=SCREENSHOT_HEIGHT
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("DevTrack card screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def take_stock_screenshot(indices: list[dict], date_range: str = "") -> ScreenshotResult | None:
    """Render the Market Pulse card to a PNG with Playwright (real charts + logo)."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page_html = _build_stock_html(indices, date_range, logo_uri=_logo_data_uri())

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(page_html)
        tmp_path = f.name

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                    device_scale_factor=1.5,
                )
                page = await context.new_page()
                await page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
                await page.wait_for_timeout(500)

                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                filepath = SCREENSHOT_DIR / f"{timestamp}-stock-market.png"
                filepath.write_bytes(await page.screenshot(full_page=False))

                logger.info("Stock market screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath),
                    url="stock:market",
                    width=SCREENSHOT_WIDTH,
                    height=SCREENSHOT_HEIGHT,
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Stock screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)
