"""The Postgres connection behind `metadata_db` and `control_tables`.

Rung 0.5's entire infrastructure requirement: PostgreSQL 16 + pgvector.

    "Every pipeline test runs inside a rolled-back transaction, so thousands of
     tests finish in minutes with no cleanup code."
    — CF-V0-E8-07

That is why `transaction()` is here and why the test fixture uses it rather
than truncating tables between tests. Truncation is slow, order-dependent, and
leaves a database an engineer cannot inspect after a failure. A rolled-back
transaction is fast, perfectly isolated, and a failing test can be paused with
its data still in front of you.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from cinqflow.core.model.profile import Profile
from cinqflow.ports.secrets import is_reference, reference_name


class Connection:
    """A thin wrapper. Deliberately not an ORM.

    "An ORM in core/ would invite engine-specific SQL into the one place it is
    forbidden" — and row-at-a-time ORM semantics are the wrong shape for the
    set-based loads this platform runs.
    """

    def __init__(self, raw: psycopg.Connection[Any]) -> None:
        self._raw = raw

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        self._raw.execute(statement, parameters or None)

    def fetch_all(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._raw.cursor() as cursor:
            cursor.execute(statement, parameters or None)
            return cursor.fetchall()

    def fetch_one(self, statement: str, parameters: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        with self._raw.cursor() as cursor:
            cursor.execute(statement, parameters or None)
            return cursor.fetchone()

    @property
    def raw(self) -> psycopg.Connection[Any]:
        return self._raw


def resolve_dsn(profile: Profile) -> str:
    """Resolve the DSN, which is a `secret://name` reference in every profile.

    The env var name is derived mechanically from the reference, so adding a
    secret is a profile line plus an env line — never a code change.
    """
    dsn = profile.dsn
    if not dsn:
        raise ValueError(f"{profile.source}: metadata_db has no dsn")
    if not is_reference(dsn):
        return dsn
    name = reference_name(dsn)
    env_name = "CINQFLOW_SECRET_" + name.replace("-", "_").replace(".", "_").upper()
    resolved = os.environ.get(env_name)
    if not resolved:
        raise KeyError(
            f"{dsn} is unresolved: set {env_name}. Profiles carry references; the secrets "
            "adapter resolves them — dotenv at rungs 0.5-1, Key Vault at rung 3."
        )
    return resolved


@contextmanager
def connect(profile: Profile, *, autocommit: bool = True) -> Iterator[Connection]:
    with psycopg.connect(resolve_dsn(profile), autocommit=autocommit) as raw:
        yield Connection(raw)


@contextmanager
def transaction(profile: Profile) -> Iterator[Connection]:
    """A connection whose work is ALWAYS rolled back.

    This is the test ergonomic that makes the Postgres plane better than a
    mock, not merely cheaper: perfect isolation, no cleanup code, and a failing
    test leaves a database an engineer can open and query.
    """
    with psycopg.connect(resolve_dsn(profile), autocommit=False) as raw:
        try:
            yield Connection(raw)
        finally:
            raw.rollback()
