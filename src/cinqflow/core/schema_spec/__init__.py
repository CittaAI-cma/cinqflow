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
                Column("column_name", TypeName.STRING, nullable=False),
                Column("detail", TypeName.STRING, nullable=False),
                Column("blocked_batch", TypeName.BOOL, nullable=False),
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
                # The count the caller already knows at write time. Deriving it
                # instead from a join to batch_reconciliation.drop_count made
                # get_quarantine_summary return 0 whenever record_quarantine was
                # called without a matching record_reconciliation call — a real
                # pipeline run always does both, but the PORT's contract does
                # not require that coupling, so the contract suite (which
                # rightly calls them independently) caught the drift.
                Column("record_count", TypeName.INT64, nullable=False),
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

# ── the three plane objects `registry/wave0.py` already declares by name ─────
#
# `registry.governed_object`, `governance.audit_ledger` and `audit.agent_action`
# were execution-plane objects from the start (core/registry/wave0.py) — every
# story contract reads or writes them. What was missing was the DDL and the
# adapter; this is that DDL, matching the object IDs already in the register
# exactly, so provisioning them is a certification of an existing contract,
# not a new one.
REGISTRY_SCHEMA = Schema(
    name="registry",
    description=(
        "Every governed object — feed, contract, mapping, dq_rule, glossary_term, runbook, "
        "release, prompt, execution_plane_contract, source — one lifecycle, one table. The "
        "body is type-specific; everything the lifecycle needs is a first-class column, "
        "because governance that depends on reading inside an opaque blob eventually gets "
        "skipped (core/model/governed.py)."
    ),
    tables=(
        Table(
            name="governed_object",
            comment="ADR-0006: one state machine, reused by every object type. Never edited "
            "in place — a new version is a new row.",
            columns=(
                Column("object_type", TypeName.STRING, nullable=False),
                Column("object_id", TypeName.STRING, nullable=False),
                Column("version", TypeName.INT64, nullable=False),
                Column("lifecycle_state", TypeName.STRING, nullable=False),
                Column("body", TypeName.JSON, nullable=False),
                Column("created_by_subject", TypeName.STRING, nullable=False),
                Column("created_by_type", TypeName.STRING, nullable=False),
                Column("created_by_name", TypeName.STRING),
                Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("approved_by_subject", TypeName.STRING),
                Column("approved_by_type", TypeName.STRING),
                Column("approved_by_name", TypeName.STRING),
                Column("approved_ts", TypeName.TIMESTAMP_UTC),
            ),
            primary_key=("object_type", "object_id", "version"),
            indexes=(("object_type",), ("object_type", "object_id")),
        ),
    ),
)

GOVERNANCE_SCHEMA = Schema(
    name="governance",
    description="The lifecycle audit trail. Append-only; no deletion path exists for anyone.",
    tables=(
        Table(
            name="audit_ledger",
            comment="One row per lifecycle transition or governance event.",
            columns=(
                Column("entry_id", TypeName.UUID, nullable=False),
                Column("object_type", TypeName.STRING, nullable=False),
                Column("object_id", TypeName.STRING, nullable=False),
                Column("version", TypeName.INT64, nullable=False),
                Column("action", TypeName.STRING, nullable=False),
                Column("actor_subject", TypeName.STRING, nullable=False),
                Column("actor_type", TypeName.STRING, nullable=False),
                Column("actor_name", TypeName.STRING),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("from_state", TypeName.STRING),
                Column("to_state", TypeName.STRING),
                Column("detail", TypeName.STRING),
            ),
            primary_key=("entry_id",),
            indexes=(("object_id", "occurred_ts"),),
        ),
    ),
)

AUDIT_SCHEMA = Schema(
    name="audit",
    description="What an agent did, and every time it was refused. Append-only.",
    tables=(
        Table(
            name="agent_action",
            comment="100% of model calls carry prompt hash, model version, cost and caller "
            "identity here — including the refusals.",
            columns=(
                Column("action_id", TypeName.UUID, nullable=False),
                Column("run_id", TypeName.STRING, nullable=False),
                Column("agent", TypeName.STRING, nullable=False),
                Column("action", TypeName.STRING, nullable=False),
                Column("outcome", TypeName.STRING, nullable=False),
                Column("actor_subject", TypeName.STRING, nullable=False),
                Column("actor_type", TypeName.STRING, nullable=False),
                Column("actor_name", TypeName.STRING),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("risk_class", TypeName.STRING, nullable=False),
                Column("prompt_ref", TypeName.STRING),
                Column("prompt_hash", TypeName.STRING),
                Column("model", TypeName.STRING),
                Column("model_version", TypeName.STRING),
                Column("prompt_tokens", TypeName.INT64),
                Column("completion_tokens", TypeName.INT64),
                Column("cost_usd", TypeName.DECIMAL, precision=18, scale=6),
                Column("latency_ms", TypeName.INT64),
                Column("detail", TypeName.STRING),
            ),
            primary_key=("action_id",),
            indexes=(("run_id",), ("agent", "occurred_ts")),
        ),
    ),
)


