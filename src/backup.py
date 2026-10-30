"""Backblaze B2 backups (S3-compatible API).

One off-site layer to a private B2 bucket, run weekly:
  - backup_database(): pg_dump the whole DB -> gzip -> db-dumps/  (full restore: posts + KB)
    IDEMPOTENT: the key carries a fingerprint of the dump; when the newest dump in B2 has
    the same fingerprint, nothing changed and nothing is uploaded.
  - prune_old_dumps(): keep only the newest DEFAULT_KEEP_DUMPS dumps (by count, not age, so
    weeks of skipped uploads never age out the only backup).

NON-FATAL: if B2 is not configured (B2_* env missing), run_backup() is a logged no-op and
never crashes the worker. The external boundary (boto3 S3 client + the pg_dump subprocess)
is mocked in tests; the fingerprint, key naming, skip logic and pruning are real.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import subprocess
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

DB_DUMP_PREFIX = "db-dumps/"
DEFAULT_KEEP_DUMPS = int(os.getenv("B2_DUMP_KEEP", "4"))
_REQUIRED = ("B2_KEY_ID", "B2_APP_KEY", "B2_ENDPOINT", "B2_BUCKET")
# pg_dump 17 wraps every dump in "\restrict <random key>" / "\unrestrict <key>" lines.
_RESTRICT_LINE = re.compile(rb"^\\(?:un)?restrict [^\n]*\n?", re.MULTILINE)


def b2_enabled() -> bool:
    """True only when every B2 setting is present."""
    return all(os.getenv(k) for k in _REQUIRED)


def _region_from_endpoint(endpoint: str) -> str:
    """'https://s3.us-east-005.backblazeb2.com' -> 'us-east-005'."""
    host = endpoint.split("//")[-1]
    parts = host.split(".")
    return parts[1] if len(parts) >= 3 else "us-east-005"


def get_client():
    """Build a boto3 S3 client pointed at B2 (boto3 imported lazily so the dep stays optional)."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ["B2_ENDPOINT"],
        aws_access_key_id=os.environ["B2_KEY_ID"],
        aws_secret_access_key=os.environ["B2_APP_KEY"],
        region_name=_region_from_endpoint(os.environ["B2_ENDPOINT"]),
    )


def _dump_database() -> bytes:
    """pg_dump the DB at DATABASE_URL (plain SQL). Raises CalledProcessError on failure."""
    url = os.environ["DATABASE_URL"]
    proc = subprocess.run(
        ["pg_dump", "--no-owner", "--no-privileges", url],
        capture_output=True,
        check=True,
    )
    return proc.stdout


def dump_fingerprint(dump: bytes) -> str:
    """Short content hash of a plain pg_dump, ignoring its random \\restrict key lines.

    >>> a = b"\\\\restrict k1\\nCOPY t;\\n\\\\unrestrict k1\\n"
    >>> dump_fingerprint(a) == dump_fingerprint(a.replace(b"k1", b"k2"))
    True
    """
    return hashlib.sha256(_RESTRICT_LINE.sub(b"", dump)).hexdigest()[:16]


def _list_dumps(client) -> list[dict]:
    """db-dumps/ objects, newest first (by LastModified)."""
    resp = client.list_objects_v2(Bucket=os.environ["B2_BUCKET"], Prefix=DB_DUMP_PREFIX)
    return sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)


def backup_database(client=None, *, now: datetime | None = None) -> str | None:
    """Dump the whole DB and upload it to db-dumps/, unless the newest dump is identical.

    Returns the new object key, or None when nothing changed since the newest dump.
    """
    client = client or get_client()
    now = now or datetime.now(UTC)
    dump = _dump_database()
    fp = dump_fingerprint(dump)
    dumps = _list_dumps(client)
    if dumps and dumps[0]["Key"].endswith(f"-{fp}.sql.gz"):
        logger.info("DB unchanged since %s — skipping B2 upload", dumps[0]["Key"])
        return None
    key = f"{DB_DUMP_PREFIX}publisher-{now:%Y%m%d-%H%M%S}-{fp}.sql.gz"
    client.put_object(Bucket=os.environ["B2_BUCKET"], Key=key, Body=gzip.compress(dump))
    logger.info("DB backup uploaded to B2: %s", key)
    return key


def prune_old_dumps(client=None, *, keep: int = DEFAULT_KEEP_DUMPS) -> int:
    """Delete all but the newest `keep` dumps. Returns # deleted."""
    client = client or get_client()
    bucket = os.environ["B2_BUCKET"]
    deleted = 0
    for obj in _list_dumps(client)[keep:]:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
        deleted += 1
    if deleted:
        logger.info("Pruned %d old DB dumps (keeping newest %d)", deleted, keep)
    return deleted


def run_backup() -> bool:
    """Weekly backup: DB dump (skipped when unchanged) + prune. No-op if B2 unconfigured."""
    if not b2_enabled():
        logger.info("B2 not configured (B2_* env missing) — skipping backup")
        return False
    client = get_client()
    backup_database(client)
    prune_old_dumps(client)
    return True
