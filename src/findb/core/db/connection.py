"""PostgreSQL connection helpers.

The project intentionally uses raw SQL plus psycopg while learning the database
layer. Keep SQL in `.sql` files or explicit query strings so the Postgres
behavior stays visible.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection

from findb.config import settings


def normalize_database_url(database_url: str) -> str:
    """Accept old SQLAlchemy-style URLs while using psycopg directly."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect() -> Iterator[Connection[tuple[Any, ...]]]:
    """Open a psycopg connection using the configured DATABASE_URL."""
    with psycopg.connect(normalize_database_url(settings.database_url)) as conn:
        yield conn
