"""Shared test fixtures for LuBot Publisher.

CRITICAL: tests run against a DEDICATED, disposable database (publisher_test),
NEVER the application DB (publisher). The app DB holds persistent data — the
RAG knowledge base and posts — and several test fixtures call drop_all()/delete(),
which would wipe it. Keeping a separate test DB is what makes the ingested book
chunks survive test runs.

Local dev: defaults to publisher_test on the Docker Postgres (auto-created here).
CI: sets DATABASE_URL to its own fresh service container, which we respect.
"""

import os
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql

DEFAULT_TEST_DB = "postgresql://publisher:publisher_dev@localhost:5433/publisher_test"


def _ensure_database(url: str) -> None:
    """Create the target database if it doesn't exist (local dev convenience)."""
    dbname = urlparse(url).path.lstrip("/")
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    try:
        conn = psycopg2.connect(admin_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        conn.close()
    except Exception:
        # CI or restricted setups may not permit this; tests will surface a clear
        # connection error if the configured DB is genuinely missing.
        pass


# Respect an explicit DATABASE_URL (CI), otherwise use the dedicated test DB.
os.environ.setdefault("DATABASE_URL", DEFAULT_TEST_DB)
_ensure_database(os.environ["DATABASE_URL"])

# Langfuse is OFF in production by default; tests turn it on to validate the
# tracing wiring (the SDK is mocked in tests, so there is no real API cost).
os.environ.setdefault("LANGFUSE_ENABLED", "true")
