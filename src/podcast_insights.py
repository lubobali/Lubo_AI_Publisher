"""Podcast Insights — turn a market podcast's RSS feed into transcribable episodes.

P1 (this file, for now): the FEED PARSER. Podcast feeds differ from the article RSS
that scraper.parse_rss handles:
  - the audio we transcribe lives in <enclosure url="...mp3">
  - the text is the episode show-notes (often in <content:encoded>, HTML)
  - some feeds (Megaphone: Animal Spirits, RiskReversal) omit <link> entirely — they
    carry only a UUID <guid> and the enclosure, so scraper.parse_rss would drop them.

So we key off the <enclosure> (no audio -> nothing to transcribe -> skip the item) and
tolerate a missing <link>. Episode SELECTION, rotation, transcription, and distillation
land in later steps (P2/P3/P5/P5.5); this file just yields clean episodes, newest-first.
"""

import contextlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.models import PublisherPodcastTranscript
from src.scraper import ScrapedArticle
from src.transcription import transcribe_audio

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scraper_sources.yaml"

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
# Distillation model — env-swappable to the latest fast chat model at deploy time.
DEFAULT_DISTILL_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_MAX_TRANSCRIPT_CHARS = 60000  # safety bound on tokens; a 60-min ep is well under this
_DISTILL_SYSTEM = (
    "You distill an investing/markets podcast transcript into the key market ideas. "
    "Output 3 to 5 short plain-language bullet points capturing the main market themes, "
    "arguments, or debates the hosts made this episode. STRIP ads, sponsor reads, intros, "
    "outros, housekeeping, and off-topic tangents. Use NO numbers (no prices, percentages, "
    "or dollar amounts). Each bullet on its own line starting with '- '. No preamble, no title."
)

# Biohacker / longevity distill (Phase F) — Lubo's lens (memory project_biohacker_brief).
_DISTILL_BIOHACKER = (
    "You distill a longevity/biohacking podcast transcript into the most VALUABLE, ACTIONABLE "
    "takeaways for a busy person. Lens: biohacking = optimizing the WHOLE body as one system "
    "(food, environment, stress, sleep, light, movement, people, mind), not just supplements. "
    "Prioritize what people can do for FREE; note premium/paid options separately. LEAD with what "
    "to STOP (seed oils, ultra-processed food, plastics, toxins) before what to add. Give the brief "
    "MECHANISM (why). Where relevant use the age framework: what lowers BIOLOGICAL age and how to "
    "MEASURE it (free at-home markers, bloodwork, premium epigenetic clocks). No hype; flag solid "
    "vs marketing. Output 3 to 5 short bullets, each on its own line starting with '- ', each = one "
    "action + the why, marked free vs costs-money; attribute claims to the speaker. Use NO invented "
    "numbers, studies, or dosages not in the transcript. No preamble, no title."
)

# AI News / tech distill (Phase 2.23) — Lubo wants latest AI+tech from a trusted show
# (Moonshots w/ Peter Diamandis), NOT just OpenAI headlines from the scraper.
_DISTILL_AINEWS = (
    "You distill an AI/tech podcast transcript into the most NEWSWORTHY developments in AI and "
    "technology the hosts discussed this episode. Focus on CONCRETE things: new models, product "
    "launches, research breakthroughs, company/funding moves, policy, and where the tech is heading. "
    "STRIP ads, sponsor reads, intros, outros, housekeeping, and off-topic tangents (skip pure space/"
    "longevity/energy unless it ties to the AI/tech story). Attribute claims to the speaker or the "
    "show. Prefer specific, verifiable developments over vague hype; flag speculation vs shipped. "
    "Output 3 to 5 short plain-language bullets, each on its own line starting with '- ', each = one "
    "concrete development + why it matters. Use NO invented numbers, dates, or company names not in "
    "the transcript. No preamble, no title."
)

# Tech Talk distill (Phase 2.24) — SAME show as ai_news, but the OPINION lens: pull ideas a
# senior engineer would riff on, not a news recap. One 2h episode -> two posts, two angles.
_DISTILL_TECHTALK = (
    "You distill an AI/tech podcast transcript into 3 to 5 MEATY ideas, debates, or trends a senior "
    "data/AI engineer could form a strong OPINION on — the conceptual and engineering angle, NOT a "
    "news recap. Prefer the 'why it matters', the trade-off, the architecture shift, or the "
    "second-order implication for people who actually build. STRIP ads, sponsor reads, intros, outros, "
    "housekeeping, and off-topic tangents. Attribute claims to the speaker or the show. Each bullet = "
    "one idea + the tension or insight worth an opinion, on its own line starting with '- '. Use NO "
    "invented numbers, studies, or company names not in the transcript. No preamble, no title."
)

