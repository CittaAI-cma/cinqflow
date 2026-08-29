"""Render the portable spec as PostgreSQL DDL.

THIS FILE IS WHERE SQL DIALECT IS ALLOWED TO LIVE.

    "engine-specific SQL exists only inside compute adapters, never in the core"
    — docs/architecture/INVARIANTS.md, data plane

The core declares `decimal(18,2)`; this file decides that is `NUMERIC(18,2)`.
The Databricks renderer will decide it is `DECIMAL(18,2)`, from the same spec,
and conformance will compare each engine's introspected schema back against the
spec rather than against the other engine.

Two renderings are honest about their differences. One rendering plus a
find-and-replace is how a dialect leaks.
"""

from __future__ import annotations

from cinqflow.core.schema_spec import Column, Schema, Table, TypeName

# The canonical vocabulary, in this dialect. The whole mapping, in one place.
_TYPES: dict[TypeName, str] = {
    TypeName.STRING: "TEXT",
    TypeName.INT64: "BIGINT",
    TypeName.TIMESTAMP_UTC: "TIMESTAMPTZ",  # never a naive TIMESTAMP
    TypeName.DATE: "DATE",
    TypeName.BOOL: "BOOLEAN",
    TypeName.UUID: "UUID",
    TypeName.JSON: "JSONB",
}


class PostgresDdlRenderer:
    """Spec in, `CREATE` statements out. Deterministic and idempotent."""

    def render_schema(self, schema: Schema) -> tuple[str, ...]:
        statements: list[str] = [
            f"CREATE SCHEMA IF NOT EXISTS {schema.name};",
            f"COMMENT ON SCHEMA {schema.name} IS {_literal(schema.description)};",
        ]
        for table in schema.tables:
            statements.extend(self.render_table(schema, table))
        if schema.append_only:
            statements.extend(self._append_only(schema))
        return tuple(statements)

    def render_table(self, schema: Schema, table: Table) -> tuple[str, ...]:
        columns = [self._column(c) for c in table.columns]
        columns.append(f"PRIMARY KEY ({', '.join(table.primary_key)})")
        for unique in table.unique:
            columns.append(f"UNIQUE ({', '.join(unique)})")
        for position, check in enumerate(table.check_constraints):
            columns.append(f"CONSTRAINT {table.name}_ck{position} CHECK ({check})")

        body = ",\n    ".join(columns)
        statements = [f"CREATE TABLE IF NOT EXISTS {schema.name}.{table.name} (\n    {body}\n);"]
        if table.comment:
            statements.append(
                f"COMMENT ON TABLE {schema.name}.{table.name} IS {_literal(table.comment)};"
            )
        for column in table.columns:
            if column.comment:
                statements.append(
                    f"COMMENT ON COLUMN {schema.name}.{table.name}.{column.name} IS "
                    f"{_literal(column.comment)};"
                )
        for index_columns in table.indexes:
            name = f"ix_{table.name}_{'_'.join(index_columns)}"
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {name} ON {schema.name}.{table.name} "
                f"({', '.join(index_columns)});"
            )
        return tuple(statements)

    def _append_only(self, schema: Schema) -> tuple[str, ...]:
        """Bronze immutability: insert-only grants PLUS a reject trigger.

        BOTH, and the "plus" is load-bearing. Grants are bypassed by a
        superuser connection — which is precisely what a migration tool runs
        as, and precisely when someone would "just fix" a Bronze row. The
        trigger refuses regardless of who is asking, and RAISE carries the
        reason so the refusal explains itself in the log.
        """
        function = (
            "CREATE OR REPLACE FUNCTION cinqflow_reject_mutation() RETURNS TRIGGER AS $$\n"
            "BEGIN\n"
            "    RAISE EXCEPTION USING\n"
            "        MESSAGE = format('%s is append-only: %s refused on %I.%I',\n"
            "                         TG_TABLE_SCHEMA, TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME),\n"
            "        HINT = 'Bronze is an untouched copy of the source. "
            "Correct data downstream and reprocess; never edit the raw layer.',\n"
            "        ERRCODE = 'check_violation';\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;"
        )
        statements = [function]
        for table in schema.tables:
            qualified = f"{schema.name}.{table.name}"
            statements.extend(
                [
                    f"REVOKE UPDATE, DELETE, TRUNCATE ON {qualified} FROM PUBLIC;",
                    f"DROP TRIGGER IF EXISTS trg_{table.name}_append_only ON {qualified};",
                    f"CREATE TRIGGER trg_{table.name}_append_only\n"
                    f"    BEFORE UPDATE OR DELETE ON {qualified}\n"
                    "    FOR EACH ROW EXECUTE FUNCTION cinqflow_reject_mutation();",
                ]
            )
        return tuple(statements)

    def _column(self, column: Column) -> str:
        if column.type is TypeName.DECIMAL:
            sql_type = f"NUMERIC({column.precision},{column.scale})"
        else:
            sql_type = _TYPES[column.type]
        null = "" if column.nullable else " NOT NULL"
        return f"{column.name} {sql_type}{null}"

    def expected_signature(self, table: Table) -> str:
        """What conformance expects to introspect back.

        Rendered from the SPEC, so a renderer cannot certify itself against its
        own output — the comparison always returns to the declaration.
        """
        return table.signature


def _literal(text: str) -> str:
    """A SQL string literal. Single quotes doubled — the only escaping needed
    for the descriptions in the spec, which are ours and contain no bytes."""
    escaped = text.replace("'", "''")
    return f"'{escaped}'"
