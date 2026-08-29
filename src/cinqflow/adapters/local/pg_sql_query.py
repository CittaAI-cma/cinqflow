"""governed_readonly_query on Postgres — the platform's read seat, never an agent's.

    "sql_query: governed_readonly_query   mock: canned   dev: postgres
     target: serverless_sql_wh"
    — docs/architecture/plates/04-pin-out-map.md

    "The `sql_query` port's free-form verb is not in any Wave-0 whitelist."
    — CF-V0-E16-09 (and unchanged in Wave 1: text-to-tool, never text-to-SQL,
      until CF-V4-E14-04)

This pin serves screens and CF-V1-E7-02's rule preview: an approved rule's SQL
runs against the sandbox sample through here, read-only. Two guards, layered:
a statement allowlist (SELECT/WITH, single statement) here, and the platform's
Bronze reject-trigger underneath for anything that slips a write past a parser.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.ports import port
from cinqflow.ports.sql_query import QueryRefusedError, QueryResult


@port("sql_query", "pg-readonly")
class PostgresSqlQuery:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def query(
        self, sql: str, parameters: Sequence[Any] = (), *, max_rows: int = 10_000
    ) -> QueryResult:
        normalized = " ".join(sql.lower().split())
        if not normalized.startswith(("select", "with")):
            raise QueryRefusedError(
                "the sql_query pin is governed_readonly_query — it does not write"
            )
        if ";" in sql.rstrip().rstrip(";"):
            raise QueryRefusedError(
                "one statement per query — a second statement is how a read becomes a write"
            )
        with self._db.raw.cursor() as cursor:
            cursor.execute(sql, tuple(parameters) or None)
            columns = tuple(d.name for d in cursor.description or ())
            fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = tuple(tuple(row) for row in fetched[:max_rows])
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)
