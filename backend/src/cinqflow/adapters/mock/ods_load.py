"""mem — `silver_ods` rows, in memory. CF-V3-E8-05.

Not fitted through `@port(...)` — `ods_load` is not one of the platform's
registered pins (see `ports/ods_load.py`'s own docstring for why). This is a
plain class, constructed directly by whoever wires it, the same way
`PostgresCompute` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any


class MemOdsLoad:
    """Keyed exactly the way the real Postgres adapter is: by entity name,
    never by a hard-coded table — so the same worker code runs against
    either without a single `if` naming "Members"."""

    def __init__(self) -> None:
        self._current: dict[tuple[str, object], dict[str, Any]] = {}
        self._effective_dated: dict[str, list[dict[str, Any]]] = {}
        self._next_key: dict[str, int] = {}

    def next_surrogate_key(self, entity: str) -> int:
        self._next_key[entity] = self._next_key.get(entity, 0) + 1
        return self._next_key[entity]

    def existing_current_row(
        self, entity: str, surrogate_key_column: str, surrogate_key_value: object
    ) -> Mapping[str, Any] | None:
        row = self._current.get((entity, surrogate_key_value))
        return dict(row) if row is not None else None

    def upsert_current_row(
        self, entity: str, surrogate_key_column: str, values: Mapping[str, Any]
    ) -> None:
        self._current[(entity, values[surrogate_key_column])] = dict(values)

    def current_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str
    ) -> Mapping[str, Any] | None:
        for row in self._effective_dated.get(entity, []):
            if row.get(end_date_column) is None and all(
                row.get(key) == value for key, value in match.items()
            ):
                return dict(row)
        return None

    def close_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str, end_date: date
    ) -> None:
        for row in self._effective_dated.get(entity, []):
            if row.get(end_date_column) is None and all(
                row.get(key) == value for key, value in match.items()
            ):
                row[end_date_column] = end_date
                return

    def insert_effective_dated_row(self, entity: str, values: Mapping[str, Any]) -> None:
        self._effective_dated.setdefault(entity, []).append(dict(values))

    def count_rows(self, entity: str, *, batch_id: str | None = None) -> int:
        return len(self._rows_of(entity, batch_id=batch_id))

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
        found = self._orphan_rows(
            child_entity, child_column, parent_entity, parent_column, batch_id
        )
        return tuple(dict(row) for row in found[:limit])

    def count_orphans(
        self,
        child_entity: str,
        child_column: str,
        parent_entity: str,
        parent_column: str,
        *,
        batch_id: str | None = None,
    ) -> int:
        return len(
            self._orphan_rows(child_entity, child_column, parent_entity, parent_column, batch_id)
        )

    def column_values(
        self, entity: str, column: str, *, batch_id: str | None = None
    ) -> tuple[object, ...]:
        seen: dict[object, None] = {}
        for row in self._rows_of(entity, batch_id=batch_id):
            if column in row:
                seen.setdefault(row[column], None)
        return tuple(seen)

    def _orphan_rows(
        self,
        child_entity: str,
        child_column: str,
        parent_entity: str,
        parent_column: str,
        batch_id: str | None,
    ) -> list[dict[str, Any]]:
        parent_values = {
            row.get(parent_column) for row in self._rows_of(parent_entity, batch_id=None)
        }
        return [
            row
            for row in self._rows_of(child_entity, batch_id=batch_id)
            if row.get(child_column) not in parent_values
        ]

    def _rows_of(self, entity: str, *, batch_id: str | None) -> list[dict[str, Any]]:
        rows = [row for (kind, _), row in self._current.items() if kind == entity]
        rows += self._effective_dated.get(entity, [])
        if batch_id is not None:
            rows = [row for row in rows if row.get("BatchId") == batch_id]
        return rows
