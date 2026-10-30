"""Tests for the B2 backup module.

External boundary (boto3 S3 client + the pg_dump subprocess) is MOCKED via a fake client
and monkeypatched _dump_database. The real logic under test: enable-gating, region parsing,
the dump fingerprint, skip-when-unchanged, object-key naming, and keep-newest-N pruning.
"""

import gzip
from datetime import UTC, datetime, timedelta

from src import backup


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
        return {"Contents": [o for o in self._listing if o["Key"].startswith(kw.get("Prefix", ""))]}


_ALL_B2 = {
    "B2_KEY_ID": "kid",
    "B2_APP_KEY": "secret",
    "B2_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
    "B2_BUCKET": "lubo-publisher",
}

_NOW = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
_DUMP = b"--\n\\restrict abc123\nCREATE TABLE t (id int);\nCOPY t FROM stdin;\n1\n\\.\n\\unrestrict abc123\n"


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


class TestDumpFingerprint:
    def test_ignores_pg_dump_random_restrict_key(self):
        # pg_dump 17 writes a new random \restrict key into every dump, so the same DB
        # would otherwise never look unchanged.
        other_key = _DUMP.replace(b"abc123", b"zzz999")
        assert backup.dump_fingerprint(_DUMP) == backup.dump_fingerprint(other_key)

    def test_changes_when_data_changes(self):
        changed = _DUMP.replace(b"1\n\\.", b"2\n\\.")
        assert backup.dump_fingerprint(_DUMP) != backup.dump_fingerprint(changed)


class TestBackupDatabase:
    def test_uploads_gzip_dump_with_dated_fingerprint_key(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup, "_dump_database", lambda: _DUMP)
        client = _FakeClient()
        key = backup.backup_database(client, now=_NOW)
        fp = backup.dump_fingerprint(_DUMP)
        assert key == f"db-dumps/publisher-20261101-090000-{fp}.sql.gz"
        assert client.puts[0]["Key"] == key
        assert client.puts[0]["Bucket"] == "lubo-publisher"
        assert gzip.decompress(client.puts[0]["Body"]) == _DUMP

    def test_skips_upload_when_newest_dump_is_unchanged(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup, "_dump_database", lambda: _DUMP)
        fp = backup.dump_fingerprint(_DUMP)
        listing = [
            {"Key": "db-dumps/publisher-20261018-090000-aaaa.sql.gz", "LastModified": _NOW - timedelta(days=14)},
            {"Key": f"db-dumps/publisher-20261025-090000-{fp}.sql.gz", "LastModified": _NOW - timedelta(days=7)},
        ]
        client = _FakeClient(listing=listing)
        assert backup.backup_database(client, now=_NOW) is None
        assert client.puts == []

    def test_uploads_when_only_an_older_dump_matches(self, monkeypatch):
        # Data went A -> B -> back to A: the newest dump (B) differs, so A is uploaded again.
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        monkeypatch.setattr(backup, "_dump_database", lambda: _DUMP)
        fp = backup.dump_fingerprint(_DUMP)
        listing = [
            {"Key": f"db-dumps/publisher-20261018-090000-{fp}.sql.gz", "LastModified": _NOW - timedelta(days=14)},
            {"Key": "db-dumps/publisher-20261025-090000-bbbb.sql.gz", "LastModified": _NOW - timedelta(days=7)},
        ]
        client = _FakeClient(listing=listing)
        assert backup.backup_database(client, now=_NOW) is not None
        assert len(client.puts) == 1


class TestPruneOldDumps:
    def test_keeps_only_newest_n_dumps(self, monkeypatch):
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        listing = [{"Key": f"db-dumps/d{i}.sql.gz", "LastModified": _NOW - timedelta(days=7 * i)} for i in range(6)]
        client = _FakeClient(listing=listing)
        deleted = backup.prune_old_dumps(client, keep=4)
        assert deleted == 2
        assert {d["Key"] for d in client.deletes} == {"db-dumps/d4.sql.gz", "db-dumps/d5.sql.gz"}

    def test_never_deletes_when_at_or_under_keep(self, monkeypatch):
        # Count-based (not age-based): a quiet month with skipped uploads must not
        # age out the only backup.
        monkeypatch.setenv("B2_BUCKET", "lubo-publisher")
        listing = [{"Key": "db-dumps/only.sql.gz", "LastModified": _NOW - timedelta(days=365)}]
        client = _FakeClient(listing=listing)
        assert backup.prune_old_dumps(client, keep=4) == 0
        assert client.deletes == []

    def test_default_keeps_four_weekly_dumps(self):
        assert backup.DEFAULT_KEEP_DUMPS == 4


class TestRunBackup:
    def test_skips_when_not_configured(self, monkeypatch):
        for k in _ALL_B2:
            monkeypatch.delenv(k, raising=False)
        assert backup.run_backup() is False

    def test_uploads_only_the_db_dump(self, monkeypatch):
        for k, v in _ALL_B2.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(backup, "_dump_database", lambda: _DUMP)
        client = _FakeClient()
        monkeypatch.setattr(backup, "get_client", lambda: client)
        assert backup.run_backup() is True
        assert [p["Key"].startswith("db-dumps/") for p in client.puts] == [True]
