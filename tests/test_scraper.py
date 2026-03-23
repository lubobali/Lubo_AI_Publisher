"""Tests for web scraper — multi-source article fetching and ranking."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.scraper import (
    ScrapedArticle,
    fetch_url,
    is_valid_article_url,
    load_sources,
    parse_hackernews,
    parse_reddit,
    parse_rss,
    rank_articles,
    scrape_topic,
)

# ---------------------------------------------------------------------------
# ScrapedArticle dataclass
# ---------------------------------------------------------------------------


class TestScrapedArticle:
    def test_create_article(self):
        article = ScrapedArticle(
            title="Test Article",
            url="https://example.com/test",
            summary="A test summary.",
            source="TestSource",
            published_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        assert article.title == "Test Article"
        assert article.url == "https://example.com/test"
        assert article.summary == "A test summary."
        assert article.source == "TestSource"
        assert article.published_at is not None

    def test_article_without_published_date(self):
        article = ScrapedArticle(
            title="No Date",
            url="https://example.com",
            summary="Summary",
            source="Src",
            published_at=None,
        )
        assert article.published_at is None

    def test_article_fields_are_strings(self):
        article = ScrapedArticle(
            title="Title",
            url="https://example.com",
            summary="Sum",
            source="Src",
            published_at=None,
        )
        assert isinstance(article.title, str)
        assert isinstance(article.url, str)
        assert isinstance(article.summary, str)
        assert isinstance(article.source, str)


# ---------------------------------------------------------------------------
# Source config loading
# ---------------------------------------------------------------------------


class TestLoadSources:
    def test_load_returns_dict(self):
        sources = load_sources()
        assert isinstance(sources, dict)

    def test_all_categories_present(self):
        sources = load_sources()
        expected = {
            "ai_news",
            "tech_talk",
            "ai_gadgets",
            "my_agent",
            "biohacker",
            "big_tech",
            "de_work",
        }
        assert set(sources.keys()) == expected

    def test_each_source_has_required_fields(self):
        sources = load_sources()
        for category, source_list in sources.items():
            assert len(source_list) > 0, f"{category} has no sources"
            for src in source_list:
                assert "name" in src, f"Missing name in {category}"
                assert "type" in src, f"Missing type in {category}"
                assert "url" in src, f"Missing url in {category}"
                assert src["type"] in (
                    "rss",
                    "hackernews",
                    "reddit",
                ), f"Unknown type {src['type']} in {category}"

    def test_hackernews_sources_have_search_query(self):
        sources = load_sources()
        for _category, source_list in sources.items():
            for src in source_list:
                if src["type"] == "hackernews":
                    assert "search_query" in src, f"HN source {src['name']} missing search_query"


# ---------------------------------------------------------------------------
# HTTP fetching with retry + rate limiting
# ---------------------------------------------------------------------------


class TestFetchUrl:
    @pytest.mark.asyncio
    async def test_fetch_returns_content(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>Hello</html>"
        mock_response.raise_for_status = lambda: None

        with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com")
            assert result == "<html>Hello</html>"

    @pytest.mark.asyncio
    async def test_fetch_retries_on_failure(self):
        fail_response = Mock()
        fail_response.status_code = 500
        fail_response.raise_for_status.side_effect = Exception("Server Error")

        ok_response = Mock()
        ok_response.status_code = 200
        ok_response.text = "OK"
        ok_response.raise_for_status = Mock()

        with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = [fail_response, ok_response]
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Should retry once and succeed
            result = await fetch_url("https://example.com", retries=2, delay=0)
            assert result == "OK"
            assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_returns_none_after_all_retries_fail(self):
        fail_response = Mock()
        fail_response.status_code = 500
        fail_response.raise_for_status.side_effect = Exception("Server Error")

        with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = fail_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com", retries=2, delay=0)
            assert result is None


# ---------------------------------------------------------------------------
# RSS parser
# ---------------------------------------------------------------------------

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>AI Breakthrough 2026</title>
      <link>https://example.com/ai-breakthrough</link>
      <description>A major AI breakthrough was announced today.</description>
      <pubDate>Thu, 20 Mar 2026 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Old News</title>
      <link>https://example.com/old-news</link>
      <description>This is old news.</description>
      <pubDate>Mon, 10 Mar 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom Article</title>
    <link href="https://example.com/atom-article"/>
    <summary>An atom summary.</summary>
    <updated>2026-03-20T15:00:00Z</updated>
  </entry>
</feed>"""


