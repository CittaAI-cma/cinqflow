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
    """Spec in, `CREATE` statements out. Deterministic and idempotent.

    Every identifier is double-quoted — a no-op for the platform's own
    snake_case names (Postgres already lowercases an unquoted `batch_id` to
    exactly `batch_id`), and what keeps a harvested model's own casing
    (`OurId`, `DateOfBirth`, CF-V3-E10-01's Member domain) from silently
    folding to `ourid`/`dateofbirth` — a real mismatch against the client's
    own SQL Server naming the first deploy against a live database found.
    """

    def render_schema(self, schema: Schema) -> tuple[str, ...]:
        statements: list[str] = [
            f"CREATE SCHEMA IF NOT EXISTS {_ident(schema.name)};",
            f"COMMENT ON SCHEMA {_ident(schema.name)} IS {_literal(schema.description)};",
        ]
        for table in schema.tables:
            statements.extend(self.render_table(schema, table))
        if schema.append_only:
            statements.extend(self._append_only(schema))
        return tuple(statements)

    def render_table(self, schema: Schema, table: Table) -> tuple[str, ...]:
        qualified = _qualified(schema.name, table.name)
        columns = [self._column(c) for c in table.columns]
        columns.append(f"PRIMARY KEY ({_ident_list(table.primary_key)})")
        for unique in table.unique:
            columns.append(f"UNIQUE ({_ident_list(unique)})")
        for position, check in enumerate(table.check_constraints):
            columns.append(f"CONSTRAINT {_ident(f'{table.name}_ck{position}')} CHECK ({check})")

        body = ",\n    ".join(columns)
        statements = [f"CREATE TABLE IF NOT EXISTS {qualified} (\n    {body}\n);"]
        if table.comment:
            statements.append(f"COMMENT ON TABLE {qualified} IS {_literal(table.comment)};")
        for column in table.columns:
            if column.comment:
                statements.append(
                    f"COMMENT ON COLUMN {qualified}.{_ident(column.name)} IS "
                    f"{_literal(column.comment)};"
                )
        for index_columns in table.indexes:
            name = _ident(f"ix_{table.name}_{'_'.join(index_columns)}")
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {name} ON {qualified} ({_ident_list(index_columns)});"
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
            qualified = _qualified(schema.name, table.name)
            trigger_name = _ident(f"trg_{table.name}_append_only")
            statements.extend(
                [
                    f"REVOKE UPDATE, DELETE, TRUNCATE ON {qualified} FROM PUBLIC;",
                    f"DROP TRIGGER IF EXISTS {trigger_name} ON {qualified};",
                    f"CREATE TRIGGER {trigger_name}\n"
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
        return f"{_ident(column.name)} {sql_type}{null}"

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


def _ident(name: str) -> str:
    """A double-quoted identifier. Double quotes doubled, the identifier
    equivalent of `_literal` — every name in the spec is ours, but quoting
    correctly costs nothing and a name that ever needed it would otherwise
    produce SQL nobody could read the error from."""
    return '"' + name.replace('"', '""') + '"'


def _ident_list(names: tuple[str, ...]) -> str:
    return ", ".join(_ident(name) for name in names)


def _qualified(schema_name: str, table_name: str) -> str:
    return f"{_ident(schema_name)}.{_ident(table_name)}"