# Per-topic distillation prompt — defaults to the market prompt for any unmapped topic.
_DISTILL_BY_TOPIC = {
    "market_pulse": _DISTILL_SYSTEM,
    "biohacker": _DISTILL_BIOHACKER,
    "ai_news": _DISTILL_AINEWS,
    "tech_talk": _DISTILL_TECHTALK,
}

# Invisible chars Megaphone et al. sprinkle around hyperlinked names in show-notes.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)

# Episode titles that are NOT a weekly market read — skip these when selecting.
_SKIP_TITLE = re.compile(
    r"talk your book|mailbag|q&a|q & a|\bama\b|ask us anything|best of|\brerun\b|\breplay\b|\bencore\b",
    re.IGNORECASE,
)


def load_podcast_feeds(topic: str = "market_pulse") -> list[dict]:
    """Load a topic's podcast feeds (name + url) from scraper_sources.yaml -> podcasts[topic]."""
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    feeds = config.get("podcasts", {}).get(topic, [])
    return [{"name": s["name"], "url": s["url"]} for s in feeds]


def select_episode(episodes: list["PodcastEpisode"], max_scan: int = 10) -> "PodcastEpisode | None":
    """Pick the freshest market-relevant episode (feeds are newest-first).

    Skips obvious non-pulse episodes by title (sponsored "Talk Your Book", mailbag,
    Q&A, AMA, best-of/reruns). Only the newest `max_scan` are considered so we stay
    timely. Returns None if none qualify — the orchestrator then advances to the next
    show in the rotation.
    """
    for ep in episodes[:max_scan]:
        if not _SKIP_TITLE.search(ep.title or ""):
            return ep
    return None


@dataclass
class PodcastEpisode:
    """One transcribable podcast episode parsed from an RSS feed."""

    title: str
    audio_url: str  # <enclosure> mp3 — required; this is what we transcribe
    guid: str  # stable id — the transcript cache key
    show_notes: str = ""  # plain text (HTML stripped, zero-width removed)
    page_url: str = ""  # <link> if present, else "" (Megaphone has none)
    published_at: datetime | None = None
    podcast_name: str = ""


def rotation_order(week: int, podcasts: list) -> list:
    """Podcasts reordered for this week: starts at ``week % n`` and wraps around.

    The first item is this week's pick; the rest are the fallback order to try if a
    feed or transcription fails (so a dead show advances to the next, never dropping
    coverage). Deterministic and stateless — same week always yields the same order,
    mirroring the epoch-based topic rotation. Empty list -> empty order.
    """
    n = len(podcasts)
    if n == 0:
        return []
    start = week % n
    return [podcasts[(start + i) % n] for i in range(n)]


def pick_podcast(week: int, podcasts: list):
    """This week's podcast, round-robin by week number. None if the list is empty."""
    order = rotation_order(week, podcasts)
    return order[0] if order else None


