"""Tests for podcast insights.

P1: parse_podcast_feed — turn a podcast RSS feed (not article RSS) into a list of
transcribable PodcastEpisode objects. Podcast feeds differ from article feeds: the
audio lives in <enclosure>, the text is the show-notes (often content:encoded), and
some feeds (Megaphone) omit <link> entirely — only a UUID <guid> + the mp3.
"""

from datetime import datetime
from unittest.mock import Mock, patch

from src.podcast_insights import (
    _DISTILL_BIOHACKER,
    _DISTILL_BY_TOPIC,
    _DISTILL_SYSTEM,
    PodcastEpisode,
    distill_transcript,
    load_podcast_feeds,
    parse_podcast_feed,
    pick_podcast,
    rotation_order,
    select_episode,
)


def _chat_response(content):
    r = Mock()
    r.raise_for_status = Mock()
    r.json = Mock(return_value={"choices": [{"message": {"content": content}}]})
    return r


# A normal podcast item: <link>, <enclosure> mp3, <guid>, plain description.
SAMPLE_STANDARD = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Investing Experts</title>
    <item>
      <title>Great market expectations</title>
      <link>https://example.com/ep/great-expectations</link>
      <description>Hosts talk about valuations and the week.</description>
      <enclosure url="https://cdn.example.com/audio/ep1.mp3" length="0" type="audio/mpeg"/>
      <guid isPermaLink="false">ep-1-guid</guid>
      <pubDate>Wed, 17 Jun 2026 10:50:02 +0000</pubDate>
    </item>
  </channel>
</rss>"""


# A Megaphone item: NO <link>, UUID <guid>, HTML show-notes in content:encoded with
# a zero-width word-joiner (⁠) sprinkled in — exactly what Animal Spirits ships.
SAMPLE_MEGAPHONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Animal Spirits</title>
    <item>
      <title>How Much is $1 Trillion? (EP. 469)</title>
      <description>Short blurb.</description>
      <content:encoded><![CDATA[<p>On episode 469, <a href="https://x.com">Michael⁠Batnick</a> and Ben discuss breadth, rotation, and what the data says.</p>]]></content:encoded>
      <enclosure url="https://traffic.megaphone.fm/TCP123.mp3" length="0" type="audio/mpeg"/>
      <guid isPermaLink="false">3d363410-48b2-11f1-8ce1-8771a70e042c</guid>
      <pubDate>Wed, 17 Jun 2026 08:00:00 -0000</pubDate>
    </item>
  </channel>
</rss>"""

# Newest-first, with a middle item that has NO audio (must be skipped) and an older
# item with NO guid (must fall back to the audio URL).
SAMPLE_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Newest</title>
      <enclosure url="https://cdn.example.com/ep-new.mp3" type="audio/mpeg"/>
      <guid>g-new</guid>
      <pubDate>Thu, 18 Jun 2026 09:00:00 +0000</pubDate>
    </item>
    <item>
      <title>NoAudio</title>
      <link>https://example.com/no-audio</link>
      <guid>g-na</guid>
    </item>
    <item>
      <title>Older</title>
      <enclosure url="https://cdn.example.com/ep-old.mp3" type="audio/mpeg"/>
      <pubDate>Wed, 10 Jun 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


