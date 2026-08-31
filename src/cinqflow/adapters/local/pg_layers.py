"""Reading the medallion layers on Postgres — masked at the source.

    "This pin serves the PLATFORM — screens, reports, the compute adapter's
     own reads. Agents reach operational truth through the certified query
     catalogue instead."
    — ports/sql_query.py

A COMPOSITION OVER TWO PINS, not a new one. `catalog` answers what the engine
has; `sql_query` answers how much and which rows. Both already exist and both
already have a Databricks target on plate 04, which is the test of whether
something deserves to be a socket of its own: a Databricks `LayerReader` fits
the same two pins and needs no plate change.

WHY THE SQL IS HERE AND NOT IN CORE. "Engine-specific SQL exists only inside
compute adapters, never in the core" — and while `SELECT count(*)` looks
portable, `quote_ident`, `information_schema` spellings and the ORDER BY
NULLS-LAST default are not. core/layers decides what a layer IS and what a
viewer may SEE; this file decides how to go and ask.

IDENTIFIERS ARE NEVER INTERPOLATED FROM A REQUEST. Every schema and table name
below comes from `core.layers.spec_of`/`table_of`, which resolve against the
frozen `schema_spec` contract and raise `KeyError` for anything else. A layer
name off the wire selects a SPEC; it never reaches a query. That is the whole
reason `rows()` takes a `Table` rather than a string — a caller cannot hand
this adapter a table name that the contract has not already vouched for, so
there is no path from a URL to an identifier.
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
from cinqflow.ports.catalog import CatalogPort
from cinqflow.ports.sql_query import QueryResult, SqlQueryPort

#: The hard ceiling on a page, regardless of what a caller asks for.
#:
#: Not a performance guard — a disclosure guard. Masking makes a row safe to
#: look at; it does not make a hundred thousand of them safe to export, and a
#: screen with no ceiling is an export tool with extra steps. Bulk extraction
#: is a delivery, which goes through `core/delivery` and leaves a row.
MAX_PAGE = 200


class PostgresLayerReader:
    """Shape, counts and masked rows for the six layers, on the rung-0.5 plane.

    Takes its two pins by constructor rather than building them, for the same
    reason `create_app` does: a reader that constructs its own adapters can
    only be tested the way production runs it.
    """

    def __init__(self, *, sql: SqlQueryPort, catalog: CatalogPort) -> None:
        self._sql = sql
        self._catalog = catalog

    # ── census ───────────────────────────────────────────────────────────────
    def census(self, spec: LayerSpec) -> LayerCensus:
        """Shape and counts for one layer. Never raises for an unbuilt layer.

        An unbuilt layer returns an EMPTY census beside a populated spec, and
        the screen renders the spec's `absence_reason`. Raising would make the
        three unbuilt layers unrenderable, which is exactly how they would end
        up quietly dropped from the spine.
        """
        if spec.schema is None:
            return LayerCensus(spec=spec, tables=())

        # What the engine actually has, once, so a table the contract declares
        # and the plane lacks is reported as absent rather than as zero rows.
        on_plane = {t.name: t for t in self._catalog.introspect_schema(spec.schema)}

        censuses: list[TableCensus] = []
        for table in tables_of(spec):
            engine = on_plane.get(table.name)
            censuses.append(
                TableCensus(
                    schema=spec.schema,
                    name=table.name,
                    comment=table.comment,
                    append_only=table.append_only,
                    row_count=self._count(spec.schema, table.name) if engine else None,
                    primary_key=table.primary_key,
                    columns=self._columns(table, engine),
                )
            )

        # A table the PLANE has and the contract does not. Reported rather than
        # skipped: on this platform that is a hand-made table in a governed
        # schema, and a screen that hides it is how it stays hidden.
        declared = {t.name for t in tables_of(spec)}
        for name, engine in sorted(on_plane.items()):
            if name in declared:
                continue
            censuses.append(
                TableCensus(
                    schema=spec.schema,
                    name=name,
                    comment=(
                        "NOT IN THE SCHEMA CONTRACT — present on the plane and declared by "
                        "nothing. Every column is masked, because unclassified is not public."
                    ),
                    append_only=False,
                    row_count=self._count(spec.schema, name),
                    columns=tuple(
                        ColumnCensus(
                            name=column.name,
                            declared_type="",
                            engine_type=column.data_type,
                            nullable=column.nullable,
                            is_phi=True,
                        )
                        for column in engine.columns
                    ),
                )
            )
        return LayerCensus(spec=spec, tables=tuple(censuses))

    # ── rows ─────────────────────────────────────────────────────────────────
    def rows(
        self,
        spec: LayerSpec,
        table: Table,
        *,
        batch_id: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> RowPage:
        """A masked page. `table` is a contract object, never a name — see the
        module note on identifiers.

        Ordering is by the primary key, ascending. Not by `ingestion_ts`, which
        would look more natural on screen and is NOT unique — a non-unique sort
        makes page 2 of a paginated read overlap page 1 silently, which is how
        a reader concludes a batch has duplicates it does not have.
        """
        if spec.schema is None:  # pragma: no cover - guarded by the API's 404
            raise KeyError(f"{spec.layer.value} has no schema on the plane")

        page = max(1, min(limit, MAX_PAGE))
        qualified = f"{_ident(spec.schema)}.{_ident(table.name)}"
        columns = tuple(c.name for c in table.columns)
        selected = ", ".join(_ident(name) for name in columns)
        order = ", ".join(_ident(name) for name in (table.primary_key or (columns[0],)))

        filtered = batch_id is not None and any(c.name == "batch_id" for c in table.columns)
        where = " WHERE batch_id = %s" if filtered else ""
        parameters: tuple[Any, ...] = (batch_id,) if filtered else ()

        total = self._count(spec.schema, table.name, batch_id=batch_id if filtered else None)
        result: QueryResult = self._sql.query(
            # S608: the only interpolated parts are `_ident`-quoted names out
            # of schema_spec; every VALUE is a parameter. See the module note.
            f"SELECT {selected} FROM {qualified}{where} "  # noqa: S608
            f"ORDER BY {order} LIMIT %s OFFSET %s",
            (*parameters, page, max(0, offset)),
            max_rows=page,
        )
        return RowPage(
            schema=spec.schema,
            table=table.name,
            columns=columns,
            rows=tuple(
                mask_row(table, dict(zip(result.columns, row, strict=True))) for row in result.rows
            ),
            total_rows=total or 0,
            truncated=(total or 0) > offset + len(result.rows),
            masked_columns=phi_columns(table),
            batch_id=batch_id if filtered else None,
        )

    # ── why rows did not cross ───────────────────────────────────────────────
    def quarantine_reasons(self, *, batch_id: str | None = None) -> tuple[QuarantineReason, ...]:
        """Grouped by rule, largest first.

        The RAW ROW is never selected here, not even masked. This query answers
        "what is wrong and how much of it" — a question that needs a rule id
        and a count, and nothing about any particular member. Selecting the row
        and then discarding it would put PHI in a result set for no reader.
        """
        where = " WHERE batch_id = %s" if batch_id else ""
        parameters: tuple[Any, ...] = (batch_id,) if batch_id else ()
        result = self._sql.query(
            "SELECT rule_id, reason, stage_name, count(*) AS rows "  # noqa: S608 — `where` is a literal, batch_id is a parameter
            f"FROM quarantine.quarantined_rows{where} "
            "GROUP BY rule_id, reason, stage_name "
            "ORDER BY count(*) DESC, rule_id",
            parameters,
        )
        return tuple(
            QuarantineReason(
                rule_id=str(rule_id),
                reason=str(reason),
                stage=str(stage),
                row_count=int(count),
            )
            for rule_id, reason, stage, count in result.rows
        )

    def reconciliation(self, *, batch_id: str | None = None) -> tuple[ReconLine, ...]:
        """The balance lines, newest first. `balanced` is the LEDGER's verdict.

        Recomputing it here would let a screen disagree with the row an auditor
        reads. `ReconLine.unattributed` is derived instead, and shown beside
        the recorded verdict — so a green tick with unexplained rows behind it
        is visible rather than trusted.
        """
        where = " WHERE batch_id = %s" if batch_id else ""
        parameters: tuple[Any, ...] = (batch_id,) if batch_id else ()
        result = self._sql.query(
            "SELECT batch_id, feed_id, stage_name, records_in, records_out, quarantined, "  # noqa: S608 — `where` is a literal, batch_id is a parameter
            "       attributed_drops, balanced, recorded_ts "
            f"FROM recon.recon_history{where} "
            "ORDER BY recorded_ts DESC, batch_id",
            parameters,
        )
        return tuple(
            ReconLine(
                batch_id=str(batch),
                feed_id=str(feed),
                stage=str(stage),
                records_in=int(rin),
                records_out=int(rout),
                quarantined=int(quarantined),
                attributed_drops=int(attributed),
                balanced=bool(balanced),
                recorded_ts=recorded.isoformat() if recorded is not None else "",
            )
            for batch, feed, stage, rin, rout, quarantined, attributed, balanced, recorded in (
                result.rows
            )
        )

    # ── internals ────────────────────────────────────────────────────────────
    def _count(self, schema: str, table: str, *, batch_id: str | None = None) -> int:
        """`count(*)`, exact.

        Not `reltuples` from `pg_class`, which is an ANALYZE-stale estimate: a
        reconciliation screen whose counts are approximate cannot be used to
        settle whether a batch balanced, and that is the one thing these counts
        are for.
        """
        where = " WHERE batch_id = %s" if batch_id else ""
        parameters: tuple[Any, ...] = (batch_id,) if batch_id else ()
        result = self._sql.query(
            f"SELECT count(*) FROM {_ident(schema)}.{_ident(table)}{where}",  # noqa: S608 — identifiers from the contract
            parameters,
        )
        return int(result.rows[0][0]) if result.rows else 0

    def _columns(self, table: Table, engine: Any) -> tuple[ColumnCensus, ...]:
        """Contract and engine, side by side — see `ColumnCensus`."""
        engine_types = {c.name: c.data_type for c in (engine.columns if engine else ())}
        return tuple(
            ColumnCensus(
                name=column.name,
                declared_type=column.type.value,
                engine_type=engine_types.get(column.name, ""),
                nullable=column.nullable,
                is_phi=column.is_phi,
                present_on_plane=column.name in engine_types,
            )
            for column in table.columns
        )


def _ident(name: str) -> str:
    """Quote an identifier that has ALREADY been vouched for by the contract.

    Belt and braces, deliberately. Every name reaching here came out of
    `schema_spec`, so there is nothing to escape — but this function is the
    only place identifiers become SQL text, and leaving it unquoted would mean
    the safety of every query above depends on a reader remembering where the
    names came from. The assertion is the point: if a name ever arrives with a
    quote or a space in it, that is a bug in the contract resolution, and it
    should fail here rather than compose.
    """
    if not name.replace("_", "").isalnum():
        raise ValueError(
            f"{name!r} is not a contract identifier. Names reaching the layer reader come "
            "from schema_spec; anything else is a resolution bug, not an escaping problem."
        )
    return f'"{name}"'
