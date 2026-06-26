"""Backblaze B2 backups (S3-compatible API).

Two off-site layers to a private B2 bucket:
  - backup_database(): pg_dump the whole DB -> gzip -> db-dumps/  (full restore: posts + KB)
  - archive_posts():   each post -> posts/<id>/post.json (+ its screenshot)  (readable archive)
plus prune_old_dumps() retention for the rolling daily dumps.

NON-FATAL: if B2 is not configured (B2_* env missing), run_backup() is a logged no-op and
never crashes the worker. The external boundary (boto3 S3 client + the pg_dump subprocess)
is mocked in tests; the key naming, JSON serialization and retention math are real.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta

from src.models import PublisherPost

logger = logging.getLogger(__name__)

DB_DUMP_PREFIX = "db-dumps/"
POSTS_PREFIX = "posts/"
DEFAULT_RETENTION_DAYS = int(os.getenv("B2_DUMP_RETENTION_DAYS", "30"))
_REQUIRED = ("B2_KEY_ID", "B2_APP_KEY", "B2_ENDPOINT", "B2_BUCKET")


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
    """pg_dump the DB at DATABASE_URL, gzip-compressed. Raises CalledProcessError on failure."""
    url = os.environ["DATABASE_URL"]
    proc = subprocess.run(
        ["pg_dump", "--no-owner", "--no-privileges", url],
        capture_output=True,
        check=True,
    )
    return gzip.compress(proc.stdout)


def backup_database(client=None, *, now: datetime | None = None) -> str:
    """Dump the whole DB and upload it to db-dumps/. Returns the object key."""
    client = client or get_client()
    now = now or datetime.now(UTC)
    key = f"{DB_DUMP_PREFIX}publisher-{now:%Y%m%d-%H%M%S}.sql.gz"
    client.put_object(Bucket=os.environ["B2_BUCKET"], Key=key, Body=_dump_database())
    logger.info("DB backup uploaded to B2: %s", key)
    return key


def post_to_dict(post: PublisherPost) -> dict:
    """Serialize a post to a plain dict for the readable archive (omits the big embedding)."""

    def _dt(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return {
        "id": post.id,
        "posted_at": _dt(post.posted_at),
        "topic_category": post.topic_category,
        "topic_title": post.topic_title,
        "source_url": post.source_url,
        "post_text": post.post_text,
        "image_path": post.image_path,
        "hashtags": list(post.hashtags) if post.hashtags else [],
        "linkedin_post_urn": post.linkedin_post_urn,
        "posting_time_ct": post.posting_time_ct,
        "day_of_week": post.day_of_week,
        "status": post.status,
        "langfuse_trace_id": post.langfuse_trace_id,
        "created_at": _dt(post.created_at),
    }


def archive_post(post: PublisherPost, client=None) -> int:
    """Upload one post's JSON (+ its screenshot if the file exists). Returns # objects uploaded."""
    client = client or get_client()
    bucket = os.environ["B2_BUCKET"]
    client.put_object(
        Bucket=bucket,
        Key=f"{POSTS_PREFIX}{post.id}/post.json",
        Body=json.dumps(post_to_dict(post), indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    uploaded = 1
    img = post.image_path
    if img and os.path.exists(img):
        with open(img, "rb") as f:
            client.put_object(
                Bucket=bucket,
                Key=f"{POSTS_PREFIX}{post.id}/{os.path.basename(img)}",
                Body=f.read(),
                ContentType="image/png",
            )
        uploaded += 1
    return uploaded


def archive_posts(session, client=None) -> int:
    """Archive every post in the DB (one failure does not abort the rest). Returns # objects."""
    client = client or get_client()
    total = 0
    for post in session.query(PublisherPost).all():
        try:
            total += archive_post(post, client)
        except Exception:
            logger.exception("Failed to archive post %s", getattr(post, "id", "?"))
    logger.info("Archived posts to B2 (%d objects)", total)
    return total


def prune_old_dumps(client=None, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: datetime | None = None) -> int:
    """Delete db-dumps older than retention_days (by LastModified). Returns # deleted."""
    client = client or get_client()
    bucket = os.environ["B2_BUCKET"]
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    resp = client.list_objects_v2(Bucket=bucket, Prefix=DB_DUMP_PREFIX)
    deleted = 0
    for obj in resp.get("Contents", []):
        last_modified = obj.get("LastModified")
        if last_modified and last_modified < cutoff:
            client.delete_object(Bucket=bucket, Key=obj["Key"])
            deleted += 1
    if deleted:
        logger.info("Pruned %d DB dumps older than %dd", deleted, retention_days)
    return deleted


def run_backup(session) -> bool:
    """Full nightly backup: DB dump + per-post files + prune. No-op if B2 unconfigured."""
    if not b2_enabled():
        logger.info("B2 not configured (B2_* env missing) — skipping backup")
        return False
    client = get_client()
    backup_database(client)
    archive_posts(session, client)
    prune_old_dumps(client)
    return True
