"""The data-plane contract: engine-neutral, declared once, rendered per engine.

Compatibility with the CINQCARE plane is defined here - layers, identities, audit
columns, append-only rules - not by reproducing PostgreSQL. Postgres renders this
declaration today (`pg.py`); a Delta/Volumes renderer would read the same objects.

Grounded in docs/03_data_source_schemas/enrollment/enrollment_silver_raw_model.sql,
whose technical columns (source_system, record_hash, batch_id, created/updated) are
the convention reproduced by AUDIT_COLUMNS.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class Layer(StrEnum):
    """Medallion positions. Stage 2 builds `bronze`; the rest are declared, not built."""

    LANDING = "landing"  # file storage, not tables - see dataplane/filestore.py
    BRONZE = "bronze"
    SILVER_RAW = "silver_raw"


class TypeName(StrEnum):
    """Closed vocabulary. Engines map these; callers never write engine types."""

    STRING = "string"
    INT64 = "int64"
    DECIMAL = "decimal"
    DATE = "date"
    TIMESTAMP_UTC = "timestamp_utc"
    BOOL = "bool"
    UUID = "uuid"
    JSON = "json"


@dataclass(frozen=True)
class Column:
    name: str
    type: TypeName
    nullable: bool = True
    comment: str = ""
    is_phi: bool = False


@dataclass(frozen=True)
class Table:
    layer: Layer
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()
    append_only: bool = False
    comment: str = ""
    #: Where this table physically lives. Empty means "the layer's own name".
    #: The layer is the logical contract; the namespace is a rendering choice, and
    #: this build renders SILVER_RAW elsewhere because `silver_raw.members` in this
    #: database belongs to the previous implementation (see settings.silver_schema).
    physical_schema: str = ""

    @property
    def schema(self) -> str:
        return self.physical_schema or self.layer.value

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def phi_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.is_phi)


#: On every data-layer row. `batch_id` is the one join key threading arrival,
#: execution and reconciliation; `record_hash` is content-derived so a row
#: re-derived after reprocessing hashes identically.
AUDIT_COLUMNS: tuple[Column, ...] = (
    Column("source_system", TypeName.STRING, nullable=False, comment="Source feed name"),
    Column(
        "ingestion_ts",
        TypeName.TIMESTAMP_UTC,
        nullable=False,
        comment="When this row entered the layer",
    ),
    Column("batch_id", TypeName.STRING, nullable=False, comment="Batch lineage identifier"),
    Column("record_hash", TypeName.STRING, nullable=False, comment="Hash for change detection"),
    Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False, comment="Insert timestamp"),
    Column("updated_ts", TypeName.TIMESTAMP_UTC, comment="Update timestamp"),
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,50}$")


class UnsafeIdentifier(ValueError):
    """Feed names reach SQL as identifiers, so they are validated, never trusted."""


def table_identifier(feed: str) -> str:
    candidate = feed.strip().lower().replace("-", "_").replace(" ", "_")
    if not _IDENTIFIER.match(candidate):
        raise UnsafeIdentifier(feed)
    return candidate


def bronze_table(feed: str) -> Table:
    """One source-aligned table per feed. Values land exactly as received.

    No semantic mapping happens at Bronze: the source row is preserved whole in
    `raw_row`, and only technical metadata is added alongside it.
    """
    name = f"{table_identifier(feed)}_raw"
    return Table(
        layer=Layer.BRONZE,
        name=name,
        columns=(
            Column("bronze_id", TypeName.UUID, nullable=False, comment="Surrogate row id"),
            Column("feed_id", TypeName.STRING, nullable=False, comment="Feed this row arrived on"),
            Column(
                "row_number",
                TypeName.INT64,
                nullable=False,
                comment="1-indexed position in the source file",
            ),
            Column(
                "raw_row",
                TypeName.JSON,
                nullable=False,
                comment="The source row, unmodified",
                is_phi=True,
            ),
            *AUDIT_COLUMNS,
        ),
        primary_key=("bronze_id",),
        indexes=(("batch_id",), ("feed_id",)),
        append_only=True,
        comment=f"Bronze, source-aligned, append-only. Feed: {feed}",
    )


#: Canonical declared type -> contract type. The canonical model is governed
#: knowledge and writes its types in its own words; this is the only translation.
CANONICAL_TYPES: dict[str, TypeName] = {
    "string": TypeName.STRING,
    "int": TypeName.INT64,
    "int64": TypeName.INT64,
    "decimal": TypeName.DECIMAL,
    "date": TypeName.DATE,
    "timestamp": TypeName.TIMESTAMP_UTC,
    "bool": TypeName.BOOL,
}

#: Every child entity in the canonical enrollment model declares this column and
#: puts it in its primary key ("one row per member per address type"), so a child
#: row without it cannot be joined back to its member.
MEMBER_KEY = "source_system_id"


def silver_table(
    entity_table: str,
    fields: dict[str, str],
    *,
    schema: str,
    phi_fields: frozenset[str] = frozenset(),
) -> Table:
    """One canonical entity, rendered from the governed model.

    The table carries the whole entity, not just today's mapping, so a later
    mapping version adds rows rather than columns.

    No PRIMARY KEY constraint is declared. The canonical key of every child entity
    begins with a discriminator (`address_type`, `phone_type`, `email_type`) that a
    roster does not supply, so declaring the key would refuse rows the mapping can
    legitimately produce. Batch identity plus `record_hash` is what replay relies on.
    """
    name = table_identifier(entity_table)
    columns = tuple(
        Column(
            field_name,
            CANONICAL_TYPES.get(declared, TypeName.STRING),
            comment=f"Canonical {entity_table}.{field_name} ({declared})",
            is_phi=field_name in phi_fields,
        )
        for field_name, declared in fields.items()
    )
    indexes: list[tuple[str, ...]] = [("batch_id",)]
    if MEMBER_KEY in fields:
        indexes.append((MEMBER_KEY,))
    return Table(
        layer=Layer.SILVER_RAW,
        name=name,
        columns=(*columns, *AUDIT_COLUMNS),
        indexes=tuple(indexes),
        append_only=False,  # replay rebuilds a batch in place; Bronze is the record
        comment=f"Silver Raw, canonically mapped. Entity: {entity_table}",
        physical_schema=schema,
    )


def quarantine_table(feed: str, *, schema: str) -> Table:
    """Rows the approved mapping could not write, with the reason it could not.

    Quarantine is per feed and per source row, because what failed is a row of the
    file - not one of the five entities it would have fanned out into.
    """
    name = f"{table_identifier(feed)}_quarantine"
    return Table(
        layer=Layer.SILVER_RAW,
        name=name,
        columns=(
            Column("quarantine_id", TypeName.UUID, nullable=False, comment="Surrogate row id"),
            Column("feed_id", TypeName.STRING, nullable=False, comment="Feed this row arrived on"),
            Column(
                "row_number",
                TypeName.INT64,
                nullable=False,
                comment="1-indexed position in the batch",
            ),
            Column(
                "mapping_version",
                TypeName.INT64,
                nullable=False,
                comment="The approved version that refused this row",
            ),
            Column("outcome", TypeName.STRING, nullable=False, comment="Worst field outcome"),
            Column(
                "reasons",
                TypeName.JSON,
                nullable=False,
                comment="Per-field reasons: source, target, rule, message",
            ),
            Column(
                "raw_row", TypeName.JSON, nullable=False, comment="The source row", is_phi=True
            ),
            *AUDIT_COLUMNS,
        ),
        indexes=(("batch_id",), ("outcome",)),
        append_only=False,  # rebuilt with its batch, for the same reason as Silver
        comment=f"Silver Raw quarantine. Feed: {feed}",
        physical_schema=schema,
    )


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]


def record_hash(row: dict[str, object]) -> str:
    """Deterministic over content, independent of key order."""
    payload = json.dumps(
        {str(k): ("" if v is None else str(v)) for k, v in row.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class StageCounts:
    """The balance equation. A stage that does not balance has lost rows."""

    records_in: int
    records_out: int
    quarantined: int = 0
    attributed_drops: int = 0

    @property
    def balanced(self) -> bool:
        return self.records_in == self.records_out + self.quarantined + self.attributed_drops

    def as_dict(self) -> dict[str, int]:
        return {
            "records_in": self.records_in,
            "records_out": self.records_out,
            "quarantined": self.quarantined,
            "attributed_drops": self.attributed_drops,
        }


@dataclass
class BronzeRow:
    """What the engine hands the adapter. The adapter adds nothing of its own."""

    bronze_id: str
    feed_id: str
    row_number: int
    raw_row: dict[str, str]
    source_system: str
    batch_id: str
    record_hash: str
    extras: dict[str, object] = field(default_factory=dict)