# ── Wave 1 · work coordination, proposals and the knowledge plane ────────────
#
# Three additive schemas (ADR-0013 holds: beside the estate, never inside it).
# `queue` is ADR-0014's Postgres queue plus the scheduler's runtime state;
# `proposals` is the universal HITL object every R2 agent writes and only a
# human moves; `knowledge` is the K2 store — the ENGINE-SPECIFIC halves
# (pgvector column, tsvector column, their indexes) live in the Postgres
# rendering only, never here (see installer/cli.py:_KNOWLEDGE_PLANE_PG).
QUEUE_SCHEMA = Schema(
    name="queue",
    description=(
        "SELECT ... FOR UPDATE SKIP LOCKED on the Postgres already running (ADR-0014). "
        "Identical at rung 4 — no broker, no cache, no cloud weld. Also holds the "
        "scheduler's runtime state: schedules are REGISTERED here from published feed "
        "metadata; the governed truth stays in registry.*."
    ),
    tables=(
        Table(
            name="message",
            comment=(
                "One unit of work. A repeated dedupe_key returns the existing message — "
                "replay safety starts at the producer, not the consumer."
            ),
            columns=(
                Column("message_id", TypeName.UUID, nullable=False),
                Column("topic", TypeName.STRING, nullable=False),
                Column("payload", TypeName.JSON, nullable=False),
                _STATE,
                Column("dedupe_key", TypeName.STRING),
                Column("attempts", TypeName.INT64, nullable=False),
                Column("enqueued_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("claimed_ts", TypeName.TIMESTAMP_UTC),
                Column("acked_ts", TypeName.TIMESTAMP_UTC),
            ),
            primary_key=("message_id",),
            unique=(("dedupe_key",),),
            indexes=(("topic", "state"),),
            check_constraints=("state IN ('pending','in_flight','done','failed')",),
        ),
        Table(
            name="schedule",
            comment=(
                "One row per feed the orchestrator runs — never a DAG per feed. A paused "
                "feed carries its reason: a pause with no stated reason becomes a mystery "
                "nobody dares unpause."
            ),
            columns=(
                _FEED,
                Column("cron", TypeName.STRING, nullable=False),
                Column("timezone", TypeName.STRING, nullable=False),
                Column("grace_period_minutes", TypeName.INT64, nullable=False),
                Column("paused_reason", TypeName.STRING),
                Column("registered_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("feed_id",),
        ),
        Table(
            name="scheduled_run",
            comment="Every trigger, so 'why did this run start' always has an answer.",
            columns=(
                Column("run_id", TypeName.UUID, nullable=False),
                _FEED,
                Column("scheduled_for", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("triggered_ts", TypeName.TIMESTAMP_UTC),
                Column("batch_id", TypeName.STRING),
            ),
            primary_key=("run_id",),
            unique=(("feed_id", "scheduled_for"),),
            indexes=(("feed_id",),),
        ),
    ),
)

PROPOSALS_SCHEMA = Schema(
    name="proposals",
    description=(
        "The universal HITL object. Agents write ONLY here (plus knowledge.*, ops.*, "
        "forecasts.*, audit.agent_action) — never to control.* or any data layer. A "
        "proposal becomes a governed object only through a human's act."
    ),
    tables=(
        Table(
            name="proposal",
            comment=(
                "DRAFT -> PENDING_REVIEW -> APPROVED|REJECTED -> APPLIED|FAILED. The AI "
                "version and the human's correction both persist: corrections are fuel — "
                "every one appends to the evaluation set."
            ),
            columns=(
                Column("proposal_id", TypeName.UUID, nullable=False),
                Column("agent", TypeName.STRING, nullable=False),
                Column("capability", TypeName.STRING, nullable=False),
                Column("risk_class", TypeName.STRING, nullable=False),
                Column("feed_id", TypeName.STRING),
                Column("run_id", TypeName.STRING, nullable=False),
                _STATE,
                Column("payload", TypeName.JSON, nullable=False),
                Column("confidence", TypeName.DECIMAL, precision=5, scale=4),
                Column("grounding_citations", TypeName.JSON),
                Column("prompt_hash", TypeName.STRING),
                Column("created_by_subject", TypeName.STRING, nullable=False),
                Column("created_by_type", TypeName.STRING, nullable=False),
                Column("created_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("decided_by_subject", TypeName.STRING),
                Column("decision_comment", TypeName.STRING),
                Column("decided_ts", TypeName.TIMESTAMP_UTC),
                Column("applied_object_type", TypeName.STRING),
                Column("applied_object_id", TypeName.STRING),
                Column("applied_version", TypeName.INT64),
            ),
            primary_key=("proposal_id",),
            indexes=(("state",), ("feed_id",), ("agent", "created_ts")),
            check_constraints=(
                "state IN ('draft','pending_review','approved','rejected','applied','failed')",
                # R4 is human-always and never automatable; an R4 proposal row is
                # a category error, refused at the schema so no code path can
                # create one.
                "risk_class IN ('R0','R1','R2','R3')",
            ),
        ),
    ),
)

KNOWLEDGE_SCHEMA = Schema(
    name="knowledge",
    description=(
        "K2 — semantic knowledge. Only Published governed objects embed; Retired deletes "
        "its chunks; the store is a REBUILDABLE PROJECTION of approved knowledge. PHI, "
        "member rows, raw feed contents, drafts and secrets never enter it."
    ),
    tables=(
        Table(
            name="chunk",
            comment=(
                "citation_id is the load-bearing column: it turns 'the model said so' "
                "into 'the approved glossary term BG-004 says so, click to open'. The "
                "embedding vector and tsvector columns are Postgres-rendering extras "
                "added by the installer — engine-specific, so not declared here."
            ),
            columns=(
                Column("chunk_id", TypeName.STRING, nullable=False),
                Column("kind", TypeName.STRING, nullable=False),
                Column("citation_id", TypeName.STRING, nullable=False),
                Column("text", TypeName.STRING, nullable=False),
                Column("domain", TypeName.STRING),
                Column("source_org", TypeName.STRING),
                Column("feed_id", TypeName.STRING),
                Column("lifecycle_state", TypeName.STRING, nullable=False),
                Column("object_version", TypeName.INT64),
                Column("scope_tags", TypeName.JSON),
                Column("metadata", TypeName.JSON),
                Column("phi_verified_at", TypeName.TIMESTAMP_UTC),
                Column("embedding_model_version", TypeName.STRING),
                Column("embedded_at", TypeName.TIMESTAMP_UTC),
            ),
            primary_key=("chunk_id",),
            indexes=(("kind",), ("domain",), ("feed_id",), ("citation_id",)),
        ),
    ),
)


PROFILING_SCHEMA = Schema(
    name="profiling",
    description=(
        "CF-V1-E5-01 — computed facts about sample files. BESIDE the client's control "
        "framework, never inside it (ADR-0013). A profile is an OBSERVATION, not a governed "
        "object: it is never approved, only attached as evidence to the things that are."
    ),
    tables=(
        Table(
            name="file_profile",
            comment=(
                "One profiling run's facts. `profile_id` IS the fingerprint of those facts, "
                "so re-profiling an unchanged file writes the same row rather than a second "
                "one — which is what makes the replay proof a database property. "
                "`profiled_ts` is deliberately OUTSIDE the fingerprint: evidence that has "
                "not changed must not look newer for having been recomputed."
            ),
            columns=(
                Column("profile_id", TypeName.STRING, nullable=False),
                _FEED,
                Column("source_key", TypeName.STRING, nullable=False),
                Column("source_fingerprint", TypeName.STRING, nullable=False),
                Column("profiler_version", TypeName.STRING, nullable=False),
                Column("readable", TypeName.BOOL, nullable=False),
                Column("would_load", TypeName.BOOL, nullable=False),
                Column("row_count", TypeName.INT64, nullable=False),
                Column("column_count", TypeName.INT64, nullable=False),
                Column("sampled", TypeName.BOOL, nullable=False),
                # The whole profile, so a stored profile round-trips exactly.
                # A stale-evidence gate comparing a full profile against a
                # partial one would call the difference a change.
                Column("facts", TypeName.JSON, nullable=False),
                Column("profiled_by", TypeName.STRING, nullable=False),
                Column("profiled_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("profile_id", "feed_id"),
            indexes=(("feed_id", "profiled_ts"), ("source_fingerprint",)),
            # A profile is a computed fact. Correcting one means profiling
            # again, which produces a new id — so there is no edit path, for
            # the same reason the audit log has no delete path.
            append_only=True,
        ),
    ),
)


OPS_SCHEMA = Schema(
    name="ops",
    description=(
        "CF-V1-E3-04 — operational state that is NOT governance. A pause stops new work "
        "and needs no approver to lift; a lifecycle state is approved configuration and "
        "does. Keeping them in different schemas is what stops the two being confused."
    ),
    tables=(
        Table(
            name="feed_suspension",
            comment=(
                "One pause or resume. APPEND-ONLY: resuming writes a row rather than "
                "deleting one, because a feed that was paused for six days and a feed "
                "that was never paused must not look identical afterwards. The current "
                "state is the newest row, so 'was this paused on the 3rd?' is answerable "
                "from what is stored."
            ),
            columns=(
                Column("suspension_id", TypeName.UUID, nullable=False),
                _FEED,
                Column("action", TypeName.STRING, nullable=False),
                Column("reason", TypeName.STRING, nullable=False),
                Column("actor_subject", TypeName.STRING, nullable=False),
                Column("actor_name", TypeName.STRING),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                # A pause with an end lifts itself. The longest outage the
                # incumbent platform had was a feed paused "for an hour" and
                # unpaused eleven days later by somebody looking for something
                # else.
                Column("resumes_after", TypeName.TIMESTAMP_UTC),
            ),
            primary_key=("suspension_id",),
            indexes=(("feed_id", "occurred_ts"),),
            check_constraints=("action IN ('paused', 'resumed')",),
            append_only=True,
        ),
        Table(
            name="action_record",
            comment=(
                "CF-V2-E12-03/E8-04 — one operations action, one row PER PHASE. The record "
                "that came back from the POST used to vanish with the response, so 'I "
                "clicked retry and nothing happened' was unanswerable and verify() had "
                "nothing to re-read. APPEND-ONLY like the suspension ledger: a REQUESTED "
                "row is never updated into a VERIFIED one — the second phase is a second "
                "row, so 'what did this look like before somebody checked' is a fact the "
                "ledger holds. The current phase is the newest row per record_id. REFUSED "
                "actions are rows too — the refusals are exactly what a reviewer needs six "
                "weeks later."
            ),
            columns=(
                Column("event_id", TypeName.UUID, nullable=False),
                Column("record_id", TypeName.STRING, nullable=False),
                Column("batch_id", TypeName.STRING, nullable=False),
                _FEED,
                Column("action", TypeName.STRING, nullable=False),
                Column("phase", TypeName.STRING, nullable=False),
                Column("actor_subject", TypeName.STRING, nullable=False),
                Column("actor_name", TypeName.STRING),
                Column("reason", TypeName.STRING),
                Column("approval_identifier", TypeName.STRING),
                Column("outcome", TypeName.STRING),
                # What the control tables said when somebody looked. NULL until
                # then — which is a different fact from "the batch has no
                # state", and the screen renders them differently.
                Column("observed_state", TypeName.STRING),
                Column("requested_ts", TypeName.TIMESTAMP_UTC, nullable=False),
                Column("verified_ts", TypeName.TIMESTAMP_UTC),
                Column("occurred_ts", TypeName.TIMESTAMP_UTC, nullable=False),
            ),
            primary_key=("event_id",),
            indexes=(("record_id", "occurred_ts"), ("batch_id",), ("feed_id", "occurred_ts")),
            check_constraints=(
                "action IN ('acknowledge', 'assign', 'note', 'pause', 'resume', 'retry', "
                "'restart_from_stage', 'reprocess_batch', 'reprocess_failed_only', 'backdate')",
                "phase IN ('requested', 'verified', 'failed', 'refused')",
            ),
            append_only=True,
        ),
    ),
    append_only=True,
)


def all_schemas() -> tuple[Schema, ...]:
    """Every schema the installer provisions, in dependency order.

    `cinqflow install` renders CREATE ... IF NOT EXISTS for everything here, so
    re-running it against a live plane is the additive upgrade path: a new
    schema or table appears; nothing existing is touched.
    """
    return (
        LANDING_CTL_SCHEMA,
        CONTROL_SCHEMA,
        BRONZE_SCHEMA,
        SILVER_RAW_SCHEMA,
        SILVER_ODS_SCHEMA,
        QUARANTINE_SCHEMA,
        RECON_SCHEMA,
        REGISTRY_SCHEMA,
        GOVERNANCE_SCHEMA,
        AUDIT_SCHEMA,
        QUEUE_SCHEMA,
        PROPOSALS_SCHEMA,
        KNOWLEDGE_SCHEMA,
        PROFILING_SCHEMA,
        OPS_SCHEMA,
    )
