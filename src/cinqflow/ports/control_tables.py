"""The `control_tables` pin — read and write the eleven control tables.

    verb: read_write_11_tables   mock: memstore   dev: postgres_schemas
    target: delta_sql_warehouse
    — docs/architecture/plates/04-pin-out-map.md

    "One join key — batch_id — threads arrival, execution, failure and
     reconciliation."
    — docs/architecture/plates/07-control-table-and-governed-object-model.md

This is the client's existing framework, and it is genuinely good. CINQFLOW
builds ON it and adds beside it, never inside it (ADR-0013).

Why the writes matter more than the reads: for an engine story, the control
rows ARE the observable behaviour. A stage that ran but wrote no
batch_stage_status row did not happen, as far as every screen, every recon
query and every agent is concerned. So the port is specified in terms of those
rows, and the tests specify the writes first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from cinqflow.core.model.vocabulary import (
    BatchState,
    ErrorCategory,
    FileState,
    Layer,
)

# The eleven. All Delta-backed at the target, Postgres-backed at rung 0.5,
# ALL linked by batch_id for end-to-end traceability.
CONTROL_TABLES: tuple[str, ...] = (
    "feed_sla_config",
    "input_registry",
    "schema_registry",
    "schema_drift_log",
    "batch_control",
    "batch_stage_status",
    "error_log",
    "quarantine_records",
    "batch_reconciliation",
    "sla_instance",
    "sla_alerts",
)


@dataclass(frozen=True)
class BatchControl:
    """batch_control — the batch's lifecycle, metrics and completion."""

    batch_id: str
    feed_id: str
    feed_version: int
    business_date: str
    state: BatchState
    started_ts: datetime
    completed_ts: datetime | None = None
    restart_count: int = 0
    # "every batch records the model version it loaded into" — FIG 10
    model_version: str | None = None


@dataclass(frozen=True)
class StageStatus:
    """batch_stage_status — per-stage execution, with the counts that must balance.

    records_in / records_out / quarantined / attributed_drops are not
    statistics. They are the terms of the balance equation, and reconciliation
    fails the batch when they do not satisfy it.
    """

    batch_id: str
    stage: Layer
    state: BatchState
    started_ts: datetime
    completed_ts: datetime | None = None
    records_in: int = 0
    records_out: int = 0
    quarantined: int = 0
    attributed_drops: int = 0


@dataclass(frozen=True)
class InputFile:
    """input_registry — file-level tracking, and the source of exactly-once.

    EVERY arriving file gets a row, including unexpected ones. An unexpected
    file is parked and surfaced, never ignored: "nothing disappears silently".
    """

    batch_id: str | None
    feed_id: str | None
    key: str
    filename: str
    size_bytes: int
    fingerprint: str
    state: FileState
    arrived_ts: datetime
    rejection_reason: str | None = None
    record_count: int | None = None

    @property
    def is_unexpected(self) -> bool:
        """A file matching no registered feed. Registered anyway — that is the point."""
        return self.feed_id is None


@dataclass(frozen=True)
class SchemaDrift:
    """schema_drift_log — observed structure vs the contract, per batch.

    Classified BY MEANING (`core.registry.contract.DriftKind`), not by
    structure: a rename is one event, not a dropped column plus a new one.
    Recorded for every non-NONE finding, whether or not it blocked the
    batch — an ADDED or REORDERED column is drift a steward should be able to
    see even when it never stopped anything.
    """

    batch_id: str
    feed_id: str
    classification: str
    column_name: str
    detail: str
    blocked_batch: bool
    detected_ts: datetime


@dataclass(frozen=True)
class ErrorRecord:
    """error_log — and the deterministic hash that is the quiet hero.

        error_id_hash = hash(batch_id, stage_name, record_key, error_type, rule_id)

    Deriving the identifier from the error's own facts makes replay idempotent
    AT THE ERROR LEVEL: reprocessing a corrected batch cannot manufacture
    duplicate incidents. That is precisely what lets "reprocess only the failed
    records" exist as a safe, ordinary button rather than control-row surgery.
    """

    error_id_hash: str
    batch_id: str
    stage: Layer
    category: ErrorCategory
    message: str
    occurred_ts: datetime
    record_key: str | None = None
    rule_id: str | None = None


