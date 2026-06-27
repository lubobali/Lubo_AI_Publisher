"""Multi-source web scraper — fetches and ranks articles per topic category."""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from bs4 import BeautifulSoup

from src.observability import get_client, observe

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scraper_sources.yaml"

USER_AGENT = "Mozilla/5.0 (compatible; LuBotPublisher/1.0; +https://lubot.ai)"


@dataclass
class ScrapedArticle:
    """A single scraped article from any source."""

    title: str
    url: str
    summary: str
    source: str
    published_at: datetime | None
    source_priority: int = 99  # lower = higher priority (0 = top source)


def load_sources() -> dict:
    """Load news-scraper sources config from YAML.

    Excludes the ``podcasts`` section — those feeds are audio episodes owned by
    PodcastInsights (transcribe + distill), not article sources the scraper fetches.
    """
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return {k: v for k, v in config.items() if k != "podcasts"}


async def fetch_url(
    url: str,
    retries: int = 3,
    delay: float = 1.0,
    params: dict | None = None,
) -> str | None:
    """Fetch URL content with retry logic and rate limiting.

    Returns response text on success, None after all retries fail.
    """
    async with httpx.AsyncClient() as client:
        for attempt in range(retries):
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=15.0,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.text
            except Exception as e:
                logger.warning(
                    "Fetch attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    retries,
                    url,
                    e,
                )
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
    return None


def parse_rss(xml_text: str, source_name: str) -> list[ScrapedArticle]:
    """Parse RSS or Atom feed XML into articles."""
    soup = BeautifulSoup(xml_text, "xml")
    articles = []

    # Try RSS <item> elements
    items = soup.find_all("item")
    for item in items:
        title = item.find("title")
        link = item.find("link")
        desc = item.find("description")
        pub_date = item.find("pubDate")

        if not title or not link:
            continue

        published_at = None
        if pub_date and pub_date.string:
            with contextlib.suppress(Exception):
                published_at = parsedate_to_datetime(pub_date.string.strip())

        articles.append(
            ScrapedArticle(
                title=title.get_text(strip=True),
                url=link.get_text(strip=True),
                summary=(desc.get_text(strip=True) if desc else ""),
                source=source_name,
                published_at=published_at,
            )
        )

    # Try Atom <entry> elements if no RSS items found
    if not articles:
        entries = soup.find_all("entry")
        for entry in entries:
            title = entry.find("title")
            link = entry.find("link")
            summary = entry.find("summary")
            updated = entry.find("updated")

            if not title:
                continue

            entry_url = ""
            if link:
                entry_url = link.get("href", "") or link.get_text(strip=True)

            published_at = None
            if updated and updated.string:
                with contextlib.suppress(Exception):
                    published_at = datetime.fromisoformat(updated.string.strip())

            articles.append(
                ScrapedArticle(
                    title=title.get_text(strip=True),
                    url=entry_url,
                    summary=(summary.get_text(strip=True) if summary else ""),
                    source=source_name,
                    published_at=published_at,
                )
            )

    return articles


def parse_hackernews(
    json_text: str,
    source_name: str,
    min_points: int = 5,
) -> list[ScrapedArticle]:
    """Parse HackerNews Algolia API response into articles."""
    data = json.loads(json_text)
    articles = []

    for hit in data.get("hits", []):
        points = hit.get("points") or 0
        if points < min_points:
            continue

        url = hit.get("url")
        if not url:
            continue

        title = hit.get("title", "")
        if not title:
            continue

        published_at = None
        created_at = hit.get("created_at")
        if created_at:
            with contextlib.suppress(Exception):
                published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        articles.append(
            ScrapedArticle(
                title=title,
                url=url,
                summary=hit.get("story_text") or "",
                source=source_name,
                published_at=published_at,
            )
        )

    return articles


