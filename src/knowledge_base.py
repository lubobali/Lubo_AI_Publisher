"""Knowledge base — extract clean text from technical-book PDFs for the RAG pipeline.

Phase 2.8 / 15c-1: PDF extraction only. Chunking, embedding, storage, and
retrieval land in later substeps. pypdf reads a local file; everything else
here is pure text processing (so it is unit-tested without touching books/).
"""

import hashlib
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from bs4 import BeautifulSoup
from pypdf import PdfReader
from sqlalchemy.orm import Session

from src.models import PublisherKnowledgeBase

logger = logging.getLogger(__name__)

# Min cosine for a chunk to be worth injecting (related query/passage ~0.43 in live test).
DEFAULT_MIN_SCORE = 0.35

# NVIDIA NeMo Retriever multimodal embedder (Phase 2.8). 2048-dim, 8192-token.
# Same model + pattern as lubot staging PDF RAG v2 (verified vs NVIDIA live docs).
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
DEFAULT_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
EMBED_DIM = 2048
EMBED_BATCH_SIZE = 50
EMBED_TIMEOUT_S = 60

_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")  # a line that is only a page number
_INLINE_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
# NUL + other control chars (keep \t=09 and \n=0a). Postgres TEXT rejects NUL.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DIGITS_RE = re.compile(r"\d+")


def _norm_line(line: str) -> str:
    """Normalize a line for repeat-detection: strip + collapse digits to '#'.

    Lets footers that carry a changing page number ("Page 8 …", "Page 9 …")
    collapse to one template so they are recognized as running footers.
    """
    return _DIGITS_RE.sub("#", line.strip())


def _extract_pages(path: str | Path) -> list[str]:
    """Return text for each non-empty page of the PDF (raw, uncleaned)."""
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return pages


def _drop_repeated_lines(
    pages: list[str],
    min_pages: int = 5,
    ratio: float = 0.3,
    max_len: int = 80,
) -> list[str]:
    """Remove short lines that repeat across many pages (running headers/footers).

    Only short lines (<= max_len) are eligible — long repeated text is likely real
    content, not a header. No-op when there are too few pages to judge.
    """
    if len(pages) < min_pages:
        return pages

    counts: Counter[str] = Counter()
    for page in pages:
        seen = {_norm_line(ln) for ln in page.splitlines() if ln.strip() and len(ln.strip()) <= max_len}
        counts.update(seen)

    threshold = max(2, int(len(pages) * ratio))
    repeated = {key for key, c in counts.items() if c >= threshold}
    if not repeated:
        return pages

    def _keep(line: str) -> bool:
        s = line.strip()
        return not (s and len(s) <= max_len and _norm_line(line) in repeated)

    return ["\n".join(ln for ln in page.splitlines() if _keep(ln)) for page in pages]


def clean_text(text: str) -> str:
    """Normalize whitespace, strip control chars, and drop page-number-only lines."""
    text = text.replace("\f", "\n")
    text = _CONTROL_RE.sub("", text)  # strip NUL/control bytes (Postgres TEXT rejects NUL)
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.splitlines() if not _PAGE_NUMBER_RE.match(line)]
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def extract_book_text(path: str | Path) -> str:
    """Extract clean full text from a PDF.

    Skips empty pages, drops running headers/footers and page numbers, and
    normalizes whitespace. Returns one cleaned string for the whole book.
    """
    pages = _drop_repeated_lines(_extract_pages(path))
    cleaned = clean_text("\n\n".join(pages))
    logger.info("Extracted %d chars from %s", len(cleaned), path)
    return cleaned


# ---------------------------------------------------------------------------
# Chunking (Phase 2.8 / 15c-2)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence units, respecting paragraph breaks first."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        for sent in _SENTENCE_SPLIT_RE.split(para):
            sent = sent.strip()
            if sent:
                units.append(sent)
    return units


def _wc(s: str) -> int:
    return len(s.split())


