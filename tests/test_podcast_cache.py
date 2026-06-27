"""Tests for the podcast transcript cache (P4) — real DB against publisher_test.

Caching is keyed by episode guid so we never re-pay to transcribe the same episode.
store_transcript() is an idempotent upsert; distilled bullets (P5.5) live in the same
row and are filled in later without clobbering the transcript.
"""

import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherPodcastTranscript
from src.podcast_insights import (
    PodcastInsights,
    get_cached_transcript,
    store_transcript,
)

# A minimal podcast feed with one market-relevant episode (audio + guid).
FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
  <channel><title>Show</title>
    <item>
      <title>Markets this week</title>
      <enclosure url="https://cdn.x/ep.mp3" type="audio/mpeg"/>
      <guid>ep-guid-1</guid>
      <pubDate>Wed, 17 Jun 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")


@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    # Clean this table inside the transaction, roll back at teardown (restores any
    # real data). Tests flush, never commit — same discipline as the KB tests.
    session = sessionmaker(bind=test_engine)()
    session.query(PublisherPodcastTranscript).delete()
    session.flush()
    yield session
    session.rollback()
    session.close()


class TestTranscriptCache:
    def test_store_and_get_roundtrip(self, db_session):
        store_transcript(
            db_session,
            guid="g-1",
            transcript="markets were choppy",
            podcast_name="Animal Spirits",
            episode_title="EP 469",
            audio_url="https://cdn.x/ep.mp3",
        )
        row = get_cached_transcript(db_session, "g-1")
        assert row is not None
        assert row.transcript == "markets were choppy"
        assert row.podcast_name == "Animal Spirits"
        assert row.episode_title == "EP 469"
        assert row.audio_url == "https://cdn.x/ep.mp3"
        assert row.distilled is None  # not distilled yet (P5.5 fills it)

    def test_get_returns_none_when_absent(self, db_session):
        assert get_cached_transcript(db_session, "nope") is None

    def test_store_is_idempotent_by_guid(self, db_session):
        store_transcript(db_session, guid="g-2", transcript="first")
        store_transcript(db_session, guid="g-2", transcript="second")
        rows = db_session.query(PublisherPodcastTranscript).filter_by(guid="g-2").all()
        assert len(rows) == 1  # no duplicate row
        assert rows[0].transcript == "second"  # latest wins

    def test_distilled_can_be_added_without_losing_transcript(self, db_session):
        store_transcript(db_session, guid="g-3", transcript="full transcript text")
        store_transcript(
            db_session, guid="g-3", transcript="full transcript text", distilled="- point one\n- point two"
        )
        row = get_cached_transcript(db_session, "g-3")
        assert row.transcript == "full transcript text"
        assert row.distilled == "- point one\n- point two"

    def test_restoring_with_no_distilled_preserves_existing(self, db_session):
        store_transcript(db_session, guid="g-4", transcript="t", distilled="kept bullets")
        store_transcript(db_session, guid="g-4", transcript="t")  # distilled omitted
        row = get_cached_transcript(db_session, "g-4")
        assert row.distilled == "kept bullets"  # not wiped


