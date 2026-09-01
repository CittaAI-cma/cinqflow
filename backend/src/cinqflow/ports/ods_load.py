"""Reading and writing `silver_ods` rows, generically over any `OdsEntity`.

    "Generate surrogate keys while always retaining source identifiers
     alongside. ... Apply history rules exactly as configured per entity."
    — CF-V3-E8-05

NOT ONE OF THE 19 NAMED PINS (`memory/07-runbooks/RB-02...`, plate 04). The
closest existing precedent — `PostgresCompute`'s `land_bronze`/
`load_silver_raw` — is itself not behind a formal pin either: `PipelineRunner`
depends on it concretely (`workers/pipeline.py:107`), because Bronze/Silver-Raw
writes are the data plane's own internal shape, not a swappable external
system the way Verato or a queue broker are. `silver_ods` writes are the same
kind of internal shape, one layer further down the spine — so this follows
that precedent rather than opening RB-02's full new-pin ceremony (atlas.html,
the conformance kit, the connection profile schema, a fresh installer
section) for a table this platform's own Postgres already owns. What IS kept
from Law 2 is the part that costs little and buys real safety: one interface,
a mock and a real implementation, one shared contract suite
(`tests/contract/test_ods_load_contract.py`) — so a future Databricks
rendering has a known shape to implement against, without registering a
twentieth chip pin for it today.

EVERY METHOD IS KEYED BY NAME, NEVER BY A HARD-CODED TABLE. An entity's
surrogate key column, its match columns for an effective-dated row, and its
end-date column are all named by the CALLER (`workers.ods_load`, which reads
them off `OdsEntity`) — so a second entity (a future harvest) needs no new
adapter method, only a new call.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OdsLoadPort(Protocol):
    def next_surrogate_key(self, entity: str) -> int:
        """A fresh surrogate key value for this entity — for a genuinely new
        record, never for one that already carries a legacy id."""
        ...

    def existing_current_row(
        self, entity: str, surrogate_key_column: str, surrogate_key_value: object
    ) -> Mapping[str, Any] | None:
        """The current-only (SCD-1) row at this key, or `None` if it has
        never been loaded."""
        ...

    def upsert_current_row(
        self, entity: str, surrogate_key_column: str, values: Mapping[str, Any]
    ) -> None:
        """Insert, or update in place — SCD-1. `values` must include
        `surrogate_key_column`."""
        ...

    def current_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str
    ) -> Mapping[str, Any] | None:
        """The one row matching every column in `match` whose
        `end_date_column` is still open (NULL) — SCD-2's "what is current
        for this key today". `match` names whatever columns make a key
        unique for this entity (its surrogate key's FK plus its declared
        `source_key_columns`), not assumed by this port."""
        ...

    def close_open_row(
        self, entity: str, match: Mapping[str, Any], end_date_column: str, end_date: date
    ) -> None:
        """Close the currently open row at `match` — "an address change
        closes the old row" — never deletes it."""
        ...

    def insert_effective_dated_row(self, entity: str, values: Mapping[str, Any]) -> None:
        """Open a new effective-dated row — "and opens a new one.\""""
        ...

    def count_rows(self, entity: str, *, batch_id: str | None = None) -> int:
        """How many rows this entity carries — the "checked" count a
        relationship check reports beside its orphans (CF-V3-E10-03).
        Scoped to one batch when given, otherwise the whole entity."""
        ...

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
        """Rows in `child_entity` whose `child_column` value matches no
        real `parent_column` value in `parent_entity` — "orphaned claims,
        dangling provider references", generic over any two entities. A
        LEFT JOIN ... WHERE NULL, never assumed structure beyond the two
        column names the caller supplies. Capped at `limit` examples; the
        TRUE total is `count_orphans`, not `len(this result)`."""
        ...

    def count_orphans(
        self,
        child_entity: str,
        child_column: str,
        parent_entity: str,
        parent_column: str,
        *,
        batch_id: str | None = None,
    ) -> int:
        """The TRUE orphan count, uncapped — what `core.relationship_
        integrity.check_relationship`'s `orphan_count` reports even when
        `orphans()` only fetched a sample."""
        ...

    def column_values(
        self, entity: str, column: str, *, batch_id: str | None = None
    ) -> tuple[object, ...]:
        """Every distinct value `column` carries for this entity — the
        member-id universe `core.member_universe.compare_member_universe`
        (CF-V3-E13-02) compares set-wise. Scoped to one batch when given,
        exactly like `count_rows`; the caller decides what two calls to
        compare (a batch's own contribution against the whole current
        table, two batches against each other, or the whole table twice
        across a coexistence window) rather than this port assuming a
        topology.
        """
        ...
