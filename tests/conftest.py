"""Shared test fixtures for LuBot Publisher."""

import os

# Local dev: port 5433 (Docker mapped), CI: port 5432 (service container)
# DATABASE_URL env var takes precedence if set
os.environ.setdefault("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher")
