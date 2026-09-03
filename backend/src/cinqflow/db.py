"""Thin psycopg3 access. Deliberately not an ORM: every statement is visible SQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cinqflow.settings import Settings, get_settings


class UnresolvedDatabaseUrl(RuntimeError):
    """Raised instead of letting psycopg produce its own cryptic parse error.

    Seen in production: a Railway reference variable
    (`${{Postgres.DATABASE_URL}}`) reaching the container unresolved because
    the service it names doesn't exist, or the `$` was dropped somewhere
    upstream - psycopg's own error for this ("missing '=' after ...") gives
    no hint that the actual cause is a templating problem, not a connection
    string typo."""


def _check_database_url(url: str) -> None:
    if "${{" in url or "}}" in url:
        raise UnresolvedDatabaseUrl(
            f"CINQFLOW_DATABASE_URL still contains an unresolved reference "
            f"variable: {url!r}. On Railway this means the referenced "
            f"service name doesn't match an existing service, or the `$` "
            f"was dropped when the value was set - check Variables → "
            f"CINQFLOW_DATABASE_URL against the actual Postgres service name."
        )


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    """One connection, one transaction, committed on clean exit."""
    settings = settings or get_settings()
    _check_database_url(settings.database_url)
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as conn:
        yield conn


def fetch_one(conn: psycopg.Connection, sql: str, params: Any = ()) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(conn: psycopg.Connection, sql: str, params: Any = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def execute(conn: psycopg.Connection, sql: str, params: Any = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
