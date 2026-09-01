"""memstore — the eleven control tables in memory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime

from cinqflow.core.identity import CrosswalkEntry
from cinqflow.core.identity.telemetry import CoverageSnapshot, ParityCheckSummary
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory
from cinqflow.ports import port
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    ControlTableError,
    ErrorRecord,
    FeedSlaConfig,
    IdentityRequestLogEntry,
    IdentityResponseLogEntry,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    RuleResult,
    SchemaDrift,
    SlaAlert,
    SlaCycle,
    StageStatus,
)


@port("control_tables", "mock")
class MemStoreControlTables:
    """In-memory, but with the SAME semantics the Postgres rendering must have.

    Two of those semantics are guarantees rather than storage details, so they
    are implemented here rather than left to the real adapter:

      • a fingerprint is unique across input_registry — that IS exactly-once
      • an error_id_hash is idempotent — recording the same error twice leaves
        one row, so reprocessing a corrected batch cannot manufacture duplicate
        incidents
    """

    def __init__(self) -> None:
        self._batches: dict[str, BatchControl] = {}
        self._stages: dict[str, list[StageStatus]] = {}
        self._inputs: dict[str, InputFile] = {}  # keyed by fingerprint
        self._errors: dict[str, ErrorRecord] = {}  # keyed by error_id_hash
        self._quarantine: dict[str, list[QuarantineSummary]] = {}
        self._recon: dict[str, list[Reconciliation]] = {}
        self._drift: dict[str, list[SchemaDrift]] = {}
        self._rule_results: list[RuleResult] = []
        self._sla_configs: dict[tuple[str, int], FeedSlaConfig] = {}
        self._cycles: dict[tuple[str, date], SlaCycle] = {}
        self._alerts: dict[str, SlaAlert] = {}
        self._identity_requests: list[IdentityRequestLogEntry] = []
        self._identity_responses: list[IdentityResponseLogEntry] = []
        # keyed by (source_system, source_member_id, batch_id) — the schema's
        # own unique constraint, and the worker's idempotency check.
        self._crosswalk: dict[tuple[str, str, str], CrosswalkEntry] = {}
        self._coverage: dict[tuple[str, str], CoverageSnapshot] = {}
        self._parity: dict[tuple[str, str], ParityCheckSummary] = {}

    # ── writes ───────────────────────────────────────────────────────────────
    def open_batch(self, batch: BatchControl) -> None:
        self._batches[batch.batch_id] = batch
        self._stages.setdefault(batch.batch_id, [])

    def update_batch_state(self, batch_id: str, state: BatchState) -> None:
        batch = self.get_batch(batch_id)
        self._batches[batch_id] = replace(batch, state=state)

    def record_model_version(self, batch_id: str, model_version: str) -> None:
        batch = self.get_batch(batch_id)
        self._batches[batch_id] = replace(batch, model_version=model_version)

    def record_stage(self, status: StageStatus) -> None:
        stages = self._stages.setdefault(status.batch_id, [])
        for index, existing in enumerate(stages):
            if existing.stage is status.stage:
                stages[index] = status
                return
        stages.append(status)

    def register_input_file(self, file: InputFile) -> None:
        # Registered even when unexpected. Parked and surfaced, never ignored.
        self._inputs.setdefault(file.fingerprint, file)

    def link_input_to_batch(self, fingerprint: str, batch_id: str) -> None:
        existing = self._inputs.get(fingerprint)
        if existing is not None:
            self._inputs[fingerprint] = replace(existing, batch_id=batch_id)

    def record_error(self, error: ErrorRecord) -> None:
        # Idempotent by hash: replay cannot duplicate an incident.
        self._errors.setdefault(error.error_id_hash, error)

    def record_quarantine(self, summary: QuarantineSummary) -> None:
        self._quarantine.setdefault(summary.batch_id, []).append(summary)

    def record_reconciliation(self, recon: Reconciliation) -> None:
        self._recon.setdefault(recon.batch_id, []).append(recon)

    def record_schema_drift(self, drift: SchemaDrift) -> None:
        self._drift.setdefault(drift.batch_id, []).append(drift)

    def record_rule_result(self, result: RuleResult) -> None:
        self._rule_results.append(result)

    # ── the identity stage · CF-V3-E9-01 ──────────────────────────────────────
    def record_identity_request(self, entry: IdentityRequestLogEntry) -> None:
        self._identity_requests.append(entry)

    def record_identity_response(self, entry: IdentityResponseLogEntry) -> None:
        self._identity_responses.append(entry)

    def record_crosswalk(self, entry: CrosswalkEntry) -> None:
        key = (entry.source_system, entry.source_member_id, entry.batch_id)
        self._crosswalk[key] = entry

    def get_crosswalk(
        self, *, source_system: str, source_member_id: str, batch_id: str
    ) -> CrosswalkEntry | None:
        return self._crosswalk.get((source_system, source_member_id, batch_id))

    def list_crosswalk(self, batch_id: str) -> Sequence[CrosswalkEntry]:
        return tuple(e for e in self._crosswalk.values() if e.batch_id == batch_id)

    # ── coverage and parity telemetry · CF-V3-E9-04 ───────────────────────────
    def record_coverage_snapshot(self, snapshot: CoverageSnapshot) -> None:
        self._coverage[(snapshot.source_system, snapshot.business_date)] = snapshot

    def coverage_history(self, source_system: str, *, days: int = 90) -> Sequence[CoverageSnapshot]:
        found = [s for s in self._coverage.values() if s.source_system == source_system]
        return tuple(sorted(found, key=lambda s: s.business_date, reverse=True)[:days])

    def record_parity_check(self, summary: ParityCheckSummary) -> None:
        self._parity[(summary.source_system, summary.business_date)] = summary

    def parity_check_history(
        self, source_system: str, *, days: int = 90
    ) -> Sequence[ParityCheckSummary]:
        found = [p for p in self._parity.values() if p.source_system == source_system]
        return tuple(sorted(found, key=lambda p: p.business_date, reverse=True)[:days])

    # ── the SLA clock ────────────────────────────────────────────────────────
    def upsert_feed_sla_config(self, config: FeedSlaConfig) -> None:
        self._sla_configs[(config.feed_id, config.feed_version)] = config

    def upsert_sla_instance(self, cycle: SlaCycle) -> None:
        key = (cycle.feed_id, cycle.cycle_date)
        existing = self._cycles.get(key)
        if existing is not None:
            # NEVER OVERWRITE actual_ts — arrival is the pipeline's fact, not
            # the clock's. A worker replaying today's materialisation must
            # not blank an arrival ten minutes old.
            cycle = replace(
                cycle,
                actual_ts=existing.actual_ts,
                batch_id=existing.batch_id,
                sla_status=existing.sla_status if existing.actual_ts else cycle.sla_status,
                sla_instance_id=existing.sla_instance_id,
            )
        self._cycles[key] = cycle

    def record_sla_arrival(
        self,
        *,
        feed_id: str,
        cycle_date: date,
        actual_ts: datetime,
        status: str,
        batch_id: str | None = None,
    ) -> None:
        key = (feed_id, cycle_date)
        existing = self._cycles.get(key)
        if existing is None:
            raise ControlTableError(
                f"{feed_id}: no cycle materialised for {cycle_date} — the clock must run "
                "before an arrival can be recorded against it"
            )
        self._cycles[key] = replace(
            existing, actual_ts=actual_ts, batch_id=batch_id, sla_status=status
        )

    def record_sla_alert(self, alert: SlaAlert) -> None:
        if (alert.feed_id, alert.cycle_date) not in self._cycles:
            raise ControlTableError(
                f"{alert.feed_id}: no cycle materialised for {alert.cycle_date} — an alert "
                "about a cycle that does not exist is a bug, not a row worth writing"
            )
        self._alerts[alert.alert_id] = alert

    def acknowledge_alert(self, alert_id: str, *, by: str, at: datetime) -> None:
        existing = self._alerts.get(alert_id)
        if existing is None:
            raise ControlTableError(f"no such alert: {alert_id!r}")
        self._alerts[alert_id] = replace(existing, acknowledged_by=by, acknowledged_ts=at)

    # ── reads ────────────────────────────────────────────────────────────────
    def get_batch(self, batch_id: str) -> BatchControl:
        try:
            return self._batches[batch_id]
        except KeyError:
            raise BatchNotFoundError(batch_id) from None

    def list_batches(self, feed_id: str, limit: int = 50) -> Sequence[BatchControl]:
        found = [b for b in self._batches.values() if b.feed_id == feed_id]
        return sorted(found, key=lambda b: b.started_ts, reverse=True)[:limit]

    def get_stages(self, batch_id: str) -> Sequence[StageStatus]:
        return tuple(self._stages.get(batch_id, ()))

    def get_reconciliation(self, batch_id: str) -> Sequence[Reconciliation]:
        return tuple(self._recon.get(batch_id, ()))

    def get_quarantine_summary(self, batch_id: str) -> Sequence[QuarantineSummary]:
        return tuple(self._quarantine.get(batch_id, ()))

    def get_schema_drift(self, batch_id: str) -> Sequence[SchemaDrift]:
        return tuple(self._drift.get(batch_id, ()))

    def rule_results(self, batch_id: str) -> Sequence[RuleResult]:
        newest: dict[str, RuleResult] = {}
        for row in self._rule_results:
            if row.batch_id != batch_id:
                continue
            held = newest.get(row.rule_id)
            if held is None or row.recorded_ts >= held.recorded_ts:
                newest[row.rule_id] = row
        return tuple(sorted(newest.values(), key=lambda r: r.rule_id))

    def rule_result_history(self, feed_id: str, *, limit: int = 200) -> Sequence[RuleResult]:
        found = [r for r in self._rule_results if r.feed_id == feed_id]
        found.sort(key=lambda r: r.recorded_ts, reverse=True)
        return tuple(found[:limit])

    def list_errors(
        self, batch_id: str, category: ErrorCategory | None = None
    ) -> Sequence[ErrorRecord]:
        found = [e for e in self._errors.values() if e.batch_id == batch_id]
        if category is not None:
            found = [e for e in found if e.category is category]
        return tuple(sorted(found, key=lambda e: e.occurred_ts))

    def find_error_by_hash(self, error_id_hash: str) -> ErrorRecord | None:
        return self._errors.get(error_id_hash)

    def find_input_by_fingerprint(self, fingerprint: str) -> InputFile | None:
        return self._inputs.get(fingerprint)

    def get_input_registry(self, feed_id: str, limit: int = 50) -> Sequence[InputFile]:
        found = [f for f in self._inputs.values() if f.feed_id == feed_id]
        return tuple(sorted(found, key=lambda f: f.arrived_ts, reverse=True)[:limit])

    def list_batch_inputs(self, batch_id: str) -> Sequence[InputFile]:
        found = [f for f in self._inputs.values() if f.batch_id == batch_id]
        return tuple(sorted(found, key=lambda f: f.arrived_ts))

    def feed_sla_configs(self, *, feed_ids: Sequence[str] = ()) -> Sequence[FeedSlaConfig]:
        found = list(self._sla_configs.values())
        if feed_ids:
            found = [c for c in found if c.feed_id in feed_ids]
        return tuple(sorted(found, key=lambda c: (c.feed_id, c.feed_version)))

    def sla_instances(
        self, *, cycle_date: date, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaCycle]:
        found = [c for c in self._cycles.values() if c.cycle_date == cycle_date]
        if feed_ids:
            found = [c for c in found if c.feed_id in feed_ids]
        return tuple(sorted(found, key=lambda c: c.feed_id))

    def sla_history(self, feed_id: str, *, days: int = 90) -> Sequence[SlaCycle]:
        found = [c for c in self._cycles.values() if c.feed_id == feed_id]
        return tuple(sorted(found, key=lambda c: c.cycle_date, reverse=True)[:days])

    def sla_alerts(
        self, *, cycle_date: date | None = None, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaAlert]:
        found = list(self._alerts.values())
        if cycle_date is not None:
            found = [a for a in found if a.cycle_date == cycle_date]
        if feed_ids:
            found = [a for a in found if a.feed_id in feed_ids]
        return tuple(sorted(found, key=lambda a: a.raised_ts))
