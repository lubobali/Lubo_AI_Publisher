"""Audio transcription via Deepgram (Phase 2.10b).

transcribe_audio(url) sends the episode's audio URL to Deepgram's pre-recorded
endpoint, which downloads + transcribes server-side — no local download, no ffmpeg,
no chunking — and handles hour-long podcasts (our episodes are 60-90 min / 75-95 MB,
far over OpenRouter's base64/JSON transcription limits, which is why we use a dedicated
long-form ASR here).

Deepgram: POST https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&
punctuate=true with header "Authorization: Token <key>" and body {"url": <audio_url>}.
Transcript at results.channels[0].alternatives[0].transcript.

Non-fatal: any failure (no key, API error, empty transcript) returns None so the
Market Pulse post still generates from the yfinance numbers alone. The ASR provider
lives behind THIS one function, so swapping backends later is a change here only —
callers (PodcastInsights) are unaffected.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEFAULT_MODEL = "nova-3"  # latest Deepgram model; override via DEEPGRAM_MODEL


def transcribe_audio(
    audio_url: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 300.0,
) -> str | None:
    """Transcribe a podcast episode (by URL) to text via Deepgram. None on any failure."""
    api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        logger.warning("DEEPGRAM_API_KEY not set — skipping transcription")
        return None
    model = model or os.getenv("DEEPGRAM_MODEL", DEFAULT_MODEL)

    try:
        resp = httpx.post(
            DEEPGRAM_URL,
            params={"model": model, "smart_format": "true", "punctuate": "true"},
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={"url": audio_url},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        transcript = (data["results"]["channels"][0]["alternatives"][0].get("transcript") or "").strip()
    except Exception:
        logger.warning("Deepgram transcription failed for %s", audio_url, exc_info=True)
        return None

    if not transcript:
        logger.warning("Deepgram returned an empty transcript for %s", audio_url)
        return None
    return transcript