@dataclass(frozen=True)
class QuarantineSummary:
    """quarantine_records, as the platform is allowed to REPORT it.

    Counts, reasons, rule identifiers and column names. NEVER row contents:
    no tool may emit a member-level row, and this is the shape that makes that
    enforceable rather than remembered — the summary type simply has nowhere to
    put a member.
    """

    batch_id: str
    stage: Layer
    rule_id: str
    reason: str
    column_names: tuple[str, ...]
    record_count: int


@dataclass(frozen=True)
class Reconciliation:
    """batch_reconciliation — expected vs actual per stage, and the verdict.

    "rows_in == rows_out + quarantined + attributed_drops, every stage,
     every batch"
    — docs/architecture/INVARIANTS.md, data plane
    """

    batch_id: str
    stage: Layer
    records_in: int
    records_out: int
    quarantined: int
    attributed_drops: int
    drop_ledger: tuple[DropLedgerEntry, ...] = field(default_factory=tuple)

    @property
    def balances(self) -> bool:
        return self.records_in == self.records_out + self.quarantined + self.attributed_drops

    @property
    def unexplained(self) -> int:
        """Zero, or the batch fails. An unexplained difference is a defect,
        never a footnote."""
        return self.records_in - (self.records_out + self.quarantined + self.attributed_drops)


@dataclass(frozen=True)
class DropLedgerEntry:
    """Every excluded record, attributed to the specific rule that excluded it.

    There is no category "other" and no category "unknown" — that is enforced
    as a schema-level constraint, not a code review comment, because the
    incident it prevents (silent row loss understating member rosters) is
    documented and expensive.
    """

    rule_id: str
    reason: str
    record_count: int
    financial_impact: Decimal | None = None


class ControlTableError(RuntimeError):
    """A control-plane write that could not be honoured."""


class BatchNotFoundError(ControlTableError):
    """Distinguished from a store failure, because a missing batch is a
    question about the data and a store failure is a page."""


@runtime_checkable
class ControlTablesPort(Protocol):
    """The eleven tables, in business language.

    Note the absence of a generic `execute(sql)` verb. A port that offers one
    lets a dialect leak into the core, and the core is the one place engine SQL
    is forbidden.
    """

    # ── writes: the observable behaviour of every engine story ───────────────
    def open_batch(self, batch: BatchControl) -> None: ...
    def update_batch_state(self, batch_id: str, state: BatchState) -> None: ...
    def record_stage(self, status: StageStatus) -> None: ...
    def register_input_file(self, file: InputFile) -> None: ...
    def link_input_to_batch(self, fingerprint: str, batch_id: str) -> None:
        """Back-fill the batch a file fed, once its batch exists.

        A file is registered on arrival, before a batch_id exists to record —
        landing decides before a batch opens. Calling this once one does is
        what makes "which file fed batch X?" answerable from the registry
        without waiting for a schema that records both at once.
        """
        ...
    def record_error(self, error: ErrorRecord) -> None: ...
    def record_quarantine(self, summary: QuarantineSummary) -> None: ...
    def record_reconciliation(self, recon: Reconciliation) -> None: ...
    def record_schema_drift(self, drift: SchemaDrift) -> None: ...

    # ── reads: what every screen and every certified query tool sees ─────────
    def get_batch(self, batch_id: str) -> BatchControl: ...
    def list_batches(self, feed_id: str, limit: int = 50) -> Sequence[BatchControl]: ...
    def get_stages(self, batch_id: str) -> Sequence[StageStatus]: ...
    def get_reconciliation(self, batch_id: str) -> Sequence[Reconciliation]: ...
    def get_quarantine_summary(self, batch_id: str) -> Sequence[QuarantineSummary]: ...
    def list_errors(
        self, batch_id: str, category: ErrorCategory | None = None
    ) -> Sequence[ErrorRecord]: ...
    def find_error_by_hash(self, error_id_hash: str) -> ErrorRecord | None:
        """The `error:<hash>` citation's own lookup — batch-independent, the
        way `find_input_by_fingerprint` already is for `file:`. A citation
        carries only the hash, never the batch_id that produced it."""
        ...
    def get_schema_drift(self, batch_id: str) -> Sequence[SchemaDrift]: ...
    def find_input_by_fingerprint(self, fingerprint: str) -> InputFile | None:
        """The exactly-once question, asked as one call.

        A non-None answer means this content has been seen: skip it, and write
        an audit entry. Never load it twice.
        """
        ...

    def get_input_registry(self, feed_id: str, limit: int = 50) -> Sequence[InputFile]: ...