def chunk_text(text: str, target_words: int = 400, overlap_words: int = 50) -> list[str]:
    """Split text into ~target_words chunks with ~overlap_words of carry-over.

    Splits only on sentence boundaries — a sentence is never cut in half. A lone
    sentence longer than target_words becomes its own chunk. Each chunk (after the
    first) is prefixed with the trailing sentences of the previous chunk for overlap.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []

    n = len(sentences)

    # Pack sentences into base groups, each <= target_words (except a lone oversized sentence).
    groups: list[tuple[int, int]] = []
    i = 0
    while i < n:
        j, words = i, 0
        while j < n:
            w = _wc(sentences[j])
            if j > i and words + w > target_words:
                break
            words += w
            j += 1
        groups.append((i, j))
        i = j

    # Add backward overlap: each group after the first starts ~overlap_words earlier.
    chunks: list[str] = []
    for k, (start, end) in enumerate(groups):
        s = start
        if k > 0:
            ow, b = 0, start
            while b > 0 and ow < overlap_words:
                b -= 1
                ow += _wc(sentences[b])
            s = b
        chunks.append(" ".join(sentences[s:end]))
    return chunks


# ---------------------------------------------------------------------------
# Embedding client — NVIDIA NIM (Phase 2.8 / 15c-3)
# ---------------------------------------------------------------------------


def _embed_model() -> str:
    """Model id — env-overridable so a vendor rotation is a 1-line .env change."""
    return os.getenv("NVIDIA_VLM_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def _l2_normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    return vec if mag == 0 else [x / mag for x in vec]


def _embed_batch(texts: list[str], input_type: str, api_key: str, model: str) -> list[list[float]]:
    """POST one batch to NIM and return L2-normalized vectors in input order."""
    payload = {
        "input": texts,
        "model": model,
        "input_type": input_type,  # "passage" for docs, "query" for searches
        "modality": "text",
        "encoding_format": "float",
        "truncate": "END",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = httpx.post(NVIDIA_EMBED_URL, json=payload, headers=headers, timeout=EMBED_TIMEOUT_S)
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
    return [_l2_normalize(item["embedding"]) for item in data]


def embed_texts(
    texts: list[str],
    input_type: str = "passage",
    *,
    api_key: str | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
) -> list[list[float]]:
    """Embed a list of texts via NVIDIA NIM. Batched, L2-normalized, order-preserving."""
    if not texts:
        return []
    api_key = api_key or os.getenv("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY not set — cannot embed")
    model = _embed_model()

    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        out.extend(_embed_batch(texts[i : i + batch_size], input_type, api_key, model))
    return out


def embed_query(text: str, *, api_key: str | None = None) -> list[float]:
    """Embed a single search query (input_type='query')."""
    return embed_texts([text], input_type="query", api_key=api_key)[0]


# ---------------------------------------------------------------------------
# Storage (Phase 2.8 / 15c-4)
# ---------------------------------------------------------------------------


def store_chunks(
    session: Session,
    book_title: str,
    book_slug: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> int:
    """Replace all rows for book_slug with these chunks + embeddings (idempotent re-ingest).

    Caller commits. Returns the number of chunks stored.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch")

    session.query(PublisherKnowledgeBase).filter_by(book_slug=book_slug).delete(synchronize_session=False)
    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
        session.add(
            PublisherKnowledgeBase(
                book_title=book_title,
                book_slug=book_slug,
                chunk_index=idx,
                text=chunk,
                word_count=len(chunk.split()),
                embedding=emb,
            )
        )
    session.flush()
    logger.info("Stored %d chunks for %s", len(chunks), book_slug)
    return len(chunks)


def _book_meta(path: str | Path) -> tuple[str, str]:
    """Derive (title, slug) from a PDF filename. Title is metadata only (never shown)."""
    stem = Path(path).stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    title = re.sub(r"[_-]+", " ", stem).strip()
    return title, slug


def ingest_book(
    session: Session,
    path: str | Path,
    *,
    api_key: str | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
) -> int:
    """Full ingest for one book: extract -> chunk -> embed -> store. Returns chunk count.

    Idempotent per book (store_chunks replaces existing rows for the slug). Caller commits.
    """
    title, slug = _book_meta(path)
    chunks = chunk_text(extract_book_text(path))
    if not chunks:
        logger.warning("No chunks extracted from %s", path)
        store_chunks(session, title, slug, [], [])  # clear any stale rows for this slug
        return 0
    embeddings = embed_texts(chunks, input_type="passage", api_key=api_key, batch_size=batch_size)
    return store_chunks(session, title, slug, chunks, embeddings)


# ---------------------------------------------------------------------------
# RSS-content ingestion — finance blogs -> KB (Phase 2.10, Stock Talk grounding)
#
# Robust alternative to per-page scraping: pull each post's body from the feed
# (content:encoded, falling back to description), chunk/embed/store ONE slug per
# post so re-running ACCUMULATES (old posts that scroll off the feed are kept).
# ---------------------------------------------------------------------------

_FEED_USER_AGENT = "Mozilla/5.0 (compatible; LuBotPublisher/1.0; +https://lubot.ai)"


