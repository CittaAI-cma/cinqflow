"""Thin psycopg3 access. Deliberately not an ORM: every statement is visible SQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cinqflow.settings import Settings, get_settings


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    """One connection, one transaction, committed on clean exit."""
    settings = settings or get_settings()
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
