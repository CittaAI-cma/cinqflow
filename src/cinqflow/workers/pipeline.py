"""The pipeline runner — Landing -> Bronze -> Silver Raw, from stored metadata.

    "Write every run into batch_control and batch_stage_status exactly as the
     architecture defines."
    "Support restart from the last completed stage after a failure."
    — CF-V0-E8-01

This is the seam between the pure core and the fitted adapters. The core
decided everything; this module SEQUENCES those decisions and records them.

For an engine story the control rows ARE the observable behaviour, which is why
they are written at every stage boundary rather than at the end: a batch that
failed at Silver Raw must be able to say so, and a run that recorded nothing
until it succeeded could not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.core.compiler.execute import ExecutionResult, apply, error_category_for
from cinqflow.core.compiler.plan import LogicalPlan
from cinqflow.core.landing import LandingDecision, LandingOutcome, classify
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, FileState, Layer
from cinqflow.core.parsers import ParseError, parse
from cinqflow.core.recon import error_id_hash
from cinqflow.core.registry.contract import DqRule, SchemaContract, compare_to_contract
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.ports.control_tables import (
    BatchControl,
    ControlTablesPort,
    DropLedgerEntry,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    SchemaDrift,
    StageStatus,
)
from cinqflow.ports.storage import FileRef, StoragePort


@dataclass(frozen=True)
class RunOutcome:
    """What happened, in the words the platform uses everywhere else."""

    batch_id: str | None
    decision: LandingDecision
    state: BatchState | None = None
    stages_completed: tuple[Layer, ...] = ()
    result: ExecutionResult | None = None
    failure: str | None = None
    drift_blocked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def processed(self) -> bool:
        return self.state is BatchState.COMPLETED


class PipelineRunner:
    """Runs one file through the spine, recording every step."""

    def __init__(
        self,
        *,
        storage: StoragePort,
        control: ControlTablesPort,
        compute: PostgresCompute,
        source_system: str = "unknown",
    ) -> None:
        self._storage = storage
        self._control = control
        self._compute = compute
        self._source_system = source_system

    def run(
        self,
        file: FileRef,
        *,
        feed: FeedRecord,
        feed_version: int,
        contract: SchemaContract,
        rules: tuple[DqRule, ...],
        plan: LogicalPlan,
        business_date: str,
        resume_from: Layer | None = None,
        batch_id: str | None = None,
    ) -> RunOutcome:
        """Landing -> Bronze -> Silver Raw. Every outcome registers a file."""
        registered = self._storage.fingerprint(file.key) if file.fingerprint is None else file
        fingerprint = file.fingerprint or self._storage.fingerprint(file.key)
        file = FileRef(
            key=file.key,
            size_bytes=file.size_bytes,
            modified_ts=file.modified_ts,
            fingerprint=fingerprint,
        )
        _ = registered

        # ── G1 · landing: the single trust boundary ──────────────────────────
        #
        # A RESTART is an operational action on a batch that already exists, so
        # it does not pass through arrival dedup. That check exists to catch a
        # re-DELIVERY of the same content; applying it to a restart would make
        # recovery impossible for exactly the batches that need it — the file
        # is, of course, already in the input registry, because it arrived.
        restarting = resume_from is not None and batch_id is not None
        decision = classify(
            file,
            feeds=(feed.for_landing(feed_version),),
            fingerprint_seen=(
                not restarting and self._control.find_input_by_fingerprint(fingerprint) is not None
            ),
        )
        if not restarting:
            self._register(file, decision)

        if decision.outcome is not LandingOutcome.ACCEPTED:
            # SKIPPED, REJECTED and UNEXPECTED all end here — registered, moved
            # to the right folder, and visible. Nothing disappears silently.
            self._storage.move(file.key, decision.move_to)
            return RunOutcome(batch_id=None, decision=decision)

        batch = batch_id or self._new_batch_id()
        started = datetime.now(UTC)
        if resume_from is None:
            self._control.open_batch(
                BatchControl(
                    batch_id=batch,
                    feed_id=feed.feed_id,
                    feed_version=feed_version,
                    business_date=business_date,
                    state=BatchState.RECEIVED,
                    started_ts=started,
                )
            )
            # The file was registered before this batch existed — landing
            # decides before a batch opens. Back-fill the link now, or
            # "which file fed batch X?" is unanswerable from the registry.
            self._control.link_input_to_batch(fingerprint, batch)
        self._control.update_batch_state(batch, BatchState.IN_PROGRESS)
        if restarting:
            # "the restart is recorded on the batch" — CF-V0-E8-01, exception
            self._control.update_batch_state(batch, BatchState.RESTARTED)
            self._control.update_batch_state(batch, BatchState.IN_PROGRESS)

        try:
            return self._process(
                file=file,
                batch=batch,
                started=started,
                feed=feed,
                contract=contract,
                rules=rules,
                plan=plan,
                decision=decision,
                resume_from=resume_from,
            )
        except _StageFailureError as failure:
            self._fail(batch, failure)
            return RunOutcome(
                batch_id=batch,
                decision=decision,
                state=BatchState.FAILED,
                stages_completed=failure.completed,
                failure=failure.message,
                drift_blocked=failure.drift_blocked,
            )
        except Exception as crash:
            # An UNEXPECTED crash (a bug in an adapter, a transient failure
            # mid-transform) is not a _StageFailureError, but the batch must
            # still reach a terminal state: an IN_PROGRESS batch that never
            # resolves is invisible to reconciliation and to the agent, and no
            # operator would think to look for a run that "is still going"
            # three days later. Returned rather than re-raised, deliberately:
            # a real run commits inside ONE transaction (adapters/local/
            # pg_control.py:commit), so an exception escaping this method
            # would roll back the very FAILED-state and error rows this
            # records — "the control rows ARE the observable behaviour" only
            # holds if recording a crash cannot itself be undone by the crash.
            self._fail_unexpected(batch, crash)
            return RunOutcome(
                batch_id=batch,
                decision=decision,
                state=BatchState.FAILED,
                failure=str(crash),
            )

    def _fail_unexpected(self, batch: str, crash: Exception) -> None:
        self._control.record_error(
            ErrorRecord(
                error_id_hash=error_id_hash(
                    batch_id=batch,
                    stage=Layer.SILVER_RAW,
                    record_key=None,
                    error_type=ErrorCategory.SYSTEM,
                    rule_id=None,
                ),
                batch_id=batch,
                stage=Layer.SILVER_RAW,
                category=ErrorCategory.SYSTEM,
                message=f"the run crashed unexpectedly and did not complete: {crash}",
                occurred_ts=datetime.now(UTC),
            )
        )
        self._control.update_batch_state(batch, BatchState.FAILED)

    # ── the stages ───────────────────────────────────────────────────────────
    def _process(
        self,
        *,
        file: FileRef,
        batch: str,
        started: datetime,
        feed: FeedRecord,
        contract: SchemaContract,
        rules: tuple[DqRule, ...],
        plan: LogicalPlan,
        decision: LandingDecision,
        resume_from: Layer | None,
    ) -> RunOutcome:
        content = self._storage.read_bytes(file.key)
        try:
            parsed = parse(content, file_format=feed.file_format)
        except ParseError as exc:
            raise _StageFailureError(Layer.BRONZE, ErrorCategory.FILE, str(exc), ()) from None

        # G2's first half: drift, classified BY MEANING.
        findings = compare_to_contract(parsed.columns, contract)
        for finding in findings:
            self._control.record_schema_drift(
                SchemaDrift(
                    batch_id=batch,
                    feed_id=feed.feed_id,
                    classification=finding.kind.value,
                    column_name=finding.column,
                    detail=finding.detail,
                    blocked_batch=finding.blocks_batch,
                    detected_ts=datetime.now(UTC),
                )
            )
        blocking = tuple(f.detail for f in findings if f.blocks_batch)
        if blocking:
            raise _StageFailureError(
                Layer.BRONZE,
                ErrorCategory.SCHEMA,
                "; ".join(blocking),
                (),
                drift_blocked=blocking,
            )

        rows = (
            [
                {name: str(value) for name, value in zip(parsed.columns, row, strict=True)}
                for row in zip(
                    *[parsed.table.column(c).to_pylist() for c in parsed.columns], strict=True
                )
            ]
            if parsed.row_count
            else []
        )

        completed: list[Layer] = []
        skip_bronze = resume_from is not None and resume_from != Layer.BRONZE

        if not skip_bronze:
            # Bronze is append-only, so a resumed run must NOT re-land it:
            # a re-run would either duplicate or be refused by the trigger.
            landed = self._compute.land_bronze(
                plan=plan, batch_id=batch, rows=rows, source_system=self._source_system
            )
            self._control.record_stage(
                StageStatus(
                    batch_id=batch,
                    stage=Layer.BRONZE,
                    state=BatchState.COMPLETED,
                    started_ts=started,
                    completed_ts=datetime.now(UTC),
                    records_in=len(rows),
                    records_out=landed,
                )
            )
        else:
            # Resuming: the derived layers are rebuilt, so clear this batch's
            # previous attempt first. Bronze is untouched — it is append-only,
            # and it is what we are rebuilding FROM.
            self._compute.clear_derived_layers(batch)
        completed.append(Layer.BRONZE)

        # G2's second half plus G3: cast, map, rules, and the balance equation.
        result = apply(plan, rows=rows, contract=contract, rules=rules, batch_id=batch)
        self._compute.load_silver_raw(
            plan=plan, batch_id=batch, result=result, source_system=self._source_system
        )
        self._compute.record_recon_history(result=result, feed_id=feed.feed_id)
        completed.append(Layer.SILVER_RAW)

        # "Fail the batch loudly if the equation does not balance" — computed
        # ONCE, before any control row is written, so the stage row and the
        # batch row can never disagree about whether this run succeeded.
        state = BatchState.COMPLETED if result.balances else BatchState.FAILED

        self._control.record_stage(
            StageStatus(
                batch_id=batch,
                stage=Layer.SILVER_RAW,
                state=state,
                started_ts=started,
                completed_ts=datetime.now(UTC),
                records_in=result.reconciliation.records_in,
                records_out=result.reconciliation.records_out,
                quarantined=len(result.quarantined),
                attributed_drops=result.reconciliation.attributed_drops,
            )
        )
        self._record_exclusions(batch, result)
        self._record_reconciliation(batch, result)
        self._control.update_batch_state(batch, state)
        self._storage.move(file.key, decision.move_to)

        return RunOutcome(
            batch_id=batch,
            decision=decision,
            state=state,
            stages_completed=tuple(completed),
            result=result,
        )

    # ── control-plane bookkeeping ────────────────────────────────────────────
    def _register(self, file: FileRef, decision: LandingDecision) -> None:
        """100% of arriving files have a registry entry — the measurable bar."""
        state = {
            LandingOutcome.ACCEPTED: FileState.ACCEPTED,
            LandingOutcome.REJECTED: FileState.REJECTED,
            LandingOutcome.UNEXPECTED: FileState.RECEIVED,
            LandingOutcome.SKIPPED: FileState.PROCESSED,
        }[decision.outcome]
        self._control.register_input_file(
            InputFile(
                batch_id=None,
                feed_id=decision.feed_id,
                key=file.key,
                filename=file.filename,
                size_bytes=file.size_bytes,
                fingerprint=file.fingerprint or "",
                state=state,
                arrived_ts=file.modified_ts,
                rejection_reason=decision.reason,
            )
        )

    def _record_exclusions(self, batch: str, result: ExecutionResult) -> None:
        """One error row per excluded record, deduplicated by the hash.

        The hash is derived from the error's own facts, so reprocessing a
        corrected batch cannot manufacture duplicate incidents.
        """
        for dropped in result.quarantined:
            category = error_category_for(dropped.rule_id)
            self._control.record_error(
                ErrorRecord(
                    error_id_hash=error_id_hash(
                        batch_id=batch,
                        stage=Layer.SILVER_RAW,
                        record_key=str(dropped.row_number),
                        error_type=category,
                        rule_id=dropped.rule_id,
                    ),
                    batch_id=batch,
                    stage=Layer.SILVER_RAW,
                    category=category,
                    message=dropped.reason,
                    record_key=str(dropped.row_number),
                    rule_id=dropped.rule_id,
                    occurred_ts=datetime.now(UTC),
                )
            )
        for entry in result.reconciliation.drops:
            self._control.record_quarantine(
                QuarantineSummary(
                    batch_id=batch,
                    stage=Layer.SILVER_RAW,
                    rule_id=entry.rule_id,
                    reason=entry.reason,
                    column_names=entry.columns,
                    record_count=entry.record_count,
                )
            )

    def _record_reconciliation(self, batch: str, result: ExecutionResult) -> None:
        recon = result.reconciliation
        self._control.record_reconciliation(
            Reconciliation(
                batch_id=batch,
                stage=Layer.SILVER_RAW,
                records_in=recon.records_in,
                records_out=recon.records_out,
                quarantined=recon.quarantined,
                attributed_drops=recon.attributed_drops,
                drop_ledger=tuple(
                    DropLedgerEntry(rule_id=d.rule_id, reason=d.reason, record_count=d.record_count)
                    for d in recon.drops
                ),
            )
        )

    def _fail(self, batch: str, failure: _StageFailureError) -> None:
        self._control.record_error(
            ErrorRecord(
                error_id_hash=error_id_hash(
                    batch_id=batch,
                    stage=failure.stage,
                    record_key=None,
                    error_type=failure.category,
                    rule_id=None,
                ),
                batch_id=batch,
                stage=failure.stage,
                category=failure.category,
                message=failure.message,
                occurred_ts=datetime.now(UTC),
            )
        )
        self._control.update_batch_state(batch, BatchState.FAILED)

    @staticmethod
    def _new_batch_id() -> str:
        return uuid.uuid4().hex[:12]


class _StageFailureError(Exception):
    def __init__(
        self,
        stage: Layer,
        category: ErrorCategory,
        message: str,
        completed: tuple[Layer, ...],
        drift_blocked: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.category = category
        self.message = message
        self.completed = completed
        self.drift_blocked = drift_blocked
