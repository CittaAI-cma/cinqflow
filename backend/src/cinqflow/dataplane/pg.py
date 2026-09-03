"""The Postgres rendering of the data-plane contract.

The only module in the codebase containing data-plane SQL. It renders DDL from
`contract.py` and writes rows; it makes no decisions about what to write.

Two notes on this database specifically:

* The previous implementation's objects live here too (`bronze.members_raw`,
  `public.cinqflow_reject_mutation`). Nothing here touches them: tables are created
  per feed under new names, and the append-only guard is created inside the layer's
  own schema under its own name.
* The developer role is superuser, and superusers bypass `REVOKE`. So the trigger -
  not the revoke - is what actually enforces append-only. The revoke is kept as
  defence for non-superuser deployments.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import psycopg

from cinqflow.dataplane.contract import AUDIT_COLUMNS, BronzeRow, Layer, Table, TypeName

_TYPES: dict[TypeName, str] = {
    TypeName.STRING: "TEXT",
    TypeName.INT64: "BIGINT",
    TypeName.DECIMAL: "NUMERIC",
    TypeName.DATE: "DATE",
    TypeName.TIMESTAMP_UTC: "TIMESTAMPTZ",
    TypeName.BOOL: "BOOLEAN",
    TypeName.UUID: "UUID",
    TypeName.JSON: "JSONB",
}

GUARD_FUNCTION = "cinqflow_append_only_guard"


def render_guard(layer: str) -> str:
    """An append-only guard scoped to this build, inside the layer's own schema."""
    return f"""
        CREATE OR REPLACE FUNCTION {layer}.{GUARD_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $guard$
        BEGIN
            RAISE EXCEPTION
                'cinqflow: %.% is append-only; % refused',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
        END
        $guard$
    """


def render_table(table: Table) -> list[str]:
    """Declaration -> statements. Every statement is idempotent."""
    columns = []
    for column in table.columns:
        sql_type = _TYPES[column.type]
        null = "" if column.nullable else " NOT NULL"
        columns.append(f'    "{column.name}" {sql_type}{null}')
    if table.primary_key:
        keys = ", ".join(f'"{k}"' for k in table.primary_key)
        columns.append(f"    PRIMARY KEY ({keys})")

    statements = [
        f'CREATE TABLE IF NOT EXISTS {table.schema}."{table.name}" (\n'
        + ",\n".join(columns)
        + "\n)"
    ]

    if table.comment:
        escaped = table.comment.replace("'", "''")
        statements.append(f"COMMENT ON TABLE {table.schema}.\"{table.name}\" IS '{escaped}'")
    for column in table.columns:
        if column.comment:
            escaped = column.comment.replace("'", "''")
            statements.append(
                f'COMMENT ON COLUMN {table.schema}."{table.name}"."{column.name}" '
                f"IS '{escaped}'"
            )

    for index in table.indexes:
        index_name = f"{table.name}_{'_'.join(index)}_idx"
        cols = ", ".join(f'"{c}"' for c in index)
        statements.append(
            f'CREATE INDEX IF NOT EXISTS "{index_name}" ON {table.schema}."{table.name}" ({cols})'
        )

    if table.append_only:
        trigger = f"{table.name}_append_only"
        statements += [
            f'DROP TRIGGER IF EXISTS "{trigger}" ON {table.schema}."{table.name}"',
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE OR TRUNCATE '
            f'ON {table.schema}."{table.name}" '
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table.schema}.{GUARD_FUNCTION}()",
            f'REVOKE UPDATE, DELETE, TRUNCATE ON {table.schema}."{table.name}" FROM PUBLIC',
        ]
    return statements


def _bind(value: object, type_name: TypeName) -> object:
    """JSON columns are dumped; everything else is passed through as given."""
    if value is None:
        return None
    if type_name is TypeName.JSON:
        return json.dumps(value, sort_keys=True)
    return value


