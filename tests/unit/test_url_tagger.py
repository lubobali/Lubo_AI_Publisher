"""Unit tests for the outbound-URL UTM tagger (attribution).

Pure functions in src.post_processor: build_campaign() + tag_urls(). The tagger rewrites every
EXACT lubot.ai root URL in post text to carry utm_source/medium/campaign, per platform, right
before the platform client posts it. No network, no DB.
"""

from datetime import date

from src.post_processor import build_campaign, tag_urls


class TestTagUrls:
    def test_bare_lubot_ai_gets_tagged_for_linkedin(self):
        out = tag_urls("check out lubot.ai now", platform="linkedin", campaign="ai-news-2026-08-03")
        assert "https://lubot.ai?utm_source=linkedin&utm_medium=post&utm_campaign=ai-news-2026-08-03" in out
        assert "check out " in out and " now" in out  # surrounding text preserved

    def test_https_lubot_ai_gets_tagged(self):
        out = tag_urls("https://lubot.ai", platform="linkedin", campaign="c")
        assert out == "https://lubot.ai?utm_source=linkedin&utm_medium=post&utm_campaign=c"

    def test_http_is_upgraded_to_https(self):
        out = tag_urls("http://lubot.ai", platform="linkedin", campaign="c")
        assert out.startswith("https://lubot.ai?utm_source=linkedin")
        assert "http://" not in out

    def test_url_with_path_preserves_path(self):
        out = tag_urls("lubot.ai/architecture", platform="linkedin", campaign="c")
        assert out == "https://lubot.ai/architecture?utm_source=linkedin&utm_medium=post&utm_campaign=c"

    def test_already_tagged_url_unchanged(self):
        original = "https://lubot.ai?utm_source=linkedin&utm_medium=post&utm_campaign=old"
        out = tag_urls(original, platform="linkedin", campaign="new")
        assert out == original  # idempotent — never re-tag or overwrite

    def test_url_with_existing_query_appends_with_amp(self):
        out = tag_urls("lubot.ai?ref=abc", platform="linkedin", campaign="c")
        assert out == "https://lubot.ai?ref=abc&utm_source=linkedin&utm_medium=post&utm_campaign=c"
        assert out.count("?") == 1  # only one query separator

    def test_url_with_fragment_puts_utm_before_fragment(self):
        out = tag_urls("lubot.ai/docs#install", platform="linkedin", campaign="c")
        assert out == "https://lubot.ai/docs?utm_source=linkedin&utm_medium=post&utm_campaign=c#install"
        assert out.index("?utm_source") < out.index("#install")  # fragment stays at the end

    def test_twitter_platform_swaps_utm_source(self):
        out = tag_urls("lubot.ai", platform="x", campaign="c")
        assert "utm_source=twitter" in out
        assert "utm_medium=post" in out

    def test_reddit_platform_swaps_utm_source(self):
        out = tag_urls("lubot.ai", platform="reddit", campaign="c")
        assert "utm_source=reddit" in out
        assert "utm_medium=comment" in out  # reddit default is a comment

    def test_non_lubot_domain_untouched(self):
        for text in ("staging.lubot.ai", "old.lubot.ai", "l.lubot.ai", "https://staging.lubot.ai"):
            assert tag_urls(text, platform="linkedin", campaign="c") == text  # only the exact root is tagged

    def test_multiple_occurrences_all_tagged(self):
        out = tag_urls("lubot.ai and again lubot.ai/x", platform="linkedin", campaign="c")
        assert out.count("utm_source=linkedin") == 2
        assert "https://lubot.ai?utm_source=linkedin" in out
        assert "https://lubot.ai/x?utm_source=linkedin" in out

    def test_tags_inside_backticks_and_quotes(self):
        out = tag_urls('try `lubot.ai` or "lubot.ai"', platform="linkedin", campaign="c")
        assert out.count("utm_source=linkedin") == 2

    def test_trailing_sentence_punctuation_not_swallowed(self):
        out = tag_urls("go to lubot.ai.", platform="linkedin", campaign="c")
        assert out.endswith(".")  # the period stays as sentence punctuation
        assert "https://lubot.ai?utm_source=linkedin&utm_medium=post&utm_campaign=c." in out


class TestBuildCampaign:
    def test_campaign_slug_uses_topic_and_date(self):
        assert build_campaign("stock-tool-launch", date(2026, 8, 4)) == "stock-tool-launch-2026-08-04"
        # a sources_key with an underscore is normalized to hyphens
        assert build_campaign("ai_news", date(2026, 8, 3)) == "ai-news-2026-08-03"

    def test_campaign_falls_back_to_organic_when_no_topic(self):
        assert build_campaign(None, date(2026, 8, 4)) == "organic-2026-08-04"
        assert build_campaign("", date(2026, 8, 4)) == "organic-2026-08-04"
