"""Knowledge base — extract clean text from technical-book PDFs for the RAG pipeline.

Phase 2.8 / 15c-1: PDF extraction only. Chunking, embedding, storage, and
retrieval land in later substeps. pypdf reads a local file; everything else
here is pure text processing (so it is unit-tested without touching books/).
"""

import logging
import re
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

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
