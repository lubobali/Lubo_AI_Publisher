"""Playwright screenshot engine — captures web pages at LinkedIn-optimal size."""

import contextlib
import html as html_lib
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
) -> ScreenshotResult | None:
    """Generate a terminal-style screenshot from real git commit data.

    Renders an HTML page styled like a dark terminal showing git log + diff stats,
    then screenshots it with Playwright.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # Build file lines with color by type
    file_lines = ""
    for f in changed_files[:15]:
        short = f.split("/", 1)[-1] if "/" in f else f
        color = "#6a9955" if "/test" in f else "#dcdcaa"
        file_lines += f'<div class="file"><span style="color:{color}">{html_lib.escape(short)}</span></div>\n'
    if len(changed_files) > 15:
        file_lines += f'<div class="file dim">... and {len(changed_files) - 15} more files</div>\n'

    net = lines_added - lines_deleted
    net_str = f"+{net}" if net > 0 else str(net)

    page_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{
    margin: 0; padding: 0;
    background: #1e1e2e;
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    color: #cdd6f4; font-size: 14px;
}}
.window {{
    margin: 24px; background: #11111b; border-radius: 12px;
    overflow: hidden; border: 1px solid #313244;
}}
.titlebar {{
    background: #181825; padding: 10px 16px;
    display: flex; align-items: center; gap: 8px;
    border-bottom: 1px solid #313244;
}}
.dot {{ width: 12px; height: 12px; border-radius: 50%; }}
.red {{ background: #f38ba8; }}
.yellow {{ background: #f9e2af; }}
.green {{ background: #a6e3a1; }}
.title {{ color: #6c7086; font-size: 12px; margin-left: 12px; }}
.content {{ padding: 20px 24px; line-height: 1.7; }}
.prompt {{ color: #89b4fa; }}
.cmd {{ color: #cdd6f4; }}
.hash {{ color: #f9e2af; }}
.msg {{ color: #cdd6f4; font-weight: 600; }}
.date {{ color: #6c7086; }}
.stats {{ margin: 16px 0; padding: 12px 0; border-top: 1px solid #313244; }}
.added {{ color: #a6e3a1; font-weight: 700; }}
.deleted {{ color: #f38ba8; font-weight: 700; }}
.net {{ color: #89b4fa; }}
.file {{ color: #bac2de; padding: 1px 0; font-size: 13px; }}
.dim {{ color: #585b70; }}
.bar {{ display: inline-block; height: 10px; border-radius: 2px; }}
.bar-add {{ background: #a6e3a1; }}
.bar-del {{ background: #f38ba8; }}
.section {{ color: #6c7086; font-size: 12px; text-transform: uppercase;
    letter-spacing: 1px; margin: 16px 0 8px; }}
</style></head><body>
<div class="window">
    <div class="titlebar">
        <div class="dot red"></div>
        <div class="dot yellow"></div>
        <div class="dot green"></div>
        <div class="title">lubot-staging ~/services-agent-api</div>
    </div>
    <div class="content">
        <div>
            <span class="prompt">$</span>
            <span class="cmd"> git log --oneline -1</span>
        </div>
        <div style="margin: 8px 0 4px;">
            <span class="hash">{html_lib.escape(commit_hash or 'HEAD')}</span>
            <span class="msg"> {html_lib.escape(commit_message)}</span>
        </div>
        <div class="date">{html_lib.escape(commit_date)}</div>

        <div class="stats">
            <div>
                <span class="prompt">$</span>
                <span class="cmd"> git diff --stat</span>
            </div>
            <div style="margin: 8px 0;">
                <span class="added">+{lines_added}</span>
                <span class="dim"> additions  </span>
                <span class="deleted">-{lines_deleted}</span>
                <span class="dim"> deletions  </span>
                <span class="net">({net_str} net)</span>
                <span class="dim">  across </span>
                <span style="color:#cdd6f4">{files_changed} files</span>
            </div>
            <div style="margin: 4px 0;">
                <span class="bar bar-add" style="width:{min(lines_added // 8, 200)}px"></span>
                <span class="bar bar-del" style="width:{min(lines_deleted // 8, 200)}px"></span>
            </div>
        </div>

        <div class="section">files changed</div>
        {file_lines}
    </div>
</div>
</body></html>"""

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
                filename = f"{timestamp}-git-commit.png"
                filepath = SCREENSHOT_DIR / filename

                screenshot_bytes = await page.screenshot(full_page=False)
                filepath.write_bytes(screenshot_bytes)

                logger.info("Git screenshot saved: %s", filepath)
                return ScreenshotResult(
                    path=str(filepath),
                    url=f"git:{commit_hash}",
                    width=SCREENSHOT_WIDTH,
                    height=SCREENSHOT_HEIGHT,
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Git screenshot failed: %s", e)
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)
