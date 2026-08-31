"""catalog on Postgres — `information_schema`, which is the rung-0.5 target.

    catalog: schemas/lineage/grants   mock: dict   dev: information_schema
    target: unity_catalog
    — docs/architecture/plates/04-pin-out-map.md

    "`information_schema` at rung 0.5 is not a stand-in for Unity Catalog's
     ideas — it answers the same three questions the platform actually asks."
    — ports/catalog.py

WHAT THIS ADAPTER WILL NOT DO: infer PHI. `information_schema` has no opinion
about whether `last_name` is protected, and a `catalog` adapter that guessed
from column names would make masking a property of naming conventions — one
renamed column and a member's date of birth is public. The `is_phi` flag comes
from `schema_spec`, which is a CONTRACT whose change requires steward
approval, and this adapter joins the engine's answer to it rather than
substituting for it. A column the plane has and the contract does not is
reported `is_phi=True`: unclassified is masked, never public.
"""

from __future__ import annotations

from collections.abc import Sequence

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.schema_spec import all_schemas
from cinqflow.ports import port
from cinqflow.ports.catalog import ColumnInfo, TableInfo

#: Every schema the contract declares, indexed for the PHI join below. Built
#: once at import: `all_schemas()` is a pure function over frozen dataclasses,
#: so there is nothing to invalidate.
_CONTRACT: dict[tuple[str, str], dict[str, bool]] = {
    (schema.name, table.name): {c.name: c.is_phi for c in table.columns}
    for schema in all_schemas()
    for table in schema.tables
}


@port("catalog", "pg-information-schema")
class PostgresCatalog:
    """Takes a connection, like every other Postgres adapter here — so the
    contract suite builds it from the shared `plane` fixture."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def describe_table(self, schema: str, name: str) -> TableInfo:
        rows = self._db.fetch_all(
            "SELECT column_name, data_type, is_nullable, character_maximum_length, "
            "       numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (schema, name),
        )
        phi = _CONTRACT.get((schema, name), {})
        return TableInfo(
            schema=schema,
            name=name,
            columns=tuple(
                ColumnInfo(
                    name=str(column),
                    data_type=_engine_type(data_type, length, precision, scale),
                    nullable=nullable == "YES",
                    # Absent from the contract -> True. See the module note.
                    is_phi=phi.get(str(column), True) if phi else True,
                )
                for column, data_type, nullable, length, precision, scale in rows
            ),
        )

    def list_tables(self, schema: str) -> Sequence[TableInfo]:
        """Names only — no columns.

        `introspect_schema` is the one that pays for a column read per table,
        because that is what conformance needs. A screen listing thirty tables
        does not, and making `list_tables` do it would put thirty round trips
        behind a navigation click.
        """
        rows = self._db.fetch_all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            (schema,),
        )
        return tuple(TableInfo(schema=schema, name=str(name)) for (name,) in rows)

    def introspect_schema(self, schema: str) -> Sequence[TableInfo]:
        """What the engine ACTUALLY has, columns and all.

        One query, not one per table: the conformance kit calls this for every
        schema in the spec, and fifteen schemas times N tables of round trips
        turns a green gate into a slow one for no added truth.
        """
        rows = self._db.fetch_all(
            "SELECT table_name, column_name, data_type, is_nullable, "
            "       character_maximum_length, numeric_precision, numeric_scale "
            "FROM information_schema.columns c "
            "WHERE table_schema = %s "
            "  AND EXISTS (SELECT 1 FROM information_schema.tables t "
            "              WHERE t.table_schema = c.table_schema "
            "                AND t.table_name = c.table_name "
            "                AND t.table_type = 'BASE TABLE') "
            "ORDER BY table_name, ordinal_position",
            (schema,),
        )
        grouped: dict[str, list[ColumnInfo]] = {}
        for table, column, data_type, nullable, length, precision, scale in rows:
            phi = _CONTRACT.get((schema, str(table)), {})
            grouped.setdefault(str(table), []).append(
                ColumnInfo(
                    name=str(column),
                    data_type=_engine_type(data_type, length, precision, scale),
                    nullable=nullable == "YES",
                    is_phi=phi.get(str(column), True) if phi else True,
                )
            )
        return tuple(
            TableInfo(schema=schema, name=table, columns=tuple(columns))
            for table, columns in sorted(grouped.items())
        )


def _engine_type(
    data_type: str, length: int | None, precision: int | None, scale: int | None
) -> str:
    """The engine's type as an engineer would write it, not as the catalogue
    spells it.

    `information_schema` says `character varying` and `timestamp with time
    zone`; a Postgres engineer says `varchar` and `timestamptz`, and the DDL
    this platform generates says the latter. Rendering the catalogue's spelling
    would make every row of a shape comparison look like a mismatch against
    the DDL a reader is holding.
    """
    name = _SPELLING.get(data_type, data_type)
    if name == "numeric" and precision is not None:
        return f"numeric({precision},{scale or 0})"
    if name == "varchar" and length is not None:
        return f"varchar({length})"
    return name


_SPELLING: dict[str, str] = {
    "character varying": "varchar",
    "character": "char",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "time with time zone": "timetz",
    "double precision": "float8",
    "boolean": "bool",
    "integer": "int4",
    "bigint": "int8",
    "smallint": "int2",
}