class TestParsePodcastFeed:
    def test_parses_standard_episode(self):
        eps = parse_podcast_feed(SAMPLE_STANDARD, podcast_name="Investing Experts")
        assert len(eps) == 1
        ep = eps[0]
        assert isinstance(ep, PodcastEpisode)
        assert ep.title == "Great market expectations"
        assert ep.audio_url == "https://cdn.example.com/audio/ep1.mp3"
        assert ep.guid == "ep-1-guid"
        assert ep.page_url == "https://example.com/ep/great-expectations"
        assert "valuations" in ep.show_notes
        assert ep.podcast_name == "Investing Experts"
        assert ep.published_at is not None
        assert ep.published_at.year == 2026

    def test_parses_linkless_megaphone_item(self):
        eps = parse_podcast_feed(SAMPLE_MEGAPHONE, podcast_name="Animal Spirits")
        assert len(eps) == 1
        ep = eps[0]
        assert ep.audio_url == "https://traffic.megaphone.fm/TCP123.mp3"
        assert ep.guid == "3d363410-48b2-11f1-8ce1-8771a70e042c"
        assert ep.page_url == ""  # Megaphone has no <link> — must not be dropped

    def test_prefers_content_encoded_and_strips_html(self):
        ep = parse_podcast_feed(SAMPLE_MEGAPHONE)[0]
        assert "breadth, rotation" in ep.show_notes  # fuller content:encoded won
        assert "<p>" not in ep.show_notes and "<a" not in ep.show_notes  # HTML stripped

    def test_strips_zero_width_chars(self):
        ep = parse_podcast_feed(SAMPLE_MEGAPHONE)[0]
        assert "⁠" not in ep.show_notes
        assert "MichaelBatnick" in ep.show_notes  # word-joiner removed, text joined

    def test_skips_items_without_audio(self):
        eps = parse_podcast_feed(SAMPLE_MIXED)
        assert [e.title for e in eps] == ["Newest", "Older"]  # NoAudio dropped, order kept

    def test_guid_falls_back_to_audio_url(self):
        older = parse_podcast_feed(SAMPLE_MIXED)[1]
        assert older.title == "Older"
        assert older.guid == "https://cdn.example.com/ep-old.mp3"


class TestRotation:
    """P2: deterministic round-robin over the podcasts, with a fallback order."""

    PODS = ["a", "b", "c", "d"]

    def test_pick_is_round_robin_by_week(self):
        picks = [pick_podcast(w, self.PODS) for w in range(6)]
        assert picks == ["a", "b", "c", "d", "a", "b"]

    def test_all_four_used_over_four_weeks(self):
        picks = {pick_podcast(w, self.PODS) for w in range(4)}
        assert picks == set(self.PODS)  # every show gets used in a 4-week cycle

    def test_rotation_order_starts_at_week_and_wraps(self):
        assert rotation_order(0, self.PODS) == ["a", "b", "c", "d"]
        assert rotation_order(2, self.PODS) == ["c", "d", "a", "b"]

    def test_rotation_order_is_a_full_permutation(self):
        # Fallback must cover EVERY show (a dead feed advances to the next) — no drops.
        order = rotation_order(3, self.PODS)
        assert len(order) == 4
        assert set(order) == set(self.PODS)
        assert order[0] == pick_podcast(3, self.PODS)  # first = this week's pick

    def test_large_and_negative_weeks_wrap(self):
        assert pick_podcast(10, self.PODS) == "c"  # 10 % 4 == 2
        assert pick_podcast(-1, self.PODS) == "d"  # -1 % 4 == 3 in Python

    def test_empty_list_is_safe(self):
        assert rotation_order(0, []) == []
        assert pick_podcast(0, []) is None


class TestLoadPodcastFeeds:
    """Podcast feeds come from scraper_sources.yaml -> podcasts[topic] (Phase F: topic-aware)."""

    def test_loads_four_feeds_with_name_and_url(self):
        feeds = load_podcast_feeds()  # defaults to market_pulse
        assert len(feeds) == 4
        names = {f["name"] for f in feeds}
        assert "Animal Spirits" in names and "RiskReversal Pod" in names
        assert all(f["url"].startswith("http") for f in feeds)

    def test_market_pulse_topic_explicit(self):
        assert load_podcast_feeds("market_pulse") == load_podcast_feeds()

    def test_loads_biohacker_feeds(self):
        feeds = load_podcast_feeds("biohacker")
        names = {f["name"] for f in feeds}
        assert "The Human Upgrade with Dave Asprey" in names
        assert all(f["url"].startswith("http") for f in feeds)

    def test_unknown_topic_is_empty(self):
        assert load_podcast_feeds("nope") == []