def _html_to_text(html: str) -> str:
    """Strip HTML to clean plain text (drops script/style), then normalize."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True))


def parse_feed_posts(xml_text: str) -> list[tuple[str, str, str]]:
    """Parse RSS items into (url, title, body_text).

    Prefers full content (content:encoded), falls back to description. Skips
    items missing a url or with empty body.
    """
    soup = BeautifulSoup(xml_text, "xml")
    posts: list[tuple[str, str, str]] = []
    for item in soup.find_all("item"):
        link = item.find("link")
        title = item.find("title")
        encoded = item.find("encoded")  # content:encoded (local name under xml parser)
        desc = item.find("description")
        html = (encoded.get_text() if encoded else "") or (desc.get_text() if desc else "")
        body = _html_to_text(html)
        url = link.get_text(strip=True) if link else ""
        if url and body:
            posts.append((url, title.get_text(strip=True) if title else "", body))
    return posts


def _fetch_feed(url: str) -> str:
    """Fetch raw RSS XML over HTTP. Separated out so tests inject a fake fetcher."""
    resp = httpx.get(url, headers={"User-Agent": _FEED_USER_AGENT}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def ingest_rss_feed(
    session: Session,
    feed_url: str,
    blog_title: str,
    blog_slug: str,
    *,
    fetch=None,
    api_key: str | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
    max_posts: int = 10,
) -> int:
    """Ingest a finance blog's RSS posts into the KB. Returns total chunks stored.

    Each post is stored under its own slug (blog_slug + url hash) so repeated runs
    accumulate posts over time instead of wiping the archive. Caller commits.
    blog_title is metadata only (never shown / never named in posts).
    """
    fetch = fetch or _fetch_feed
    posts = parse_feed_posts(fetch(feed_url))[:max_posts]
    total = 0
    for url, _title, body in posts:
        chunks = chunk_text(body)
        if not chunks:
            continue
        embeddings = embed_texts(chunks, input_type="passage", api_key=api_key, batch_size=batch_size)
        slug = f"{blog_slug}-{hashlib.md5(url.encode()).hexdigest()[:8]}"
        total += store_chunks(session, blog_title, slug, chunks, embeddings)
    logger.info("Ingested %d chunks from %d posts of %s", total, len(posts), blog_slug)
    return total


# ---------------------------------------------------------------------------
# Retrieval (Phase 2.8 / 15c-5)
# ---------------------------------------------------------------------------


@dataclass
class RetrievedChunk:
    """One book chunk returned by a knowledge-base search."""

    text: str
    book_title: str
    score: float


def _rank_by_cosine(matrix: np.ndarray, query: np.ndarray, top_k: int) -> list[tuple[int, float]]:
    """Return (row_index, score) for the top_k rows, highest cosine first.

    Both matrix rows and query are L2-normalized in production, so the dot
    product IS the cosine similarity.
    """
    if matrix.shape[0] == 0:
        return []
    scores = matrix @ query
    order = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in order]


class KnowledgeBase:
    """Searches stored book chunks by embedding similarity (cached numpy matrix)."""

    def __init__(self, session: Session, min_score: float = DEFAULT_MIN_SCORE):
        self.session = session
        self.min_score = min_score
        self._matrix: np.ndarray | None = None
        self._meta: list[tuple[str, str]] | None = None  # (text, book_title) per row

    def _load(self) -> None:
        """Load all chunk embeddings into an in-memory matrix once."""
        if self._matrix is not None:
            return
        rows = self.session.query(PublisherKnowledgeBase).all()
        self._meta = [(r.text, r.book_title) for r in rows]
        self._matrix = (
            np.array([r.embedding for r in rows], dtype=np.float32)
            if rows
            else np.zeros((0, EMBED_DIM), dtype=np.float32)
        )

    def search(
        self,
        query: str,
        top_k: int = 3,
        min_score: float | None = None,
        *,
        api_key: str | None = None,
    ) -> list[RetrievedChunk]:
        """Embed the query and return up to top_k chunks scoring >= min_score."""
        threshold = self.min_score if min_score is None else min_score
        self._load()
        if not self._meta:  # empty KB — skip the API call
            return []

        q = np.asarray(embed_query(query, api_key=api_key), dtype=np.float32)
        results: list[RetrievedChunk] = []
        for i, score in _rank_by_cosine(self._matrix, q, top_k):
            if score < threshold:
                continue
            text, title = self._meta[i]
            results.append(RetrievedChunk(text=text, book_title=title, score=score))
        return results
