"""Dependency wiring: settings-bound FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg

from cinqflow.db import connect
from cinqflow.settings import Settings


def make_get_conn(settings: Settings) -> Callable[[], Iterator[psycopg.Connection]]:
    """A per-request DB connection dependency bound to `settings`."""

    def get_conn() -> Iterator[psycopg.Connection]:
        with connect(settings) as conn:
            yield conn

    return get_conn