class PostgresDataPlane:
    """Implements DataPlanePort against PostgreSQL."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def install_layer(self, layer: str, *, physical: str | None = None) -> None:
        """`layer` is the logical position and is validated against the contract;
        `physical` is the namespace it renders into, which may differ."""
        Layer(layer)  # refuse anything not declared in the contract
        namespace = physical or layer
        with self.conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
            cur.execute(render_guard(namespace))

    def ensure_table(self, table: Table) -> None:
        self.install_layer(table.layer.value, physical=table.schema)
        with self.conn.cursor() as cur:
            for sql in render_table(table):
                cur.execute(sql)

    def append_bronze(self, table: Table, rows: list[BronzeRow]) -> int:
        if not rows:
            return 0
        now = datetime.now(UTC)
        sql = f"""
            INSERT INTO {table.schema}."{table.name}"
                (bronze_id, feed_id, row_number, raw_row,
                 source_system, ingestion_ts, batch_id, record_hash, created_ts)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        params = [
            (
                row.bronze_id,
                row.feed_id,
                row.row_number,
                json.dumps(row.raw_row, sort_keys=True),
                row.source_system,
                now,
                row.batch_id,
                row.record_hash,
                now,
            )
            for row in rows
        ]
        with self.conn.cursor() as cur:
            cur.executemany(sql, params)
            return len(params)

    def write_rows(
        self,
        table: Table,
        rows: list[dict[str, object]],
        *,
        source_system: str,
        batch_id: str,
    ) -> int:
        """Insert mapped rows, adding only the audit columns.

        Values arrive as strings (Bronze preserved them verbatim and the executor
        cast them to canonical string forms), so each placeholder carries the
        column's declared cast. A value that cannot satisfy its column fails here
        rather than landing wrong - and `validate_spec` refused that combination at
        save time, so it should never reach this point.
        """
        if not rows:
            return 0
        if table.append_only:  # pragma: no cover - guarded by the contract
            raise ValueError(f"{table.qualified} is append-only; use append_bronze")

        audit = {c.name for c in AUDIT_COLUMNS}
        mapped = [c for c in table.columns if c.name not in audit]
        now = datetime.now(UTC)

        placeholders = [f"%s::{_TYPES[c.type]}" for c in mapped]
        columns = [f'"{c.name}"' for c in mapped]
        sql = f"""
            INSERT INTO {table.schema}."{table.name}"
                ({", ".join(columns)},
                 source_system, ingestion_ts, batch_id, record_hash, created_ts)
            VALUES ({", ".join(placeholders)}, %s, %s, %s, %s, %s)
        """
        params = [
            (
                *[_bind(row.get(c.name), c.type) for c in mapped],
                source_system,
                now,
                batch_id,
                row["record_hash"],
                now,
            )
            for row in rows
        ]
        with self.conn.cursor() as cur:
            cur.executemany(sql, params)
            return len(params)

    def delete_batch(self, table: Table, batch_id: str) -> int:
        """Remove one batch's rows so a replay can rebuild them.

        Only ever called on tables the contract marks rebuildable; Bronze refuses
        this at the database level, which is the point of the trigger.
        """
        if table.append_only:
            raise ValueError(f"{table.qualified} is append-only; it cannot be rebuilt")
        with self.conn.cursor() as cur:
            cur.execute(
                f'DELETE FROM {table.schema}."{table.name}" WHERE batch_id = %s', (batch_id,)
            )
            return cur.rowcount

    def count_rows(self, table: Table, batch_id: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*) AS n FROM {table.schema}."{table.name}" WHERE batch_id = %s',
                (batch_id,),
            )
            return cur.fetchone()["n"]

    def read_rows(
        self, table: Table, batch_id: str, *, limit: int, offset: int = 0, stride: int = 1
    ) -> list[dict]:
        """Rows of one batch in file order.

        `stride` > 1 takes every k-th row instead of consecutive ones, which is how
        a preview samples across a whole batch rather than off the top of it. The
        arithmetic is on `row_number`, so the same stride always returns the same
        rows.
        """
        # `mod(...)` rather than the `%` operator: psycopg reads `%` as the start of
        # a placeholder.
        stride_clause = "" if stride <= 1 else "AND mod(row_number - 1, %s) = 0"
        params: tuple = (batch_id,) if stride <= 1 else (batch_id, stride)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""SELECT row_number, raw_row, record_hash, batch_id, source_system, ingestion_ts
                    FROM {table.schema}."{table.name}"
                    WHERE batch_id = %s {stride_clause}
                    ORDER BY row_number LIMIT %s OFFSET %s""",
                (*params, limit, offset),
            )
            return list(cur.fetchall())

    def read_quarantine(
        self, table: Table, batch_id: str, *, limit: int, offset: int = 0
    ) -> list[dict]:
        """Refused rows of one batch, worst first, then in file order."""
        with self.conn.cursor() as cur:
            cur.execute(
                f"""SELECT row_number, mapping_version, outcome, reasons, raw_row, record_hash
                    FROM {table.schema}."{table.name}"
                    WHERE batch_id = %s ORDER BY row_number LIMIT %s OFFSET %s""",
                (batch_id, limit, offset),
            )
            return list(cur.fetchall())

    def count_by(self, table: Table, batch_id: str, column: str) -> dict[str, int]:
        """Tally one column of a batch. `column` is checked against the declaration."""
        if column not in table.column_names:
            raise ValueError(f"{table.qualified} has no column {column}")
        with self.conn.cursor() as cur:
            cur.execute(
                f'SELECT "{column}" AS key, count(*) AS n '
                f'FROM {table.schema}."{table.name}" WHERE batch_id = %s GROUP BY 1 ORDER BY 1',
                (batch_id,),
            )
            return {str(row["key"]): row["n"] for row in cur.fetchall()}

    def table_exists(self, table: Table) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT to_regclass(%s) IS NOT NULL AS present""",
                (f'{table.schema}."{table.name}"',),
            )
            return cur.fetchone()["present"]
