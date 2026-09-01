"""A seeded medallion plane in memory. Rung 0 — nothing runs but Python.

    0: {socket: mock, cost: 0, proves: core_logic_in_ci_seconds}
    — docs/architecture/plates/05-socket-ladder.md

This exists so the six-layer screen works on the dev socket, and the reason
that matters is not convenience. The screen's whole argument is that the
platform's structure is legible — six positions, three of them not built, with
counts beside the three that are. A screen that only renders against Postgres
would make that argument unverifiable in CI, and the Playwright suite would be
asserting a fixture in a browser instead of the contract.

IT MASKS. Every value here is invented, so there is no PHI in this file and
nothing would be disclosed by skipping the masking. It masks anyway, through
the same `core.layers.mask_row` the Postgres reader uses, because the thing
under test on the rung-0 socket is that the RENDERING hides what it should —
and a stand-in that returned clear values would let a UI regression that
un-masks a column pass every fast test and fail only against a real plane.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.layers import (
    ColumnCensus,
    LayerCensus,
    LayerSpec,
    QuarantineReason,
    ReconLine,
    RowPage,
    TableCensus,
    mask_row,
    phi_columns,
    tables_of,
)
from cinqflow.core.schema_spec import Table


class MemLayerReader:
    """Seeded rows, keyed by `schema.table`. Absent means "on the plane, empty".

    Distinct from a table the contract declares and the plane LACKS, which is
    what `absent` is for. Both render differently on the screen and collapsing
    them would make the stand-in unable to exercise the case the real plane
    has — a declared table with no migration behind it.
    """

    def __init__(
        self,
        *,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        quarantine: tuple[QuarantineReason, ...] = (),
        recon: tuple[ReconLine, ...] = (),
        absent: frozenset[str] = frozenset(),
    ) -> None:
        self._rows = rows or {}
        self._quarantine = quarantine
        self._recon = recon
        self._absent = absent

    def census(self, spec: LayerSpec) -> LayerCensus:
        if spec.schema is None:
            return LayerCensus(spec=spec, tables=())
        return LayerCensus(
            spec=spec,
            tables=tuple(
                TableCensus(
                    schema=spec.schema,
                    name=table.name,
                    comment=table.comment,
                    append_only=table.append_only,
                    row_count=(
                        None
                        if f"{spec.schema}.{table.name}" in self._absent
                        else len(self._rows.get(f"{spec.schema}.{table.name}", []))
                    ),
                    primary_key=table.primary_key,
                    columns=tuple(
                        ColumnCensus(
                            name=column.name,
                            declared_type=column.type.value,
                            # The stand-in reports the PORTABLE type as the
                            # engine's, because in memory they are the same
                            # thing. Inventing a Postgres spelling here would
                            # be a second, unchecked type map — and the whole
                            # reason `declared_type` and `engine_type` are two
                            # fields is that nobody should be maintaining a
                            # mapping the conformance kit does not verify.
                            engine_type=column.type.value,
                            nullable=column.nullable,
                            is_phi=column.is_phi,
                            present_on_plane=f"{spec.schema}.{table.name}" not in self._absent,
                        )
                        for column in table.columns
                    ),
                )
                for table in tables_of(spec)
            ),
        )

    def rows(
        self,
        spec: LayerSpec,
        table: Table,
        *,
        batch_id: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> RowPage:
        seeded = list(self._rows.get(f"{spec.schema}.{table.name}", []))
        filtered = batch_id is not None and any(c.name == "batch_id" for c in table.columns)
        if filtered:
            seeded = [row for row in seeded if str(row.get("batch_id")) == batch_id]
        window = seeded[max(0, offset) : max(0, offset) + max(1, limit)]
        return RowPage(
            schema=spec.schema or "",
            table=table.name,
            columns=tuple(c.name for c in table.columns),
            rows=tuple(
                # Every declared column, in contract order, whether the seed
                # supplied it or not — a stand-in whose rows had different keys
                # from the real plane's would let a UI depend on a column the
                # contract does not guarantee.
                mask_row(table, {c.name: row.get(c.name) for c in table.columns})
                for row in window
            ),
            total_rows=len(seeded),
            truncated=len(seeded) > offset + len(window),
            masked_columns=phi_columns(table),
            batch_id=batch_id if filtered else None,
        )

    def quarantine_reasons(self, *, batch_id: str | None = None) -> tuple[QuarantineReason, ...]:
        _ = batch_id  # the seed is one batch; filtering it would always be a no-op
        return self._quarantine

    def reconciliation(self, *, batch_id: str | None = None) -> tuple[ReconLine, ...]:
        if batch_id is None:
            return self._recon
        return tuple(line for line in self._recon if line.batch_id == batch_id)
