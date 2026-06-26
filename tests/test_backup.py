"""Tests for the B2 backup module.

External boundary (boto3 S3 client + the pg_dump subprocess) is MOCKED via a fake client
and monkeypatched _dump_database. The real logic under test: enable-gating, region parsing,
post serialization, object-key naming, image handling, and retention math.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import mock_open, patch

from src import backup
from src.models import PublisherPost


class _FakeClient:
    """Captures S3 calls so tests can assert on keys/bodies without touching the network."""

    def __init__(self, listing=None):
        self.puts = []
        self.deletes = []
        self._listing = listing or []

    def put_object(self, **kw):
        self.puts.append(kw)

    def delete_object(self, **kw):
        self.deletes.append(kw)

    def list_objects_v2(self, **kw):
        return {"Contents": self._listing}


class _FakeSession:
    def __init__(self, items):
        self._items = items

    def query(self, _model):
        return self

    def all(self):
        return self._items


def _post(**over):
    defaults = dict(
        id=7,
        posted_at=datetime(2026, 6, 26, 20, 0, tzinfo=UTC),
        topic_category="ai_news",
        topic_title="How agents are transforming work",
        source_url="https://openai.com/index/x",
        post_text="some post body",
        image_path=None,
        hashtags=["AI", "Agents"],
        linkedin_post_urn="urn:li:share:1",
        posting_time_ct="15:30",
        day_of_week="Friday",
        status="published",
        langfuse_trace_id=None,
        created_at=datetime(2026, 6, 26, 19, 0, tzinfo=UTC),
        post_embedding=[0.1, 0.2, 0.3],
    )
    defaults.update(over)
    return PublisherPost(**defaults)


_ALL_B2 = {
    "B2_KEY_ID": "kid",
    "B2_APP_KEY": "secret",
    "B2_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
    "B2_BUCKET": "lubo-publisher",
}


class TestEnabledAndRegion:
    def test_b2_enabled_true_when_all_set(self, monkeypatch):
        for k, v in _ALL_B2.items():
            monkeypatch.setenv(k, v)
        assert backup.b2_enabled() is True

    def test_b2_enabled_false_when_missing(self, monkeypatch):
        for k in _ALL_B2:
            monkeypatch.delenv(k, raising=False)
        assert backup.b2_enabled() is False

    def test_region_parsed_from_endpoint(self):
        assert backup._region_from_endpoint("https://s3.us-east-005.backblazeb2.com") == "us-east-005"
        assert backup._region_from_endpoint("https://s3.eu-central-003.backblazeb2.com") == "eu-central-003"


class TestPostSerialization:
    def test_serializes_fields_and_iso_dates(self):
        d = backup.post_to_dict(_post())
        assert d["id"] == 7
        assert d["topic_category"] == "ai_news"
        assert d["posted_at"] == "2026-06-26T20:00:00+00:00"  # datetime -> ISO
        assert d["hashtags"] == ["AI", "Agents"]
        assert "post_embedding" not in d  # the big vector is omitted from the readable archive

    def test_handles_empty_hashtags(self):
        assert backup.post_to_dict(_post(hashtags=None))["hashtags"] == []


class TestBackupDatabase:
    def test_uploads_gzip_dump_with_dated_key(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup, "_dump_database", lambda: b"GZIP-BYTES")
        client = _FakeClient()
        key = backup.backup_database(client, now=datetime(2026, 6, 26, 4, 0, 0, tzinfo=UTC))
        assert key == "db-dumps/publisher-20260626-040000.sql.gz"
        assert client.puts[0]["Key"] == key
        assert client.puts[0]["Body"] == b"GZIP-BYTES"
        assert client.puts[0]["Bucket"] == "lubo-publisher"


class TestArchivePost:
    def test_uploads_json_only_when_no_image(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        client = _FakeClient()
        n = backup.archive_post(_post(image_path=None), client)
        assert n == 1
        assert client.puts[0]["Key"] == "posts/7/post.json"
        assert client.puts[0]["ContentType"] == "application/json"
        body = json.loads(client.puts[0]["Body"].decode())
        assert body["topic_title"] == "How agents are transforming work"

    def test_uploads_image_when_file_exists(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup.os.path, "exists", lambda _p: True)
        client = _FakeClient()
        with patch("builtins.open", mock_open(read_data=b"PNGDATA")):
            n = backup.archive_post(_post(image_path="/app/screenshots/abc.png"), client)
        assert n == 2
        keys = [p["Key"] for p in client.puts]
        assert "posts/7/post.json" in keys
        assert "posts/7/abc.png" in keys

    def test_skips_image_when_file_missing(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup.os.path, "exists", lambda _p: False)
        client = _FakeClient()
        n = backup.archive_post(_post(image_path="/app/screenshots/missing.png"), client)
        assert n == 1  # only the json


class TestArchivePosts:
    def test_archives_every_post(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        session = _FakeSession([_post(id=1), _post(id=2)])
        client = _FakeClient()
        total = backup.archive_posts(session, client)
        assert total == 2
        assert {p["Key"] for p in client.puts} == {"posts/1/post.json", "posts/2/post.json"}


class TestPruneOldDumps:
    def test_deletes_only_dumps_past_retention(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        now = datetime(2026, 6, 26, tzinfo=UTC)
        listing = [
            {"Key": "db-dumps/old.sql.gz", "LastModified": now - timedelta(days=40)},
            {"Key": "db-dumps/recent.sql.gz", "LastModified": now - timedelta(days=5)},
        ]
        client = _FakeClient(listing=listing)
        deleted = backup.prune_old_dumps(client, retention_days=30, now=now)
        assert deleted == 1
        assert client.deletes == [{"Bucket": "lubo-publisher", "Key": "db-dumps/old.sql.gz"}]


class TestRunBackup:
    def test_skips_when_not_configured(self, monkeypatch):
        for k in _ALL_B2:
            monkeypatch.delenv(k, raising=False)
        assert backup.run_backup(_FakeSession([])) is False
