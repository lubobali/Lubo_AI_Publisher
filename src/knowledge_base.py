"""Knowledge base — extract clean text from technical-book PDFs for the RAG pipeline.

Phase 2.8 / 15c-1: PDF extraction only. Chunking, embedding, storage, and
retrieval land in later substeps. pypdf reads a local file; everything else
here is pure text processing (so it is unit-tested without touching books/).
"""

import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

import httpx
from pypdf import PdfReader

logger = logging.getLogger(__name__)

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
    """Normalize whitespace and drop page-number-only lines."""
    text = text.replace("\f", "\n")
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