class TestParseRss:
    def test_parse_rss_extracts_articles(self):
        articles = parse_rss(SAMPLE_RSS, source_name="TestFeed")
        assert len(articles) == 2
        assert articles[0].title == "AI Breakthrough 2026"
        assert articles[0].url == "https://example.com/ai-breakthrough"
        assert articles[0].source == "TestFeed"
        assert "breakthrough" in articles[0].summary.lower()

    def test_parse_rss_extracts_dates(self):
        articles = parse_rss(SAMPLE_RSS, source_name="TestFeed")
        assert articles[0].published_at is not None
        assert articles[0].published_at.year == 2026
        assert articles[0].published_at.month == 3
        assert articles[0].published_at.day == 20

    def test_parse_atom_feed(self):
        articles = parse_rss(SAMPLE_ATOM, source_name="AtomFeed")
        assert len(articles) == 1
        assert articles[0].title == "Atom Article"
        assert articles[0].url == "https://example.com/atom-article"

    def test_parse_rss_empty_feed(self):
        empty_rss = """<?xml version="1.0"?><rss><channel></channel></rss>"""
        articles = parse_rss(empty_rss, source_name="Empty")
        assert articles == []


# ---------------------------------------------------------------------------
# HackerNews parser
# ---------------------------------------------------------------------------

SAMPLE_HN_JSON = """{
  "hits": [
    {
      "title": "Show HN: My AI Project",
      "url": "https://example.com/hn-project",
      "story_text": null,
      "created_at": "2026-03-20T14:00:00.000Z",
      "points": 150,
      "num_comments": 42,
      "objectID": "123456"
    },
    {
      "title": "Ask HN: Best AI tools?",
      "url": null,
      "story_text": "What are the best AI tools in 2026?",
      "created_at": "2026-03-19T10:00:00.000Z",
      "points": 80,
      "num_comments": 25,
      "objectID": "123457"
    },
    {
      "title": "Low Score Post",
      "url": "https://example.com/low-score",
      "story_text": null,
      "created_at": "2026-03-20T08:00:00.000Z",
      "points": 3,
      "num_comments": 0,
      "objectID": "123458"
    }
  ]
}"""


class TestParseHackernews:
    def test_parse_extracts_articles(self):
        articles = parse_hackernews(SAMPLE_HN_JSON, source_name="HN")
        # Should include articles with URLs, skip Ask HN without URL
        urls = [a.url for a in articles]
        assert "https://example.com/hn-project" in urls

    def test_parse_filters_low_score(self):
        articles = parse_hackernews(SAMPLE_HN_JSON, source_name="HN", min_points=10)
        titles = [a.title for a in articles]
        assert "Low Score Post" not in titles

    def test_parse_extracts_dates(self):
        articles = parse_hackernews(SAMPLE_HN_JSON, source_name="HN")
        for article in articles:
            if article.title == "Show HN: My AI Project":
                assert article.published_at is not None
                assert article.published_at.year == 2026

    def test_parse_empty_response(self):
        articles = parse_hackernews('{"hits": []}', source_name="HN")
        assert articles == []


# ---------------------------------------------------------------------------
# Reddit parser
# ---------------------------------------------------------------------------

SAMPLE_REDDIT_JSON = """{
  "data": {
    "children": [
      {
        "data": {
          "title": "Cool AI paper",
          "url": "https://arxiv.org/abs/2026.12345",
          "selftext": "Check out this paper on new transformer architecture.",
          "created_utc": 1742472000,
          "score": 200,
          "num_comments": 50,
          "subreddit": "MachineLearning",
          "is_self": false
        }
      },
      {
        "data": {
          "title": "Self post discussion",
          "url": "https://www.reddit.com/r/MachineLearning/comments/abc123",
          "selftext": "What do you think about the latest GPT model?",
          "created_utc": 1742385600,
          "score": 15,
          "num_comments": 30,
          "subreddit": "MachineLearning",
          "is_self": true
        }
      },
      {
        "data": {
          "title": "Low effort post",
          "url": "https://example.com/low",
          "selftext": "",
          "created_utc": 1742472000,
          "score": 2,
          "num_comments": 0,
          "subreddit": "MachineLearning",
          "is_self": false
        }
      }
    ]
  }
}"""


class TestParseReddit:
    def test_parse_extracts_articles(self):
        articles = parse_reddit(SAMPLE_REDDIT_JSON, source_name="Reddit ML")
        assert len(articles) >= 1
        urls = [a.url for a in articles]
        assert "https://arxiv.org/abs/2026.12345" in urls

    def test_parse_filters_low_score(self):
        articles = parse_reddit(SAMPLE_REDDIT_JSON, source_name="Reddit ML", min_score=10)
        titles = [a.title for a in articles]
        assert "Low effort post" not in titles

    def test_parse_includes_self_posts_with_text(self):
        articles = parse_reddit(SAMPLE_REDDIT_JSON, source_name="Reddit ML", min_score=10)
        titles = [a.title for a in articles]
        assert "Self post discussion" in titles

    def test_parse_extracts_dates(self):
        articles = parse_reddit(SAMPLE_REDDIT_JSON, source_name="Reddit ML")
        for article in articles:
            assert article.published_at is not None

    def test_parse_empty_response(self):
        articles = parse_reddit('{"data": {"children": []}}', source_name="Reddit")
        assert articles == []


