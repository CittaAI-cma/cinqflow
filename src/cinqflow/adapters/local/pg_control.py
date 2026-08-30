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


def _connect(profile: Profile, *, autocommit: bool) -> psycopg.Connection[Any]:
    """The ONE place a raw connection is opened, so the session's timezone is
    pinned exactly once rather than at three call sites that could drift.

    STORAGE IS UTC BY EXPLICIT RULE (ADR-0002) — but `timestamptz` in Postgres
    is stored as an absolute instant and RENDERED in whatever timezone the
    SESSION happens to be set to, which is an operator's `TimeZone` setting or
    the server default, never a fact this platform controls. Without pinning
    it, a value written as `06:00 UTC` reads back as `11:30` on a session
    configured for `Asia/Kolkata` — comparisons still work (Python compares
    aware datetimes as absolute instants), but every RENDERED sentence an
    operator reads — "expected 6:00 AM — not received" — silently becomes
    wrong the moment it touches a real connection whose environment happens to
    differ from the one it was tested on. `options="-c TimeZone=UTC"` sets it
    at connection startup, atomically, with no separate round trip to race.
    """
    return psycopg.connect(resolve_dsn(profile), autocommit=autocommit, options="-c TimeZone=UTC")


@contextmanager
def connect(profile: Profile, *, autocommit: bool = True) -> Iterator[Connection]:
    with _connect(profile, autocommit=autocommit) as raw:
        yield Connection(raw)


@contextmanager
def transaction(profile: Profile) -> Iterator[Connection]:
    """A connection whose work is ALWAYS rolled back.

    This is the test ergonomic that makes the Postgres plane better than a
    mock, not merely cheaper: perfect isolation, no cleanup code, and a failing
    test leaves a database an engineer can open and query.
    """
    with _connect(profile, autocommit=False) as raw:
        try:
            yield Connection(raw)
        finally:
            raw.rollback()


@contextmanager
def commit(profile: Profile) -> Iterator[Connection]:
    """A connection whose work COMMITS on success — the real counterpart to
    `transaction()`, which exists only so tests can run with no cleanup code.

    This is what a real pipeline run uses: everything inside the block is one
    transaction, committed together on a clean exit or rolled back on any
    exception — never left half-written for a crash to strand. `transaction()`
    stays untouched; it is the test fixture, and confusing the two is exactly
    how a suite ends up "passing" against a database nothing ever committed to.
    """
    with _connect(profile, autocommit=False) as raw:
        try:
            yield Connection(raw)
        except BaseException:
            raw.rollback()
            raise
        else:
            raw.commit()
