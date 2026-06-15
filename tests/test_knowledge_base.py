"""Tests for knowledge_base PDF extraction (Phase 2.8 / 15c-1).

pypdf is MOCKED at the boundary — tests never read books/ (gitignored, absent in CI).
clean_text and repeated-line dropping are pure logic, tested on plain strings.
"""

from unittest.mock import MagicMock, patch

from src.knowledge_base import (
    _drop_repeated_lines,
    clean_text,
    extract_book_text,
)


def _mock_reader(page_texts):
    """Build a fake pypdf PdfReader whose pages return the given texts."""
    reader = MagicMock()
    pages = []
    for t in page_texts:
        pg = MagicMock()
        pg.extract_text.return_value = t
        pages.append(pg)
    reader.pages = pages
    return reader


class TestExtractBookText:
    @patch("src.knowledge_base.PdfReader")
    def test_concatenates_pages(self, mock_reader_cls):
        mock_reader_cls.return_value = _mock_reader(["Page one text.", "Page two text."])
        out = extract_book_text("/fake/book.pdf")
        assert "Page one text." in out
        assert "Page two text." in out

    @patch("src.knowledge_base.PdfReader")
    def test_skips_empty_and_whitespace_pages(self, mock_reader_cls):
        mock_reader_cls.return_value = _mock_reader(["real content here", "   ", "", "more content"])
        out = extract_book_text("/fake/book.pdf")
        assert "real content here" in out
        assert "more content" in out
        # no giant gap from the blank pages
        assert "\n\n\n" not in out

    @patch("src.knowledge_base.PdfReader")
    def test_handles_none_extract_text(self, mock_reader_cls):
        mock_reader_cls.return_value = _mock_reader([None, "kept text", None])
        out = extract_book_text("/fake/book.pdf")
        assert out == "kept text"

    @patch("src.knowledge_base.PdfReader")
    def test_returns_string(self, mock_reader_cls):
        mock_reader_cls.return_value = _mock_reader(["something"])
        assert isinstance(extract_book_text("/fake/book.pdf"), str)


class TestCleanText:
    def test_collapses_inline_whitespace(self):
        assert clean_text("a     b\tc") == "a b c"

    def test_collapses_blank_lines(self):
        assert clean_text("para one\n\n\n\n\npara two") == "para one\n\npara two"

    def test_removes_page_number_lines(self):
        assert "42" not in clean_text("real text\n42\nmore text").split("\n")

    def test_strips_form_feed(self):
        assert "\f" not in clean_text("page a\fpage b")

    def test_empty_input(self):
        assert clean_text("") == ""


class TestDropRepeatedLines:
    def test_drops_running_header(self):
        header = "Designing Data-Intensive Applications"
        bodies = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
        pages = [f"{header}\nunique body {w}" for w in bodies]
        out = _drop_repeated_lines(pages)
        assert all(header not in p for p in out)
        assert any("unique body gamma" in p for p in out)

    def test_keeps_lines_when_too_few_pages(self):
        pages = ["HEADER\nbody a", "HEADER\nbody b"]  # below min_pages
        assert _drop_repeated_lines(pages) == pages

    def test_drops_page_numbered_footer(self):
        # Real case (ML Yearning): footer carries a CHANGING page number, so the
        # lines are not identical — must still be detected as a running footer.
        footer = "Page {n} Machine Learning Yearning Draft Andrew Ng"
        body = "this is a normal long paragraph of unique body content for page number {n} in the book"
        pages = [f"{body.format(n=i)}\n{footer.format(n=i)}" for i in range(8)]
        out = _drop_repeated_lines(pages)
        assert all("Machine Learning Yearning" not in p for p in out)
        assert any("unique body content for page number 3" in p for p in out)

    def test_keeps_long_repeated_lines(self):
        # A long line (> max_len) that repeats is probably real content, not a header — keep it
        long_line = (
            "this is a genuinely long sentence of real body content that just happens to repeat across the pages"
        )
        assert len(long_line) > 80
        pages = [f"{long_line}\nbody {i}" for i in range(6)]
        out = _drop_repeated_lines(pages)
        assert any(long_line in p for p in out)