# ---------------------------------------------------------------------------
# Article ranking
# ---------------------------------------------------------------------------


class TestRankArticles:
    def test_rank_by_recency(self):
        old = ScrapedArticle(
            title="Old",
            url="https://example.com/old",
            summary="Old article",
            source="Src",
            published_at=datetime(2026, 3, 10, tzinfo=UTC),
        )
        new = ScrapedArticle(
            title="New",
            url="https://example.com/new",
            summary="New article",
            source="Src",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        ranked = rank_articles([old, new])
        assert ranked[0].title == "New"

    def test_articles_without_date_ranked_last(self):
        dated = ScrapedArticle(
            title="Dated",
            url="https://example.com/dated",
            summary="Has date",
            source="Src",
            published_at=datetime(2026, 3, 15, tzinfo=UTC),
        )
        undated = ScrapedArticle(
            title="Undated",
            url="https://example.com/undated",
            summary="No date",
            source="Src",
            published_at=None,
        )
        ranked = rank_articles([undated, dated])
        assert ranked[0].title == "Dated"
        assert ranked[-1].title == "Undated"

    def test_dedup_by_url(self):
        a1 = ScrapedArticle(
            title="Article A",
            url="https://example.com/same",
            summary="First",
            source="Src1",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        a2 = ScrapedArticle(
            title="Article A Copy",
            url="https://example.com/same",
            summary="Second",
            source="Src2",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        ranked = rank_articles([a1, a2])
        assert len(ranked) == 1

    def test_rank_limits_results(self):
        articles = [
            ScrapedArticle(
                title=f"Article {i}",
                url=f"https://example.com/{i}",
                summary="Sum",
                source="Src",
                published_at=datetime(2026, 3, 20, tzinfo=UTC),
            )
            for i in range(20)
        ]
        ranked = rank_articles(articles, max_results=5)
        assert len(ranked) == 5

    def test_rank_empty_list(self):
        assert rank_articles([]) == []

    def test_rank_filters_bare_domain_urls(self):
        good = ScrapedArticle(
            title="Good Article",
            url="https://example.com/real-article",
            summary="Has path",
            source="Src",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        bare = ScrapedArticle(
            title="Bare Domain",
            url="https://www.reddit.com/",
            summary="No path",
            source="Reddit",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        bare2 = ScrapedArticle(
            title="Another Bare",
            url="https://www.reddit.com",
            summary="No path either",
            source="Reddit",
            published_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        ranked = rank_articles([good, bare, bare2])
        assert len(ranked) == 1
        assert ranked[0].title == "Good Article"


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestIsValidArticleUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/article/123",
            "https://arxiv.org/abs/2026.12345",
            "https://www.reddit.com/r/MachineLearning/comments/abc123/title",
            "https://techcrunch.com/2026/03/20/ai-story/",
        ],
    )
    def test_valid_urls(self, url):
        assert is_valid_article_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.reddit.com",
            "https://www.reddit.com/",
            "https://example.com",
            "https://example.com/",
            "",
            "not-a-url",
        ],
    )
    def test_invalid_urls(self, url):
        assert is_valid_article_url(url) is False


# ---------------------------------------------------------------------------
# scrape_topic orchestration
# ---------------------------------------------------------------------------


class TestScrapeTopic:
    @pytest.mark.asyncio
    async def test_scrape_topic_returns_articles(self):
        mock_rss = SAMPLE_RSS
        with patch("src.scraper.fetch_url", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_rss
            articles = await scrape_topic("ai_news")
            assert len(articles) > 0
            assert all(isinstance(a, ScrapedArticle) for a in articles)

    @pytest.mark.asyncio
    async def test_scrape_topic_invalid_category(self):
        articles = await scrape_topic("nonexistent_category")
        assert articles == []

    @pytest.mark.asyncio
    async def test_scrape_topic_handles_fetch_failure(self):
        with patch("src.scraper.fetch_url", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None  # All fetches fail
            articles = await scrape_topic("ai_news")
            assert articles == []

    @pytest.mark.asyncio
    async def test_scrape_topic_results_are_ranked(self):
        mock_rss = SAMPLE_RSS
        with patch("src.scraper.fetch_url", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_rss
            articles = await scrape_topic("ai_news")
            # Results should be sorted by date descending (newest first)
            dates = [a.published_at for a in articles if a.published_at is not None]
            assert dates == sorted(dates, reverse=True)
