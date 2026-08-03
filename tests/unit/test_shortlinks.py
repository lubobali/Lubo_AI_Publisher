"""Unit tests for the tiny short-link service (attribution via lubot.ai/go/<code>).

The shortener lives ENTIRELY in the publisher: a short_links table + get_or_create_code /
resolve. Codes redirect to lubot.ai/?utm_... (per platform + campaign). Real DB (publisher_test).
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, PublisherShortLink
from src.shortlinks import CODE_LEN, _utm_dest, generate_code, get_or_create_code, resolve

TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher_test")
_engine = create_engine(TEST_DB_URL)
_Session = sessionmaker(bind=_engine)


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=_engine)
    yield
    _engine.dispose()


@pytest.fixture()
def session():
    s = _Session()
    s.query(PublisherShortLink).delete()
    s.flush()
    yield s
    s.rollback()
    s.close()


class TestUtmDest:
    def test_linkedin_root(self):
        assert _utm_dest("linkedin", "my-agent-2026-08-03") == (
            "/?utm_source=linkedin&utm_medium=post&utm_campaign=my-agent-2026-08-03"
        )

    def test_x_maps_to_twitter(self):
        assert "utm_source=twitter" in _utm_dest("x", "c")

    def test_reddit_medium_is_comment(self):
        assert "utm_source=reddit" in _utm_dest("reddit", "c")
        assert "utm_medium=comment" in _utm_dest("reddit", "c")

    def test_path_is_preserved(self):
        assert _utm_dest("linkedin", "c", "architecture").startswith("/architecture?utm_source=linkedin")

    def test_unknown_platform_used_verbatim(self):
        assert "utm_source=foo" in _utm_dest("foo", "c")


class TestGenerateCode:
    def test_length_and_charset(self):
        code = generate_code()
        assert len(code) == CODE_LEN
        assert code.isalnum()


class TestGetOrCreateAndResolve:
    def test_creates_and_resolves(self, session):
        code = get_or_create_code(session, platform="linkedin", campaign="my-agent-2026-08-03")
        assert len(code) == CODE_LEN
        assert resolve(session, code) == ("/?utm_source=linkedin&utm_medium=post&utm_campaign=my-agent-2026-08-03")

    def test_idempotent_same_platform_campaign_reuses_code(self, session):
        a = get_or_create_code(session, platform="linkedin", campaign="c")
        b = get_or_create_code(session, platform="linkedin", campaign="c")
        assert a == b  # same (platform, campaign) -> same code, no duplicate row

    def test_different_platform_gets_different_code(self, session):
        li = get_or_create_code(session, platform="linkedin", campaign="c")
        x = get_or_create_code(session, platform="x", campaign="c")
        assert li != x
        assert "utm_source=twitter" in resolve(session, x)

    def test_resolve_unknown_code_is_none(self, session):
        assert resolve(session, "zzzzz") is None

    def test_path_roundtrips(self, session):
        code = get_or_create_code(session, platform="linkedin", campaign="c", dest_path="architecture")
        assert resolve(session, code).startswith("/architecture?utm_source=linkedin")
