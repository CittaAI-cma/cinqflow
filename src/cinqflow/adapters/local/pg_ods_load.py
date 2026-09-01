"""Postgres — `silver_ods` rows, for real. CF-V3-E8-05.

Generic over any `OdsEntity`: every statement is built from the entity name
and column names the caller supplies, never a hard-coded table. A second
harvested entity needs a new call from `workers.ods_load`, not a new method
here.

Identifiers are quoted throughout — `pg_ddl.py`'s own fix, CF-V3-E10-01: an
unquoted `OurId` is what Postgres folds to `ourid`, and every table this
adapter touches carries the client's own PascalCase naming.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from cinqflow.adapters.local.pg_control import Connection

_SCHEMA = "silver_ods"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qualified(table: str) -> str:
    return f"{_ident(_SCHEMA)}.{_ident(table)}"


class PostgresOdsLoad:
    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def next_surrogate_key(self, entity: str) -> int:
        # One sequence per entity, provisioned by `installer.ods_model`
        # alongside the entity's own table — see that module.
        statement = f"SELECT nextval('{_qualified(sequence_name(entity))}')"
        row = self._db.fetch_one(statement)
        assert row is not None
        return int(row[0])

    def existing_current_row(
        self, entity: str, surrogate_key_column: str, surrogate_key_value: object
    ) -> Mapping[str, Any] | None:
        columns = self._columns(entity)
        selected = ", ".join(_ident(c) for c in columns)
        column = _ident(surrogate_key_column)
        statement = f"SELECT {selected} FROM {_qualified(entity)} WHERE {column} = %s"  # noqa: S608
        row = self._db.fetch_one(statement, (surrogate_key_value,))
        return dict(zip(columns, row, strict=True)) if row is not None else None

    def upsert_current_row(
        self, entity: str, surrogate_key_column: str, values: Mapping[str, Any]
    ) -> None:
        columns = tuple(values.keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_list = ", ".join(_ident(c) for c in columns)
        updates = ", ".join(
            f"{_ident(c)} = EXCLUDED.{_ident(c)}" for c in columns if c != surrogate_key_column
        )
        conflict_column = _ident(surrogate_key_column)
        # Every interpolated part below is `_ident`-quoted; the values are parameters.
        statement = (
            f"INSERT INTO {_qualified(entity)} ({column_list}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT ({conflict_column}) DO UPDATE SET {updates}"
        )
        self._db.execute(statement, tuple(values[c] for c in columns))

    def current_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str
    ) -> Mapping[str, Any] | None:
        columns = self._columns(entity)
        selected = ", ".join(_ident(c) for c in columns)
        where = " AND ".join(f"{_ident(c)} = %s" for c in match)
        # Every interpolated part below is `_ident`-quoted; the values are parameters.
        statement = (
            f"SELECT {selected} FROM {_qualified(entity)} "  # noqa: S608
            f"WHERE {where} AND {_ident(end_date_column)} IS NULL"
        )
        row = self._db.fetch_one(statement, tuple(match.values()))
        return dict(zip(columns, row, strict=True)) if row is not None else None

    def close_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str, end_date: date
    ) -> None:
        where = " AND ".join(f"{_ident(c)} = %s" for c in match)
        # Every interpolated part below is `_ident`-quoted; the values are parameters.
        statement = (
            f"UPDATE {_qualified(entity)} SET {_ident(end_date_column)} = %s "  # noqa: S608
            f"WHERE {where} AND {_ident(end_date_column)} IS NULL"
        )
        self._db.execute(statement, (end_date, *match.values()))

    def insert_effective_dated_row(self, entity: str, values: Mapping[str, Any]) -> None:
        columns = tuple(values.keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_list = ", ".join(_ident(c) for c in columns)
        statement = f"INSERT INTO {_qualified(entity)} ({column_list}) VALUES ({placeholders})"  # noqa: S608
        self._db.execute(statement, tuple(values[c] for c in columns))

    def count_rows(self, entity: str, *, batch_id: str | None = None) -> int:
        where, parameters = _batch_filter(batch_id)
        statement = f"SELECT count(*) FROM {_qualified(entity)}{where}"  # noqa: S608
        row = self._db.fetch_one(statement, parameters)
        assert row is not None
        return int(row[0])

    def orphans(
        self,
        child_entity: str,
        child_column: str,
        parent_entity: str,
        parent_column: str,
        *,
        batch_id: str | None = None,
        limit: int = 50,
    ) -> tuple[Mapping[str, Any], ...]:
        columns = self._columns(child_entity)
        statement, parameters = _orphan_query(
            "SELECT " + ", ".join(f"c.{_ident(c)}" for c in columns),
            child_entity,
            child_column,
            parent_entity,
            parent_column,
            batch_id,
        )
        rows = self._db.fetch_all(f"{statement} LIMIT %s", (*parameters, limit))
        return tuple(dict(zip(columns, row, strict=True)) for row in rows)

    def count_orphans(
        self,
        child_entity: str,
        child_column: str,
        parent_entity: str,
        parent_column: str,
        *,
        batch_id: str | None = None,
    ) -> int:
        statement, parameters = _orphan_query(
            "SELECT count(*)", child_entity, child_column, parent_entity, parent_column, batch_id
        )
        row = self._db.fetch_one(statement, parameters)
        assert row is not None
        return int(row[0])

    def column_values(
        self, entity: str, column: str, *, batch_id: str | None = None
    ) -> tuple[object, ...]:
        where, parameters = _batch_filter(batch_id)
        statement = f"SELECT DISTINCT {_ident(column)} FROM {_qualified(entity)}{where}"  # noqa: S608
        rows = self._db.fetch_all(statement, parameters)
        return tuple(row[0] for row in rows)

    def _columns(self, entity: str) -> tuple[str, ...]:
        rows = self._db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (_SCHEMA, entity),
        )
        return tuple(row[0] for row in rows)


def _batch_filter(batch_id: str | None) -> tuple[str, tuple[Any, ...]]:
    if batch_id is None:
        return "", ()
    return f" WHERE {_ident('BatchId')} = %s", (batch_id,)


def _orphan_query(
    select: str,
    child_entity: str,
    child_column: str,
    parent_entity: str,
    parent_column: str,
    batch_id: str | None,
) -> tuple[str, tuple[Any, ...]]:
    """A LEFT JOIN ... WHERE NULL — the one shape that finds a child row
    with no matching parent, generic over any two entities and columns.
    Every interpolated part is `_ident`-quoted; the only value is the
    optional `batch_id` parameter."""
    join_condition = f"c.{_ident(child_column)} = p.{_ident(parent_column)}"
    null_check = f"p.{_ident(parent_column)} IS NULL"
    statement = (
        f"{select} FROM {_qualified(child_entity)} c "
        f"LEFT JOIN {_qualified(parent_entity)} p ON {join_condition} "
        f"WHERE {null_check}"
    )
    if batch_id is None:
        return statement, ()
    return statement + f" AND c.{_ident('BatchId')} = %s", (batch_id,)


def sequence_name(entity: str) -> str:
    """One name, shared with `installer.ods_model.provision_ods_model`
    (which creates it, importing this) — so a rename of either breaks a
    test instead of silently reading from a sequence that was never
    created."""
    return f"{entity.lower()}_surrogate_key_seq"
