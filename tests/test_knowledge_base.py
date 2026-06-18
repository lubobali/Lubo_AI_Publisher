"""Tests for knowledge_base PDF extraction (Phase 2.8 / 15c-1).

pypdf is MOCKED at the boundary — tests never read books/ (gitignored, absent in CI).
clean_text and repeated-line dropping are pure logic, tested on plain strings.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.knowledge_base import (
    _drop_repeated_lines,
    _html_to_text,
    chunk_text,
    clean_text,
    embed_query,
    embed_texts,
    extract_book_text,
    ingest_rss_feed,
    parse_feed_posts,
    store_chunks,
)
from src.models import Base, PublisherKnowledgeBase

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")


@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    # Isolation in a shared DB: delete existing KB rows INSIDE this transaction
    # (so tests see a clean KB), then roll back at teardown — which undoes both the
    # delete and the test's own rows. Real committed knowledge-base data is restored
    # intact. Tests must flush (never commit).
    session = sessionmaker(bind=test_engine)()
    session.query(PublisherKnowledgeBase).delete()
    session.flush()
    yield session
    session.rollback()
    session.close()


def _sentences_text(n):
    """n distinct ~10-word sentences."""
    return " ".join(f"This is sentence number {i} with some filler words here." for i in range(n))


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

    def test_strips_nul_and_control_chars(self):
        # Real PDFs (LLM Foundations, Jurafsky) embed NUL bytes that Postgres TEXT rejects
        out = clean_text("bad\x00text and\x07more\x1f")
        assert "\x00" not in out and "\x07" not in out and "\x1f" not in out
        assert "badtext" in out and "andmore" in out
        # tabs and newlines survive (tab collapses to a space)
        assert clean_text("a\tb") == "a b"

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


class TestChunkText:
    def test_empty_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_short_text_single_chunk(self):
        chunks = chunk_text(_sentences_text(5), target_words=400)
        assert len(chunks) == 1
        assert "sentence number 0" in chunks[0]
        assert "sentence number 4" in chunks[0]

    def test_long_text_multiple_chunks(self):
        chunks = chunk_text(_sentences_text(200), target_words=400, overlap_words=50)
        assert len(chunks) > 1

    def test_no_empty_chunks(self):
        chunks = chunk_text(_sentences_text(200), target_words=100, overlap_words=20)
        assert all(c.strip() for c in chunks)

    def test_chunks_respect_target_size(self):
        target, overlap = 100, 20
        chunks = chunk_text(_sentences_text(300), target_words=target, overlap_words=overlap)
        # never split a sentence -> a chunk may overrun by overlap + at most one sentence (~10 words)
        assert all(len(c.split()) <= target + overlap + 30 for c in chunks)

    def test_overlap_between_consecutive_chunks(self):
        chunks = chunk_text(_sentences_text(200), target_words=50, overlap_words=15)
        assert len(chunks) >= 2
        # tail of chunk N appears at the start of chunk N+1
        tail = " ".join(chunks[0].split()[-8:])
        assert tail in chunks[1]

    def test_never_splits_mid_sentence(self):
        chunks = chunk_text(_sentences_text(200), target_words=50, overlap_words=15)
        # a specific sentence survives intact in some chunk
        assert any("This is sentence number 137 with some filler words here." in c for c in chunks)

    def test_single_oversized_sentence_is_its_own_chunk(self):
        big = "word " * 600  # one 600-word "sentence", no terminators
        chunks = chunk_text(big.strip(), target_words=400)
        assert len(chunks) == 1


def _fake_post_factory(vector):
    """Return an httpx.post stand-in that echoes len(input) embeddings of `vector`."""

    def _fake_post(url, json=None, headers=None, timeout=None):
        n = len(json["input"])
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": [{"index": i, "embedding": list(vector)} for i in range(n)]}
        return resp

    return _fake_post


class TestEmbedding:
    @patch("src.knowledge_base.httpx.post")
    def test_posts_correct_payload(self, mock_post):
        mock_post.side_effect = _fake_post_factory([1.0, 0.0])
        embed_texts(["hello", "world"], input_type="passage", api_key="k")
        url = mock_post.call_args[0][0]
        payload = mock_post.call_args[1]["json"]
        headers = mock_post.call_args[1]["headers"]
        assert url == "https://integrate.api.nvidia.com/v1/embeddings"
        assert payload["model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
        assert payload["input_type"] == "passage"
        assert payload["modality"] == "text"
        assert payload["input"] == ["hello", "world"]
        assert headers["Authorization"] == "Bearer k"

    @patch("src.knowledge_base.httpx.post")
    def test_normalizes_vectors(self, mock_post):
        mock_post.side_effect = _fake_post_factory([3.0, 4.0])  # magnitude 5
        out = embed_texts(["x"], api_key="k")
        assert out[0] == [0.6, 0.8]

    @patch("src.knowledge_base.httpx.post")
    def test_batches_large_input(self, mock_post):
        mock_post.side_effect = _fake_post_factory([1.0, 0.0])
        out = embed_texts([f"t{i}" for i in range(120)], api_key="k", batch_size=50)
        assert mock_post.call_count == 3  # 50 + 50 + 20
        assert len(out) == 120

    @patch("src.knowledge_base.httpx.post")
    def test_orders_by_index(self, mock_post):
        def _out_of_order(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }
            return resp

        mock_post.side_effect = _out_of_order
        out = embed_texts(["a", "b"], api_key="k")
        assert out[0] == [1.0, 0.0]
        assert out[1] == [0.0, 1.0]

    @patch("src.knowledge_base.httpx.post")
    def test_embed_query_uses_query_input_type(self, mock_post):
        mock_post.side_effect = _fake_post_factory([1.0, 0.0])
        vec = embed_query("what is sharding", api_key="k")
        assert isinstance(vec, list) and isinstance(vec[0], float)
        assert mock_post.call_args[1]["json"]["input_type"] == "query"

    def test_empty_texts_returns_empty(self):
        assert embed_texts([], api_key="k") == []

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        try:
            embed_texts(["x"])
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass


class TestStoreChunks:
    def test_inserts_rows(self, db_session):
        n = store_chunks(
            db_session,
            "Designing Data-Intensive Applications",
            "ddia",
            ["chunk a", "chunk b two"],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        db_session.flush()
        assert n == 2
        rows = (
            db_session.query(PublisherKnowledgeBase)
            .filter_by(book_slug="ddia")
            .order_by(PublisherKnowledgeBase.chunk_index)
            .all()
        )
        assert [r.chunk_index for r in rows] == [0, 1]
        assert rows[0].book_title == "Designing Data-Intensive Applications"
        assert rows[1].text == "chunk b two"
        assert rows[1].word_count == 3
        assert rows[0].embedding == [1.0, 0.0]  # JSON round-trips

    def test_reingest_replaces_old_rows(self, db_session):
        store_chunks(db_session, "DDIA", "ddia", ["a", "b", "c"], [[1.0]] * 3)
        db_session.flush()
        store_chunks(db_session, "DDIA", "ddia", ["x", "y"], [[2.0]] * 2)
        db_session.flush()
        rows = db_session.query(PublisherKnowledgeBase).filter_by(book_slug="ddia").all()
        assert len(rows) == 2
        assert {r.text for r in rows} == {"x", "y"}

    def test_isolates_by_slug(self, db_session):
        store_chunks(db_session, "Book A", "a", ["a1", "a2"], [[1.0]] * 2)
        store_chunks(db_session, "Book B", "b", ["b1", "b2", "b3"], [[1.0]] * 3)
        db_session.flush()
        # re-ingesting B must not touch A
        store_chunks(db_session, "Book B", "b", ["b1new"], [[1.0]])
        db_session.flush()
        assert db_session.query(PublisherKnowledgeBase).filter_by(book_slug="a").count() == 2
        assert db_session.query(PublisherKnowledgeBase).filter_by(book_slug="b").count() == 1

    def test_length_mismatch_raises(self, db_session):
        with pytest.raises(ValueError, match="mismatch"):
            store_chunks(db_session, "DDIA", "ddia", ["a", "b"], [[1.0]])


def _unit(v):
    import numpy as np

    a = np.array(v, dtype=np.float32)
    return (a / np.linalg.norm(a)).tolist()


class TestRankByCosine:
    def test_ranks_closest_first(self):
        import numpy as np

        from src.knowledge_base import _rank_by_cosine

        m = np.array([_unit([1, 0, 0]), _unit([0, 1, 0]), _unit([1, 1, 0])], dtype=np.float32)
        q = np.array(_unit([1, 0.1, 0]), dtype=np.float32)
        ranked = _rank_by_cosine(m, q, top_k=3)
        assert ranked[0][0] == 0  # closest to [1,0,0]
        assert ranked[0][1] >= ranked[1][1] >= ranked[2][1]  # scores descending

    def test_empty_matrix(self):
        import numpy as np

        from src.knowledge_base import _rank_by_cosine

        out = _rank_by_cosine(np.zeros((0, 3), dtype=np.float32), np.array([1, 0, 0], dtype=np.float32), 3)
        assert out == []

    def test_respects_top_k(self):
        import numpy as np

        from src.knowledge_base import _rank_by_cosine

        m = np.array([_unit([1, 0]), _unit([0, 1]), _unit([1, 1])], dtype=np.float32)
        assert len(_rank_by_cosine(m, np.array(_unit([1, 0]), dtype=np.float32), top_k=2)) == 2


class TestKnowledgeBaseSearch:
    @patch("src.knowledge_base.embed_query")
    def test_returns_most_relevant_chunk(self, mock_eq, db_session):
        from src.knowledge_base import KnowledgeBase

        embs = [_unit([1, 0, 0]), _unit([0, 1, 0]), _unit([0, 0, 1])]
        store_chunks(db_session, "Book", "b", ["about cats", "about dogs", "about birds"], embs)
        db_session.flush()
        mock_eq.return_value = _unit([0, 1, 0.05])  # closest to "about dogs"
        results = KnowledgeBase(db_session, min_score=0.3).search("anything", top_k=2)
        assert results[0].text == "about dogs"
        assert results[0].book_title == "Book"
        assert results[0].score > 0.9

    @patch("src.knowledge_base.embed_query")
    def test_threshold_filters_irrelevant(self, mock_eq, db_session):
        from src.knowledge_base import KnowledgeBase

        store_chunks(db_session, "Book", "b", ["a", "b"], [_unit([1, 0, 0]), _unit([0, 1, 0])])
        db_session.flush()
        mock_eq.return_value = _unit([0, 0, 1])  # orthogonal -> score ~0, below threshold
        assert KnowledgeBase(db_session, min_score=0.35).search("q") == []

    @patch("src.knowledge_base.embed_query")
    def test_empty_kb_returns_empty_without_embedding(self, mock_eq, db_session):
        from src.knowledge_base import KnowledgeBase

        assert KnowledgeBase(db_session).search("q") == []
        mock_eq.assert_not_called()  # don't waste an API call on an empty KB


class TestIngestBook:
    def test_book_meta(self):
        from src.knowledge_base import _book_meta

        title, slug = _book_meta("/books/Machine-Learning-Yearning-Andrew-Ng.pdf")
        assert slug == "machine-learning-yearning-andrew-ng"
        assert "Machine Learning Yearning" in title

    @patch("src.knowledge_base.embed_texts")
    @patch("src.knowledge_base.extract_book_text")
    def test_ingests_and_stores(self, mock_extract, mock_embed, db_session):
        from src.knowledge_base import ingest_book

        mock_extract.return_value = _sentences_text(200)  # -> multiple chunks
        mock_embed.side_effect = lambda texts, **kw: [[1.0, 0.0]] * len(texts)
        n = ingest_book(db_session, "/books/Some-Book.pdf", api_key="k")
        db_session.flush()
        assert n > 1
        assert db_session.query(PublisherKnowledgeBase).filter_by(book_slug="some-book").count() == n

    @patch("src.knowledge_base.extract_book_text")
    def test_empty_book_stores_nothing(self, mock_extract, db_session):
        from src.knowledge_base import ingest_book

        mock_extract.return_value = ""
        assert ingest_book(db_session, "/books/Empty.pdf", api_key="k") == 0
        assert db_session.query(PublisherKnowledgeBase).filter_by(book_slug="empty").count() == 0

    @patch("src.knowledge_base.embed_texts")
    @patch("src.knowledge_base.extract_book_text")
    def test_reingest_is_idempotent(self, mock_extract, mock_embed, db_session):
        from src.knowledge_base import ingest_book

        mock_embed.side_effect = lambda texts, **kw: [[1.0, 0.0]] * len(texts)
        mock_extract.return_value = _sentences_text(200)
        first = ingest_book(db_session, "/books/Some-Book.pdf", api_key="k")
        db_session.flush()
        mock_extract.return_value = _sentences_text(50)  # smaller second time
        second = ingest_book(db_session, "/books/Some-Book.pdf", api_key="k")
        db_session.flush()
        assert second < first
        assert db_session.query(PublisherKnowledgeBase).filter_by(book_slug="some-book").count() == second


# ---------------------------------------------------------------------------
# RSS-content ingestion (Phase 2.10 — finance blogs -> KB)
# ---------------------------------------------------------------------------

_CONTENT_NS = 'xmlns:content="http://purl.org/rss/1.0/modules/content/"'


def _synth_feed():
    """Synthetic RSS: post one has full content:encoded, post two only a description."""
    body1 = "Investing is simple but not always easy. " * 20
    body2 = "Patience is the real edge over many years. " * 20
    return f"""<?xml version="1.0"?>
