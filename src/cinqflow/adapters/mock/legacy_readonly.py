"""seeded_db — the incumbent's output, from a fixture. WAVE 5 ONLY."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.legacy_readonly import ParityDifference


@port("legacy_readonly", "mock")
class SeededLegacyDb:
    """Fitted in Wave 0 as a NAMED SEAT so conformance can report it
    unenergized — the difference between "not built yet" and "not thought
    about". Parallel run is CF-V5-E15-*, and this port is removed at cutover.

    Read-only is structural: there is no write verb to misuse against the
    system being decommissioned.
    """

    def __init__(self, rows: dict[str, Sequence[dict[str, Any]]] | None = None) -> None:
        self._rows = dict(rows or {})

    def fetch(self, query_name: str, **parameters: Any) -> Sequence[dict[str, Any]]:
        _ = parameters
        return tuple(self._rows.get(query_name, ()))

    def compare(
        self, query_name: str, ours: Sequence[dict[str, Any]]
    ) -> Sequence[ParityDifference]:
        theirs = {row.get("key"): row for row in self.fetch(query_name)}
        differences: list[ParityDifference] = []
        for row in ours:
            key = row.get("key")
            other = theirs.get(key)
            if other is None:
                differences.append(
                    ParityDifference(key=str(key), column="*", ours=row, theirs=None)
                )
                continue
            for column, value in row.items():
                if other.get(column) != value:
                    differences.append(
                        ParityDifference(
                            key=str(key), column=column, ours=value, theirs=other.get(column)
                        )
                    )
        return tuple(differences)