def parse_reddit(
    json_text: str,
    source_name: str,
    min_score: int = 5,
) -> list[ScrapedArticle]:
    """Parse Reddit JSON API response into articles."""
    data = json.loads(json_text)
    articles = []

    children = data.get("data", {}).get("children", [])
    for child in children:
        post = child.get("data", {})
        score = post.get("score", 0)
        if score < min_score:
            continue

        title = post.get("title", "")
        if not title:
            continue

        url = post.get("url", "")
        selftext = post.get("selftext", "")
        is_self = post.get("is_self", False)

        # For self posts, use selftext as summary
        summary = selftext if is_self else ""

        published_at = None
        created_utc = post.get("created_utc")
        if created_utc:
            with contextlib.suppress(Exception):
                published_at = datetime.fromtimestamp(created_utc, tz=UTC)

        articles.append(
            ScrapedArticle(
                title=title,
                url=url,
                summary=summary,
                source=source_name,
                published_at=published_at,
            )
        )

    return articles


def is_valid_article_url(url: str) -> bool:
    """Check if URL has a meaningful path (not just a bare domain).

    Rejects empty strings, non-URLs, and domain-only URLs like https://www.reddit.com/.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    return len(path) > 0


def rank_articles(
    articles: list[ScrapedArticle],
    max_results: int = 10,
) -> list[ScrapedArticle]:
    """Rank articles by recency, dedup by URL, and limit results."""
    if not articles:
        return []

    # Filter out articles with bare domain URLs
    valid = [a for a in articles if is_valid_article_url(a.url)]

    # Dedup by URL
    seen_urls: set[str] = set()
    unique: list[ScrapedArticle] = []
    for article in valid:
        if article.url not in seen_urls:
            seen_urls.add(article.url)
            unique.append(article)

    # Sort: source priority first (lower = better), then recency within same priority
    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    def sort_key(a: ScrapedArticle) -> tuple[int, datetime]:
        pub = a.published_at or epoch
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        # Negate timestamp so newest sorts first within same priority
        return (a.source_priority, datetime.max.replace(tzinfo=UTC) - pub)

    unique.sort(key=sort_key)

    return unique[:max_results]


@observe()
async def scrape_topic(category: str) -> list[ScrapedArticle]:
    """Scrape all sources for a topic category and return ranked articles."""
    sources = load_sources()

    if category not in sources:
        logger.warning("Unknown category: %s", category)
        return []

    all_articles: list[ScrapedArticle] = []
    sources_attempted = len(sources[category])
    sources_fetched = 0
    sources_parse_failed = 0

    for source_idx, source in enumerate(sources[category]):
        source_name = source["name"]
        source_type = source["type"]
        url = source["url"]

        # Build fetch params for HackerNews
        params = None
        if source_type == "hackernews":
            params = {
                "query": source.get("search_query", ""),
                "tags": "story",
                "numericFilters": "points>5",
                "hitsPerPage": 10,
            }

        content = await fetch_url(url, params=params)
        if content is None:
            logger.warning("Failed to fetch %s (%s)", source_name, url)
            continue

        sources_fetched += 1

        try:
            if source_type == "rss":
                articles = parse_rss(content, source_name=source_name)
            elif source_type == "hackernews":
                articles = parse_hackernews(content, source_name=source_name)
            elif source_type == "reddit":
                articles = parse_reddit(content, source_name=source_name)
            else:
                logger.warning("Unknown source type: %s", source_type)
                continue

            # Tag articles with source priority (position in config = priority)
            for article in articles:
                article.source_priority = source_idx
            all_articles.extend(articles)
        except Exception as e:
            sources_parse_failed += 1
            logger.warning("Failed to parse %s: %s", source_name, e)

    result = rank_articles(all_articles)

    # Report scraping metrics to Langfuse
    try:
        get_client().update_current_span(
            metadata={
                "category": category,
                "sources_attempted": sources_attempted,
                "sources_fetched": sources_fetched,
                "sources_parse_failed": sources_parse_failed,
                "articles_before_ranking": len(all_articles),
                "articles_after_ranking": len(result),
            }
        )
    except Exception:
        logger.debug("Langfuse scraper reporting failed", exc_info=True)

    return result