<rss version="2.0" {_CONTENT_NS}><channel>
<item><title>Post One</title><link>https://ex.com/one</link>
<content:encoded><![CDATA[<p>{body1}</p><script>tracker()</script>]]></content:encoded>
<description>excerpt one teaser</description></item>
<item><title>Post Two</title><link>https://ex.com/two</link>
<description><![CDATA[<p>{body2}</p>]]></description></item>
</channel></rss>"""


def _synth_feed_extra():
    body3 = "Compounding quietly rewards the patient investor. " * 20
    return f"""<?xml version="1.0"?>
<rss version="2.0" {_CONTENT_NS}><channel>
<item><title>Post Three</title><link>https://ex.com/three</link>
<content:encoded><![CDATA[<p>{body3}</p>]]></content:encoded></item>
</channel></rss>"""


class TestHtmlToText:
    def test_strips_tags_and_scripts(self):
        out = _html_to_text("<p>hello <b>world</b></p><script>bad()</script>")
        assert "hello world" in out
        assert "bad" not in out


class TestParseFeedPosts:
    def test_prefers_content_encoded_over_description(self):
        posts = parse_feed_posts(_synth_feed())
        assert len(posts) == 2
        url, _title, body = posts[0]
        assert url == "https://ex.com/one"
        assert "Investing is simple" in body
        assert "excerpt one teaser" not in body  # full content won, not the teaser

    def test_falls_back_to_description(self):
        posts = parse_feed_posts(_synth_feed())
        assert "Patience is the real edge" in posts[1][2]

    def test_skips_items_without_url(self):
        xml = (
            '<?xml version="1.0"?><rss><channel><item><title>No link</title>'
            "<description>body here words</description></item></channel></rss>"
        )
        assert parse_feed_posts(xml) == []


class TestIngestRssFeed:
    def test_stores_one_slug_per_post(self, db_session):
        with patch("src.knowledge_base.embed_texts", side_effect=lambda texts, **k: [[0.1, 0.2]] * len(texts)):
            n = ingest_rss_feed(db_session, "http://feed", "My Blog", "myblog", fetch=lambda _u: _synth_feed())
        assert n >= 2
        rows = db_session.query(PublisherKnowledgeBase).all()
        slugs = {r.book_slug for r in rows}
        assert len(slugs) == 2
        assert all(s.startswith("myblog-") for s in slugs)
        assert all(r.book_title == "My Blog" for r in rows)

    def test_accumulates_across_runs(self, db_session):
        with patch("src.knowledge_base.embed_texts", side_effect=lambda texts, **k: [[0.1, 0.2]] * len(texts)):
            ingest_rss_feed(db_session, "http://feed", "My Blog", "myblog", fetch=lambda _u: _synth_feed())
            # A later run surfaces a NEW post; old posts must remain (not wiped)
            ingest_rss_feed(db_session, "http://feed", "My Blog", "myblog", fetch=lambda _u: _synth_feed_extra())
        slugs = {r.book_slug for r in db_session.query(PublisherKnowledgeBase).all()}
        assert len(slugs) == 3  # 2 original + 1 new, accumulated

    def test_reingest_same_feed_is_idempotent(self, db_session):
        with patch("src.knowledge_base.embed_texts", side_effect=lambda texts, **k: [[0.1, 0.2]] * len(texts)):
            ingest_rss_feed(db_session, "http://feed", "My Blog", "myblog", fetch=lambda _u: _synth_feed())
            ingest_rss_feed(db_session, "http://feed", "My Blog", "myblog", fetch=lambda _u: _synth_feed())
        slugs = {r.book_slug for r in db_session.query(PublisherKnowledgeBase).all()}
        assert len(slugs) == 2  # same 2 posts, not duplicated
