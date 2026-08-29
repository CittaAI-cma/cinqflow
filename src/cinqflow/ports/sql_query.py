"""The `sql_query` pin — governed read-only query.

    verb: governed_readonly_query   mock: canned   dev: postgres|spark_sql
    target: serverless_sql_wh
    — docs/architecture/plates/04-pin-out-map.md

READ THIS BEFORE GIVING AN AGENT THIS PIN.

    "The `sql_query` port's free-form verb is not in any Wave-0 whitelist."
    — CF-V0-E16-09

    "Expose free-form SQL to any agent. Text-to-tool, never text-to-SQL, until
     CF-V4-E14-04." (a documented don't)

This pin serves the PLATFORM — screens, reports, the compute adapter's own
reads. Agents reach operational truth through the certified query catalogue
instead: fourteen typed, named operations whose scope filter runs inside the
query and whose results carry resolvable citations. NL->SQL over data layers
arrives in Wave 4, with full RBAC and masking underneath it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool = False


class QueryRefusedError(RuntimeError):
    """A query the governor would not run — a write, or beyond the caller's scope."""


@runtime_checkable
class SqlQueryPort(Protocol):
    def query(
        self, sql: str, parameters: Sequence[Any] = (), *, max_rows: int = 10_000
    ) -> QueryResult:
        """Run a read-only, parameterised query.

        Refuses anything that writes. `parameters` is not a convenience — it is
        the only way values enter a query, so string interpolation never
        becomes the path of least resistance.
        """
        ...
