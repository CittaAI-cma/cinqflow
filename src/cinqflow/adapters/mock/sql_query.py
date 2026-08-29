"""canned — pre-registered results. NOT reachable by any agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.sql_query import QueryRefusedError, QueryResult

_WRITE_VERBS = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "grant")


@port("sql_query", "mock")
class CannedSqlQuery:
    """Returns registered results, and refuses writes exactly as the real one must.

    Refusing writes in the MOCK matters: otherwise the negative test passes at
    rung 0.5 and nobody discovers the real adapter never implemented it.
    """

    def __init__(self, results: dict[str, QueryResult] | None = None) -> None:
        self._results = dict(results or {})

    def query(
        self, sql: str, parameters: Sequence[Any] = (), *, max_rows: int = 10_000
    ) -> QueryResult:
        _ = (parameters, max_rows)
        normalized = " ".join(sql.lower().split())
        if normalized.startswith(_WRITE_VERBS):
            raise QueryRefusedError(
                "the sql_query pin is governed_readonly_query — it does not write"
            )
        if sql not in self._results:
            raise QueryRefusedError(f"no canned result registered for: {sql[:60]}")
        return self._results[sql]