class TestPodcastInsightsOrchestration:
    """P5: pick -> fetch -> select -> (cache or transcribe+distill) -> ScrapedArticle."""

    FEEDS = [{"name": "Animal Spirits", "url": "u1"}]

    @patch("src.podcast_insights.distill_transcript", return_value="- bullet one\n- bullet two")
    @patch("src.podcast_insights.transcribe_audio", return_value="raw transcript")
    def test_returns_article_and_caches(self, mock_tx, mock_distill, db_session):
        pi = PodcastInsights()
        with patch.object(PodcastInsights, "_fetch_feed", return_value=FEED_XML):
            art = pi.get_episode_article(db_session, week=0, feeds=self.FEEDS)
        assert art is not None
        assert art.summary == "- bullet one\n- bullet two"  # summary IS the bullets
        assert art.source == "Animal Spirits"
        assert art.title == "Markets this week"
        # transcript + bullets were cached by guid
        row = get_cached_transcript(db_session, "ep-guid-1")
        assert row.transcript == "raw transcript"
        assert row.distilled == "- bullet one\n- bullet two"
        mock_tx.assert_called_once()

    def test_cache_hit_skips_transcription_and_distill(self, db_session):
        store_transcript(
            db_session,
            guid="ep-guid-1",
            transcript="t",
            distilled="- cached bullet",
            podcast_name="Animal Spirits",
            episode_title="Markets this week",
            audio_url="https://cdn.x/ep.mp3",
        )
        pi = PodcastInsights()
        with (
            patch.object(PodcastInsights, "_fetch_feed", return_value=FEED_XML),
            patch("src.podcast_insights.transcribe_audio", side_effect=AssertionError("no transcribe!")),
            patch("src.podcast_insights.distill_transcript", side_effect=AssertionError("no distill!")),
        ):
            art = pi.get_episode_article(db_session, week=0, feeds=self.FEEDS)
        assert art.summary == "- cached bullet"  # served from cache, zero API spend

    def test_advances_to_next_feed_on_fetch_failure(self, db_session):
        feeds = [{"name": "Bad", "url": "bad"}, {"name": "Good", "url": "good"}]

        def fake_fetch(self, url):
            if url == "bad":
                raise Exception("feed down")
            return FEED_XML

        with (
            patch.object(PodcastInsights, "_fetch_feed", fake_fetch),
            patch("src.podcast_insights.transcribe_audio", return_value="t"),
            patch("src.podcast_insights.distill_transcript", return_value="- b"),
        ):
            art = PodcastInsights().get_episode_article(db_session, week=0, feeds=feeds)
        assert art.source == "Good"  # fell through the dead feed to the next show

    def test_returns_none_when_transcription_fails(self, db_session):
        with (
            patch.object(PodcastInsights, "_fetch_feed", return_value=FEED_XML),
            patch("src.podcast_insights.transcribe_audio", return_value=None),
        ):
            art = PodcastInsights().get_episode_article(db_session, week=0, feeds=self.FEEDS)
        assert art is None  # non-fatal: caller falls back to yfinance-only

    def test_show_offset_picks_a_different_show(self, db_session):
        """show_offset biases the rotation so a 3x/week topic pulls different shows."""
        feeds = [{"name": "ShowA", "url": "a"}, {"name": "ShowB", "url": "b"}, {"name": "ShowC", "url": "c"}]

        def fake_fetch(self, url):
            # each feed yields a uniquely-named episode so we can see which show was chosen
            name = {"a": "ShowA", "b": "ShowB", "c": "ShowC"}[url]
            return f"""<rss version="2.0"><channel><item>
                <title>{name} weekly</title>
                <enclosure url="https://cdn/{name}.mp3" type="audio/mpeg"/>
                <guid>g-{name}</guid>
                <pubDate>Wed, 17 Jun 2026 08:00:00 +0000</pubDate>
            </item></channel></rss>"""

        chosen = []
        with (
            patch.object(PodcastInsights, "_fetch_feed", fake_fetch),
            patch("src.podcast_insights.transcribe_audio", return_value="t"),
            patch("src.podcast_insights.distill_transcript", return_value="- b"),
        ):
            for offset in range(3):
                art = PodcastInsights().get_episode_article(
                    db_session, week=0, topic="biohacker", feeds=feeds, show_offset=offset
                )
                chosen.append(art.source)
        assert len(set(chosen)) == 3  # 3 offsets -> 3 distinct shows

    @patch("src.podcast_insights.transcribe_audio", return_value="raw transcript")
    def test_biohacker_topic_uses_its_feeds_and_distill_lens(self, mock_tx, db_session):
        """topic='biohacker' loads the biohacker feeds and distills with the biohacker prompt."""
        from src.podcast_insights import _DISTILL_BIOHACKER

        with (
            patch.object(PodcastInsights, "_fetch_feed", return_value=FEED_XML),
            patch("src.podcast_insights.distill_transcript", return_value="- stop seed oils") as mock_distill,
        ):
            art = PodcastInsights().get_episode_article(db_session, week=0, topic="biohacker")
        assert art is not None and art.summary == "- stop seed oils"
        # the biohacker lens was passed through, not the market default
        assert mock_distill.call_args.kwargs["system"] is _DISTILL_BIOHACKER
