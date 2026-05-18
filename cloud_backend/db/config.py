"""PostgreSQL async database configuration."""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/attendance"
)


def get_database_url() -> str:
    """Return the async SQLAlchemy database URL."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_database_url_sync() -> str:
    """Return a sync PostgreSQL URL for optional sync tooling."""
    url = get_database_url()
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "", 1)
    return url
