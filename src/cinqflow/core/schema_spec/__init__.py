"""The portable DDL spec — every schema declared ONCE, rendered per engine.

    "Keep the control-table DDLs logically identical to the pinned target
     versions — the conformance kit asserts schema equality on both engines."
    — CF-V0-E8-07

    postgres_schemas: [landing_ctl, control, bronze, silver_raw, silver_ods,
                       quarantine, recon]
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

THE DESIGN DECISION, and why it is worth the indirection:

Declare the tables once, in a small canonical type vocabulary. Each compute
adapter renders its OWN DDL from the spec. Conformance then compares each
engine's INTROSPECTED schema against the SPEC — never one engine against the
other.

That asymmetry is the whole value. "Engine X disagrees with the spec" is an
attributable defect someone can fix. "Postgres and Databricks disagree" is a
diff nobody can adjudicate, and it is the shape of argument that stalls a
migration for a week.

The type vocabulary is deliberately tiny. Every extra type is another chance
for two engines to disagree about nulls, precision or collation — and FIG 08's
pinned divergences say exactly where that bites: declare precision and scale
for numerics, store UTC and derive business_date by an explicit rule.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class TypeName(StrEnum):
    """The canonical type vocabulary. Closed on purpose.

    Note what is absent: a naive timestamp. There is only TIMESTAMP_UTC, so a
    naive timestamp cannot be specified — which is how "store UTC" becomes
    structural instead of a review comment.
    """

    STRING = "string"
    INT64 = "int64"
    DECIMAL = "decimal"  # precision and scale are REQUIRED
    TIMESTAMP_UTC = "timestamp_utc"
    DATE = "date"
    BOOL = "bool"
    UUID = "uuid"
    JSON = "json"


@dataclass(frozen=True)
class Column:
    name: str
    type: TypeName
    nullable: bool = True
    precision: int | None = None
    scale: int | None = None
    # From the schema contract. Masking is driven by this flag, and changing it
    # requires steward approval.
    is_phi: bool = False
    comment: str = ""

    def __post_init__(self) -> None:
        if self.type is TypeName.DECIMAL and (self.precision is None or self.scale is None):
            raise ValueError(
                f"{self.name}: a decimal must declare precision and scale. An undeclared "
                "decimal is where money quietly changes between engines."
            )
        if self.type is not TypeName.DECIMAL and (self.precision or self.scale):
            raise ValueError(f"{self.name}: only a decimal carries precision and scale")

    @property
    def signature(self) -> str:
        """What conformance compares. Logical, never dialect."""
        suffix = f"({self.precision},{self.scale})" if self.type is TypeName.DECIMAL else ""
        null = "NULL" if self.nullable else "NOT NULL"
        return f"{self.name}:{self.type.value}{suffix}:{null}"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()
    unique: tuple[tuple[str, ...], ...] = ()
    check_constraints: tuple[str, ...] = ()
    append_only: bool = False
    comment: str = ""

    def __post_init__(self) -> None:
        if not self.primary_key:
            raise ValueError(
                f"{self.name}: a table with no primary key cannot be reconciled, "
                "deduplicated or cited"
            )
        names = {c.name for c in self.columns}
        for key in (*self.primary_key, *(c for idx in self.indexes for c in idx)):
            if key not in names:
                raise ValueError(f"{self.name}: key column {key!r} is not in the table")

    def column(self, name: str) -> Column:
        for candidate in self.columns:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no column {name!r}")

    @property
    def signature(self) -> str:
        columns = ",".join(c.signature for c in self.columns)
        return f"{self.name}[{columns}]pk({','.join(self.primary_key)})ao={self.append_only}"


@dataclass(frozen=True)
class Schema:
    name: str
    description: str
    tables: tuple[Table, ...] = ()
    append_only: bool = False

    def table(self, name: str) -> Table:
        for candidate in self.tables:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no table {name!r}")

    @property
    def fingerprint(self) -> str:
        """Stable across processes, so a conformance report can pin WHICH spec
        an engine was checked against. A green result against an unnamed spec
        is unfalsifiable."""
        material = self.name + "|" + "|".join(t.signature for t in self.tables)
        return hashlib.sha256(material.encode()).hexdigest()[:32]


# ── shared column groups ─────────────────────────────────────────────────────
def _audit_columns() -> tuple[Column, ...]:
    """ "Audit columns appear everywhere." Lineage has to survive every hop, so
    a data-layer row can always be traced back to the file it came from."""
    return (
        Column("source_system", TypeName.STRING, nullable=False),
        Column("ingestion_ts", TypeName.TIMESTAMP_UTC, nullable=False),
        Column("batch_id", TypeName.STRING, nullable=False),
        Column("record_hash", TypeName.STRING, nullable=False),
        Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False),
        Column("updated_ts", TypeName.TIMESTAMP_UTC),
    )


_STATE = Column("state", TypeName.STRING, nullable=False)
_BATCH = Column("batch_id", TypeName.STRING, nullable=False)
_FEED = Column("feed_id", TypeName.STRING, nullable=False)


# ── the eleven control tables ────────────────────────────────────────────────
CONTROL_SCHEMA = Schema(
    name="control",
    description=(
        "The client's existing framework, which is genuinely good. CINQFLOW builds ON it "
        "and adds beside it, never inside it (ADR-0013). All eleven join on batch_id."
    ),
    tables=(
        Table(
            name="feed_sla_config",
            comment="Feed definitions, expected counts, arrival windows, SLA deadlines.",
            columns=(
                _FEED,
                Column("feed_version", TypeName.INT64, nullable=False),
                Column("domain", TypeName.STRING, nullable=False),
                Column("source_system", TypeName.STRING, nullable=False),
                Column("file_format", TypeName.STRING, nullable=False),
                Column("landing_path", TypeName.STRING, nullable=False),
                Column("file_pattern", TypeName.STRING, nullable=False),
                Column("schedule_cron", TypeName.STRING, nullable=False),
                Column("expected_file_count", TypeName.INT64),
                Column("min_size_bytes", TypeName.INT64),
                Column("max_size_bytes", TypeName.INT64),
                Column("grace_period_minutes", TypeName.INT64),
                Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("feed_id", "feed_version"),
            # "Allow two active feeds to claim the same landing path and pattern"
            # is a documented don't for CF-V0-E3-01. Enforced here, not in a
            # validator that a second write path could bypass.
            unique=(("landing_path", "file_pattern"),),
        ),
        Table(
            name="input_registry",
            comment=(
                "File-level tracking. EVERY arriving file gets a row, including "
                "unexpected ones — parked and surfaced, never ignored."
            ),
            columns=(
                Column("input_id", TypeName.UUID, nullable=False),
                Column("batch_id", TypeName.STRING),  # null until a batch opens
                Column("feed_id", TypeName.STRING),  # null when UNEXPECTED
                Column("file_key", TypeName.STRING, nullable=False),
                Column("filename", TypeName.STRING, nullable=False),
                Column("size_bytes", TypeName.INT64, nullable=False),
                Column("fingerprint", TypeName.STRING, nullable=False),
                _STATE,
                Column("arrived_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("rejection_reason", TypeName.STRING),
                Column("record_count", TypeName.INT64),
            ),
            primary_key=("input_id",),
            # THE exactly-once mechanism. A unique fingerprint is what makes
            # "the same file presented twice is skipped" a database guarantee
            # rather than a check somebody remembered to write.
            unique=(("fingerprint",),),
            indexes=(("feed_id", "arrived_ts"),),
        ),
        Table(
            name="schema_registry",
            comment="Expected schema per feed and version — the baseline for drift.",
            columns=(
                _FEED,
                Column("contract_version", TypeName.INT64, nullable=False),
                Column("columns", TypeName.JSON, nullable=False),
                Column("approved_by", TypeName.STRING),
                Column("approved_ts", TypeName.TIMESTAMP_UTC),
                Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("feed_id", "contract_version"),
        ),
        Table(
            name="schema_drift_log",
            comment="Drift events, classified BY MEANING rather than by structure.",
            columns=(
                Column("drift_id", TypeName.UUID, nullable=False),
                _BATCH,
                _FEED,
                Column("classification", TypeName.STRING, nullable=False),
                Column("field_details", TypeName.JSON, nullable=False),
                Column("detected_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("drift_id",),
            indexes=(("batch_id",),),
        ),
        Table(
            name="batch_control",
            comment="Batch lifecycle, metrics and completion.",
            columns=(
                _BATCH,
                _FEED,
                Column("feed_version", TypeName.INT64, nullable=False),
                Column("business_date", TypeName.DATE, nullable=False),
                _STATE,
                Column("started_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("completed_ts", TypeName.TIMESTAMP_UTC),
                Column("restart_count", TypeName.INT64, nullable=False),
                # "every batch records the model version it loaded into" — FIG 10
                Column("model_version", TypeName.STRING),
            ),
            primary_key=("batch_id",),
            indexes=(("feed_id", "started_ts"),),
        ),
        Table(
            name="batch_stage_status",
            comment=(
                "Per-stage execution. The counts here are not statistics — they are "
                "the terms of the balance equation."
            ),
            columns=(
                _BATCH,
                Column("stage_name", TypeName.STRING, nullable=False),
                _STATE,
                Column("started_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("completed_ts", TypeName.TIMESTAMP_UTC),
                Column("records_in", TypeName.INT64, nullable=False),
                Column("records_out", TypeName.INT64, nullable=False),
                Column("quarantined", TypeName.INT64, nullable=False),
                Column("attributed_drops", TypeName.INT64, nullable=False),
            ),
            # One row per stage per batch. Two rows would double every count a
            # screen or a recon query reads, which is why it is the key.
            primary_key=("batch_id", "stage_name"),
            check_constraints=(
                "records_in >= 0 AND records_out >= 0 AND quarantined >= 0 "
                "AND attributed_drops >= 0",
            ),
        ),
        Table(
            name="error_log",
            comment=(
                "The deterministic error hash is the quiet hero: "
                "hash(batch_id, stage_name, record_key, error_type, rule_id) makes replay "
                "idempotent AT THE ERROR LEVEL, so reprocessing a corrected batch cannot "
                "manufacture duplicate incidents."
            ),
            columns=(
                Column("error_id_hash", TypeName.STRING, nullable=False),
                _BATCH,
                Column("stage_name", TypeName.STRING, nullable=False),
                Column("error_category", TypeName.STRING, nullable=False),
                Column("message", TypeName.STRING, nullable=False),
                Column("record_key", TypeName.STRING),
                Column("rule_id", TypeName.STRING),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("error_id_hash",),
            indexes=(("batch_id", "error_category"),),
            check_constraints=(
                "error_category IN ('FILE_ERROR','SCHEMA_ERROR','VALIDATION_ERROR',"
                "'TRANSFORMATION_ERROR','INTEGRATION_ERROR','SYSTEM_ERROR')",
            ),
        ),
        Table(
            name="quarantine_records",
            comment=(
                "Invalid records isolated during transformation, with reason and "
                "reprocessing status. Reported as counts and reasons — never contents."
            ),
            columns=(
                Column("quarantine_id", TypeName.UUID, nullable=False),
                _BATCH,
                Column("stage_name", TypeName.STRING, nullable=False),
                Column("rule_id", TypeName.STRING, nullable=False),
                Column("reason", TypeName.STRING, nullable=False),
                Column("column_names", TypeName.JSON, nullable=False),
                Column("record_key", TypeName.STRING),
                Column("payload", TypeName.JSON, is_phi=True),
                Column("reprocessed_ts", TypeName.TIMESTAMP_UTC),
                Column("quarantined_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("quarantine_id",),
            indexes=(("batch_id", "rule_id"),),
        ),
        Table(
            name="batch_reconciliation",
            comment=(
                "Expected vs actual per stage. The drop ledger's category vocabulary is "
                "constrained HERE, at the schema level."
            ),
            columns=(
                Column("recon_id", TypeName.UUID, nullable=False),
                _BATCH,
                Column("stage_name", TypeName.STRING, nullable=False),
                Column("records_in", TypeName.INT64, nullable=False),
                Column("records_out", TypeName.INT64, nullable=False),
                Column("quarantined", TypeName.INT64, nullable=False),
                Column("attributed_drops", TypeName.INT64, nullable=False),
                Column("drop_rule_id", TypeName.STRING),
                Column("drop_reason", TypeName.STRING),
                Column("drop_count", TypeName.INT64),
                Column("financial_impact", TypeName.DECIMAL, precision=18, scale=2),
                Column("balanced", TypeName.BOOL, nullable=False),
                Column("reconciled_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("recon_id",),
            indexes=(("batch_id", "stage_name"),),
            check_constraints=(
                # The balance equation, as a database guarantee.
                "records_in = records_out + quarantined + attributed_drops",
                # "Allow any category called 'other' or 'unknown' in the drop ledger"
                # is a documented don't. A row can only leave the pipeline with a
                # REASON attached, and 'other' is not a reason.
                "drop_rule_id IS NULL OR lower(drop_rule_id) NOT IN ('other','unknown','n/a')",
                "drop_reason IS NULL OR lower(drop_reason) NOT IN ('other','unknown','n/a')",
            ),
        ),
        Table(
            name="sla_instance",
            comment="Per-cycle SLA tracking: expected vs actual, with computed status.",
            columns=(
                Column("sla_instance_id", TypeName.UUID, nullable=False),
                _FEED,
                Column("batch_id", TypeName.STRING),
                Column("cycle_date", TypeName.DATE, nullable=False),
                Column("expected_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("actual_ts", TypeName.TIMESTAMP_UTC),
                Column("sla_status", TypeName.STRING, nullable=False),
            ),
            primary_key=("sla_instance_id",),
            unique=(("feed_id", "cycle_date"),),
            check_constraints=("sla_status IN ('On-Time','Delayed','Breached')",),
        ),
        Table(
            name="sla_alerts",
            comment="Alerts with severity, dispatch status and acknowledgment tracking.",
            columns=(
                Column("alert_id", TypeName.UUID, nullable=False),
                Column("batch_id", TypeName.STRING),
                Column("sla_instance_id", TypeName.UUID),
                Column("severity", TypeName.STRING, nullable=False),
                Column("summary", TypeName.STRING, nullable=False),
                Column("citations", TypeName.JSON),
                Column("dispatched_ts", TypeName.TIMESTAMP_UTC),
                Column("acknowledged_by", TypeName.STRING),
                Column("acknowledged_ts", TypeName.TIMESTAMP_UTC),
                Column("raised_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("alert_id",),
            check_constraints=("severity IN ('info','warning','critical')",),
        ),
    ),
)


# ── the data-plane schemas ───────────────────────────────────────────────────
LANDING_CTL_SCHEMA = Schema(
    name="landing_ctl",
    description=(
        "Landing is the Control Entry Point: structural validation only, no semantic "
        "validation. Files are immutable; only ACCEPTED files proceed to Bronze."
    ),
    tables=(
        Table(
            name="landing_event",
            comment="Every arrival, acceptance, rejection and park — the trust boundary's log.",
            columns=(
                Column("event_id", TypeName.UUID, nullable=False),
                Column("file_key", TypeName.STRING, nullable=False),
                Column("fingerprint", TypeName.STRING, nullable=False),
                Column("feed_id", TypeName.STRING),
                Column("outcome", TypeName.STRING, nullable=False),
                Column("reason", TypeName.STRING),
                Column("moved_to", TypeName.STRING),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("event_id",),
            indexes=(("fingerprint",),),
            check_constraints=("outcome IN ('ACCEPTED','REJECTED','UNEXPECTED','SKIPPED')",),
        ),
    ),
)

BRONZE_SCHEMA = Schema(
    name="bronze",
    description=(
        "An untouched copy of the source. Append-only, enforced at the DATABASE layer: "
        "insert-only grants PLUS a reject-update/delete trigger. An UPDATE or DELETE is "
        "refused and the attempt is logged."
    ),
    append_only=True,
    tables=(
        Table(
            name="members_raw",
            comment="Raw fidelity. Every column arrives as a string; casting happens later.",
            append_only=True,
            columns=(
                Column("bronze_id", TypeName.UUID, nullable=False),
                Column("feed_id", TypeName.STRING, nullable=False),
                Column("row_number", TypeName.INT64, nullable=False),
                Column("raw_row", TypeName.JSON, nullable=False, is_phi=True),
                *_audit_columns(),
            ),
            primary_key=("bronze_id",),
            indexes=(("batch_id",),),
        ),
    ),
)

SILVER_RAW_SCHEMA = Schema(
    name="silver_raw",
    description="Typed, mapped, rule-evaluated. The Wave-0 terminus.",
    tables=(
        Table(
            name="members",
            comment="Canonical column names, contracted types, source identifiers retained.",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("feed_id", TypeName.STRING, nullable=False),
                Column("source_member_id", TypeName.STRING, nullable=False, is_phi=True),
                Column("first_name", TypeName.STRING, is_phi=True),
                Column("last_name", TypeName.STRING, is_phi=True),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
                Column("gender", TypeName.STRING),
                Column("line_of_business", TypeName.STRING),
                Column("effective_date", TypeName.DATE),
                Column("end_date", TypeName.DATE),
                Column("is_active", TypeName.BOOL, nullable=False),
                *_audit_columns(),
            ),
            primary_key=("member_row_id",),
            # A source member appears once per batch. Re-running a batch must
            # not silently double a roster — incident #11's lesson, as a key.
            unique=(("batch_id", "feed_id", "source_member_id"),),
            indexes=(("batch_id",), ("source_member_id",)),
        ),
    ),
)

SILVER_ODS_SCHEMA = Schema(
    name="silver_ods",
    description=(
        "The canonical, member-centric model. Provisioned EMPTY in Wave 0: it sits "
        "behind G4 identity resolution, which is Wave 3 (CF-V3-E9-*, CF-V3-E10-*). "
        "A record whose identity is unresolved never loads — it waits, visibly."
    ),
)

QUARANTINE_SCHEMA = Schema(
    name="quarantine",
    description="Isolated records, each attributed to the named rule that excluded it.",
    tables=(
        Table(
            name="quarantined_rows",
            comment="The row, its reason, and the rule. PHI-flagged and masked in every view.",
            columns=(
                Column("quarantine_id", TypeName.UUID, nullable=False),
                Column("batch_id", TypeName.STRING, nullable=False),
                Column("stage_name", TypeName.STRING, nullable=False),
                Column("rule_id", TypeName.STRING, nullable=False),
                Column("reason", TypeName.STRING, nullable=False),
                Column("record_key", TypeName.STRING),
                Column("raw_row", TypeName.JSON, nullable=False, is_phi=True),
                Column("quarantined_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("quarantine_id",),
            indexes=(("batch_id", "rule_id"),),
        ),
    ),
)

RECON_SCHEMA = Schema(
    name="recon",
    description="Reconciliation history, so trends are visible per feed over time.",
    tables=(
        Table(
            name="recon_history",
            comment="One row per batch per stage, kept so drop-rate trends are visible.",
            columns=(
                Column("history_id", TypeName.UUID, nullable=False),
                Column("batch_id", TypeName.STRING, nullable=False),
                Column("feed_id", TypeName.STRING, nullable=False),
                Column("stage_name", TypeName.STRING, nullable=False),
                Column("records_in", TypeName.INT64, nullable=False),
                Column("records_out", TypeName.INT64, nullable=False),
                Column("quarantined", TypeName.INT64, nullable=False),
                Column("attributed_drops", TypeName.INT64, nullable=False),
                Column("balanced", TypeName.BOOL, nullable=False),
                Column("recorded_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("history_id",),
            indexes=(("feed_id", "recorded_ts"),),
        ),
    ),
)

DATA_SCHEMAS: tuple[Schema, ...] = (
    LANDING_CTL_SCHEMA,
    BRONZE_SCHEMA,
    SILVER_RAW_SCHEMA,
    SILVER_ODS_SCHEMA,
    QUARANTINE_SCHEMA,
    RECON_SCHEMA,
)


def all_schemas() -> tuple[Schema, ...]:
    """Every schema the installer provisions, in dependency order."""
    return (
        LANDING_CTL_SCHEMA,
        CONTROL_SCHEMA,
        BRONZE_SCHEMA,
        SILVER_RAW_SCHEMA,
        SILVER_ODS_SCHEMA,
        QUARANTINE_SCHEMA,
        RECON_SCHEMA,
    )