class TestDistillByTopic:
    """Each topic gets its own distillation lens; defaults to the market prompt."""

    def test_registry_maps_topics(self):
        assert _DISTILL_BY_TOPIC["market_pulse"] is _DISTILL_SYSTEM
        assert _DISTILL_BY_TOPIC["biohacker"] is _DISTILL_BIOHACKER

    def test_biohacker_prompt_is_longevity_focused(self):
        p = _DISTILL_BIOHACKER.lower()
        assert "biohacking" in p or "longevity" in p
        assert "free" in p  # always serve no-money audience
        assert "stop" in p  # lead with what to remove


class TestSelectEpisode:
    """P5 smart-select: freshest market-relevant episode, skip non-pulse titles."""

    def _ep(self, title, day):
        return PodcastEpisode(
            title=title,
            audio_url=f"https://x/{day}.mp3",
            guid=f"g-{day}",
            published_at=datetime(2026, 6, day),
        )

    def test_picks_newest_when_relevant(self):
        eps = [self._ep("Weekly market recap", 17), self._ep("Older", 10)]
        assert select_episode(eps).title == "Weekly market recap"

    def test_skips_talk_your_book_and_mailbag(self):
        eps = [
            self._ep("Talk Your Book: EM Bonds", 18),
            self._ep("Mailbag special", 17),
            self._ep("Markets this week", 16),
        ]
        assert select_episode(eps).title == "Markets this week"

    def test_returns_none_when_all_skippable(self):
        eps = [self._ep("Talk Your Book: X", 18), self._ep("AMA episode", 17)]
        assert select_episode(eps) is None

    def test_empty_returns_none(self):
        assert select_episode([]) is None


class TestDistillTranscript:
    """P5.5: transcript -> 3-5 market-theme bullets via OpenRouter (boundary mocked)."""

    @patch("src.podcast_insights.httpx.post")
    def test_returns_bullets(self, mock_post):
        mock_post.return_value = _chat_response("- breadth is narrow\n- rotation into value")
        out = distill_transcript("a long transcript", api_key="sk")
        assert "breadth" in out and out.startswith("-")

    @patch("src.podcast_insights.httpx.post")
    def test_sends_model_and_transcript(self, mock_post):
        mock_post.return_value = _chat_response("- x")
        distill_transcript("THE TRANSCRIPT", api_key="sk", model="some/model")
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk"
        body = kwargs["json"]
        assert body["model"] == "some/model"
        assert any("THE TRANSCRIPT" in m["content"] for m in body["messages"])

    @patch("src.podcast_insights.httpx.post")
    def test_default_system_is_market_prompt(self, mock_post):
        mock_post.return_value = _chat_response("- x")
        distill_transcript("t", api_key="sk")
        msgs = mock_post.call_args.kwargs["json"]["messages"]
        assert msgs[0]["content"] == _DISTILL_SYSTEM

    @patch("src.podcast_insights.httpx.post")
    def test_custom_system_is_used(self, mock_post):
        mock_post.return_value = _chat_response("- x")
        distill_transcript("t", api_key="sk", system=_DISTILL_BIOHACKER)
        msgs = mock_post.call_args.kwargs["json"]["messages"]
        assert msgs[0]["content"] == _DISTILL_BIOHACKER

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr("src.podcast_insights.httpx.post", Mock(side_effect=AssertionError("network!")))
        assert distill_transcript("t") is None

    @patch("src.podcast_insights.httpx.post", side_effect=Exception("boom"))
    def test_api_error_returns_none(self, _mock_post):
        assert distill_transcript("t", api_key="sk") is None

    @patch("src.podcast_insights.httpx.post")
    def test_empty_distillation_returns_none(self, mock_post):
        mock_post.return_value = _chat_response("   ")
        assert distill_transcript("t", api_key="sk") is None