def distill_transcript(
    transcript: str,
    *,
    system: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 120.0,
) -> str | None:
    """Distill a raw transcript into 3-5 takeaway bullets via OpenRouter chat.

    The key quality lever (P5.5): turns ~10k unstructured words into the writer's
    actual angle. `system` selects the per-topic distill lens (defaults to the market
    prompt). Non-fatal — returns None on missing key, API error, or empty output.
    """
    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set — skipping distillation")
        return None
    model = model or os.getenv("OPENROUTER_DISTILL_MODEL", DEFAULT_DISTILL_MODEL)
    text = (transcript or "").strip()[:_MAX_TRANSCRIPT_CHARS]
    if not text:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or _DISTILL_SYSTEM},
            {"role": "user", "content": f"Distill this podcast transcript:\n\n{text}"},
        ],
        "temperature": 0.3,
    }
    try:
        resp = httpx.post(
            OPENROUTER_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        bullets = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        logger.warning("Distillation failed", exc_info=True)
        return None
    return bullets or None


def get_cached_transcript(session: Session, guid: str) -> PublisherPodcastTranscript | None:
    """Return the cached transcript row for an episode guid, or None."""
    return session.query(PublisherPodcastTranscript).filter_by(guid=guid).first()


def store_transcript(
    session: Session,
    *,
    guid: str,
    transcript: str,
    podcast_name: str = "",
    episode_title: str = "",
    audio_url: str = "",
    distilled: str | None = None,
) -> PublisherPodcastTranscript:
    """Idempotent upsert of a transcript by guid. Caller commits.

    Re-storing the same guid updates the existing row (never a duplicate). `distilled`
    is only written when provided, so adding the P5.5 bullets later — or re-storing the
    transcript — never wipes existing bullets.
    """
    row = session.query(PublisherPodcastTranscript).filter_by(guid=guid).first()
    if row is None:
        row = PublisherPodcastTranscript(guid=guid)
        session.add(row)
    row.transcript = transcript
    row.podcast_name = podcast_name
    row.episode_title = episode_title
    row.audio_url = audio_url
    if distilled is not None:
        row.distilled = distilled
    session.flush()
    return row


def _clean_notes(raw: str) -> str:
    """Strip HTML tags + zero-width junk from show-notes and collapse whitespace."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    text = text.translate(_ZERO_WIDTH)
    return " ".join(text.split())


def parse_podcast_feed(xml_text: str, podcast_name: str = "") -> list[PodcastEpisode]:
    """Parse a podcast RSS feed into episodes (feed order, i.e. newest-first).

    Items without an audio <enclosure> are skipped — we have nothing to transcribe.
    Show-notes prefer <content:encoded> (fuller) and fall back to <description>.
    """
    soup = BeautifulSoup(xml_text, "xml")
    episodes: list[PodcastEpisode] = []

    for item in soup.find_all("item"):
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url", "").strip() if enclosure else ""
        if not audio_url:
            continue  # no audio -> can't transcribe -> skip

        title_tag = item.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        guid_tag = item.find("guid")
        guid = (guid_tag.get_text(strip=True) if guid_tag else "") or audio_url

        link_tag = item.find("link")
        page_url = link_tag.get_text(strip=True) if link_tag else ""

        # Prefer the fuller content:encoded; fall back to description.
        notes_tag = item.find("content:encoded") or item.find("encoded") or item.find("description")
        show_notes = _clean_notes(notes_tag.get_text() if notes_tag else "")

        published_at = None
        pub_tag = item.find("pubDate")
        if pub_tag and pub_tag.string:
            with contextlib.suppress(Exception):
                published_at = parsedate_to_datetime(pub_tag.string.strip())

        episodes.append(
            PodcastEpisode(
                title=title,
                audio_url=audio_url,
                guid=guid,
                show_notes=show_notes,
                page_url=page_url,
                published_at=published_at,
                podcast_name=podcast_name,
            )
        )

    return episodes


class PodcastInsights:
    """Turn the week's rotated podcast into the Market Pulse ANGLE (Phase 2.10b).

    get_episode_article() picks the rotated show, fetches + parses its feed, smart-
    selects a market-relevant episode, then returns a ScrapedArticle whose summary is
    the distilled bullets (transcribe -> distill, cached by guid). yfinance stays the
    number backbone; this is the angle only. Non-fatal: returns None if nothing is
    usable, so the post still generates from yfinance alone. On any per-feed failure it
    advances to the next show in the rotation order.
    """

    def __init__(self) -> None:
        self.episode: PodcastEpisode | None = None  # the chosen episode, for logging

    def _fetch_feed(self, url: str) -> str:
        """Network boundary — fetch raw feed XML (mocked in tests)."""
        resp = httpx.get(url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        return resp.text

    def _bullets_for(self, session: Session, ep: PodcastEpisode, system: str | None = None) -> str | None:
        """Distilled bullets for an episode — from cache if present, else transcribe+distill.

        `system` selects the per-topic distill lens; the raw transcript stays shared across
        topics (cached by guid), only the distillation differs.
        """
        cached = get_cached_transcript(session, ep.guid)
        if cached and cached.distilled:
            return cached.distilled  # full cache hit — zero API spend

        transcript = cached.transcript if cached else transcribe_audio(ep.audio_url)
        if not transcript:
            return None
        bullets = distill_transcript(transcript, system=system)
        if not bullets:
            return None
        store_transcript(
            session,
            guid=ep.guid,
            transcript=transcript,
            podcast_name=ep.podcast_name,
            episode_title=ep.title,
            audio_url=ep.audio_url,
            distilled=bullets,
        )
        return bullets

    def get_episode_article(
        self,
        session: Session,
        week: int,
        topic: str = "market_pulse",
        feeds: list[dict] | None = None,
        show_offset: int = 0,
    ) -> ScrapedArticle | None:
        """Return the week's podcast angle as a ScrapedArticle (summary = bullets), or None.

        `topic` selects both the feed list (config podcasts[topic]) and the distill lens.
        `show_offset` biases which show is picked first — used when a topic posts more
        than once a week (e.g. biohacker 3x) so each slot pulls a DIFFERENT show.
        """
        feeds = feeds if feeds is not None else load_podcast_feeds(topic)
        system = _DISTILL_BY_TOPIC.get(topic, _DISTILL_SYSTEM)
        for feed in rotation_order(week + show_offset, feeds):
            try:
                xml = self._fetch_feed(feed["url"])
            except Exception:
                logger.warning("Failed to fetch podcast feed %s", feed.get("name"), exc_info=True)
                continue
            ep = select_episode(parse_podcast_feed(xml, podcast_name=feed["name"]))
            if ep is None:
                continue
            bullets = self._bullets_for(session, ep, system=system)
            if not bullets:
                continue
            self.episode = ep
            logger.info("%s podcast angle from %s: %s", topic, ep.podcast_name, ep.title)
            return ScrapedArticle(
                title=ep.title,
                url=ep.page_url or ep.audio_url,
                summary=bullets,
                source=ep.podcast_name,
                published_at=ep.published_at,
            )
        return None
