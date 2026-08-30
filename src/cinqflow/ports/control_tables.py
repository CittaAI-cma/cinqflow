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
from datetime import date, datetime
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


@dataclass(frozen=True)
class RuleResult:
    """A row of `control.rule_results` — one rule's verdict on one batch,
    INCLUDING the clean pass.

        "Record every rule's execution result on every batch, including clean
         passes — silence is data too." — CF-V2-E7-05

    The drop ledger already names the rules that FIRED; without this table a
    rule that passed and a rule that never ran are indistinguishable, and the
    DQ trend's denominator is a ratio over failures. `evaluated` is the rows
    the rule saw; `failed` is the rows it flagged; `excluded` is the subset
    the consequence actually removed — a warn-severity rule fails rows and
    excludes none, and collapsing those two numbers would hide exactly the
    rules a steward is deciding thresholds for.
    """

    batch_id: str
    feed_id: str
    rule_id: str
    evaluated: int
    failed: int
    excluded: int
    recorded_ts: datetime

    @property
    def clean(self) -> bool:
        return self.failed == 0

    @property
    def pass_rate(self) -> float:
        """0.0–1.0. An unevaluated rule has no rate — the caller filters on
        `evaluated` first; answering 1.0 here would make a skipped rule look
        like a perfect one, which is the story's own don't."""
        if self.evaluated <= 0:
            return 0.0
        return (self.evaluated - self.failed) / self.evaluated


@dataclass(frozen=True)
class FeedSlaConfig:
    """A row of `control.feed_sla_config` — a feed's delivery contract.

    CF-V2-E12-01/05. PRIMARY KEY IS `(feed_id, feed_version)`, deliberately:
    version 2 of a feed's schedule does not overwrite version 1, because a
    batch that ran under the old cadence must still be judged against the
    window it was actually owed. Re-publishing the SAME version amends the
    row — a payer moving their delivery from 06:00 to 05:00 does not grow a
    second contract for the version they are still on.
    """

    feed_id: str
    feed_version: int
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    created_ts: datetime
    expected_file_count: int | None = None
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    grace_period_minutes: int | None = None


@dataclass(frozen=True)
class SlaCycle:
    """A row of `control.sla_instance` — one delivery the platform is owed.

    `sla_status` is the CHECK constraint's own three words — `On-Time`,
    `Delayed`, `Breached` — computed by `core.sla` and passed in rather than
    derived here, because the port stores facts and `core/` decides what they
    mean.
    """

    feed_id: str
    cycle_date: date
    expected_ts: datetime
    sla_status: str
    batch_id: str | None = None
    actual_ts: datetime | None = None
    sla_instance_id: str | None = None


@dataclass(frozen=True)
class SlaAlert:
    """A row of `control.sla_alerts` — an alert, before it is acknowledged.

    KEYED ON `(feed_id, cycle_date)`, matching `core.sla.SlaAlert` exactly —
    not on `sla_instance_id`. The table's own foreign key IS the instance id,
    but the instance a caller means is always "the cycle for this feed on
    this day", and making callers resolve a UUID themselves before they can
    raise an alert is a lookup this port should do once rather than a lookup
    every caller repeats slightly differently. The adapter resolves the FK;
    a cycle that has not been materialised yet is a bug, and both adapters
    refuse it the same way `record_sla_arrival` does.

    `citations` is not decoration: an alert whose facts cannot be opened is
    the context-free alert CF-V2-E12-05 exists to abolish.
    """

    alert_id: str
    feed_id: str
    cycle_date: date
    severity: str
    summary: str
    raised_ts: datetime
    batch_id: str | None = None
    citations: tuple[str, ...] = ()
    dispatched_ts: datetime | None = None
    acknowledged_by: str = ""
    acknowledged_ts: datetime | None = None


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
    def record_rule_result(self, result: RuleResult) -> None:
        """CF-V2-E7-05. Append-only; a re-run records again and reads take the
        newest row per (batch, rule), the same way every ops ledger folds."""
        ...

    # ── the SLA clock — CF-V2-E12-01/05, the missing writer ──────────────────
    def upsert_feed_sla_config(self, config: FeedSlaConfig) -> None:
        """Idempotent on `(feed_id, feed_version)` — publishing the same
        version twice amends it rather than duplicating it."""
        ...

    def upsert_sla_instance(self, cycle: SlaCycle) -> None:
        """Idempotent on `(feed_id, cycle_date)`. NEVER OVERWRITES `actual_ts`
        — arrival is recorded by the pipeline, through `record_sla_arrival`,
        not by the clock. That is the whole of the worker's idempotency
        guarantee: it can run on any cadence, restart mid-run, or be replayed
        by a chaos test, and the second pass is a no-op."""
        ...

    def record_sla_arrival(
        self,
        *,
        feed_id: str,
        cycle_date: date,
        actual_ts: datetime,
        status: str,
        batch_id: str | None = None,
    ) -> None:
        """THE ONE PLACE `actual_ts` IS EVER WRITTEN. Called by the pipeline
        when a file registers for a cycle the clock already materialised —
        never by `upsert_sla_instance`, which is why the two are separate
        verbs rather than one with an optional argument nobody would
        remember to omit."""
        ...

    def record_sla_alert(self, alert: SlaAlert) -> None:
        """Refuses (`ControlTableError`) when `(alert.feed_id,
        alert.cycle_date)` names no materialised cycle — an alert about a
        cycle that does not exist is a bug in the caller, not a row worth
        writing."""
        ...

    def acknowledge_alert(self, alert_id: str, *, by: str, at: datetime) -> None:
        """Refuses (`ControlTableError`) for an alert id that was never
        raised — an acknowledgement of nothing is a lie in the audit trail,
        not a no-op."""
        ...

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

    def list_batch_inputs(self, batch_id: str) -> Sequence[InputFile]:
        """Every file registered against ONE run — the batch drawer's Inputs
        tab. Distinct from `get_input_registry`, which is a FEED's history
        across every run; a drawer open on `batch:8842` has a batch_id, not a
        feed_id, and must never be handed the wrong scope to make that work.
        """
        ...

    # ── the SLA clock — reads ─────────────────────────────────────────────────
    def feed_sla_configs(self, *, feed_ids: Sequence[str] = ()) -> Sequence[FeedSlaConfig]:
        """Every version on file, unfiltered by default. `feed_ids` narrows to
        what a scoped caller may see — filtering here, in the query, is what
        keeps an out-of-scope feed's contract out of a response body before
        anybody checks a permission."""
        ...

    def sla_instances(
        self, *, cycle_date: date, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaCycle]: ...
    def sla_history(self, feed_id: str, *, days: int = 90) -> Sequence[SlaCycle]:
        """Newest first, bounded. The reliability trend reads this, and ninety
        days of a 15-minute ADT feed is 8,640 rows nobody asked for."""
        ...

    def sla_alerts(
        self, *, cycle_date: date | None = None, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaAlert]: ...

    def rule_results(self, batch_id: str) -> Sequence[RuleResult]:
        """One row per rule, the newest per (batch, rule) — what E7-05's
        per-batch view renders, clean passes included."""
        ...

    def rule_result_history(self, feed_id: str, *, limit: int = 200) -> Sequence[RuleResult]:
        """Newest first, bounded — the DQ trend's input, and the reliability
        score's DQ signal. Bounded for the same reason `sla_history` is."""
        ...
