"""The `legacy_readonly` pin — compare against the incumbent.

    verb: parity_compare   mock: seeded_db   dev: seeded_db
    target: jdbc_readonly
    — docs/architecture/plates/04-pin-out-map.md

WAVE 5 ONLY, and REMOVED AT CUTOVER. A port with a scheduled end date.

It exists for parallel run: the comparator reads the incumbent's output
read-only and compares it against CINQFLOW's, on OUTPUT DATA. It is fitted in
Wave 0 as a named seat so the conformance kit can report it as unenergized
rather than missing — the difference between "not built yet" and "nobody
thought about it".

READ-ONLY is structural. There is no write verb, because the incumbent estate
is additive-only territory (ADR-0013) and a parity comparator with write access
is a migration tool nobody authorised.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ParityDifference:
    """One difference, itemised. Never a summary count alone — "1,240 rows
    differ" is not an actionable finding."""

    key: str
    column: str
    ours: Any
    theirs: Any


@runtime_checkable
class LegacyReadOnlyPort(Protocol):
    def fetch(self, query_name: str, **parameters: Any) -> Sequence[dict[str, Any]]:
        """Named queries only. No free-form SQL against the system being
        decommissioned."""
        ...

    def compare(
        self, query_name: str, ours: Sequence[dict[str, Any]]
    ) -> Sequence[ParityDifference]: ...
