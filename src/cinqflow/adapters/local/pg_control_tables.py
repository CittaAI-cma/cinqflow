"""The eleven control tables, on Postgres. Rung 0.5's real control plane.

The SAME contract suite that runs against the memstore mock runs against this.
That is the whole mechanism: a second adapter is a certification, not a
migration — and any behaviour the mock has that this one lacks fails there,
loudly, rather than being discovered when the demo runs.

Note what stays identical between the two: a fingerprint is unique, an
error_id_hash is idempotent, and one stage has one row. Those are guarantees,
not storage details, so both implementations must have them — here they are
database constraints, and in the mock they are dict semantics.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.identity.telemetry import CoverageSnapshot, ParityCheckSummary
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, FileState, Layer
from cinqflow.ports import port
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    ControlTableError,
    DropLedgerEntry,
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


@port("control_tables", "pg-control")
class PostgresControlTables:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    # ── writes ───────────────────────────────────────────────────────────────
    def open_batch(self, batch: BatchControl) -> None:
        self._db.execute(
            "INSERT INTO control.batch_control (batch_id, feed_id, feed_version, business_date, "
            "state, started_ts, completed_ts, restart_count, model_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (batch_id) DO NOTHING",
            (
                batch.batch_id,
                batch.feed_id,
                batch.feed_version,
                batch.business_date,
                batch.state.value,
                batch.started_ts,
                batch.completed_ts,
                batch.restart_count,
                batch.model_version,
            ),
        )

    def update_batch_state(self, batch_id: str, state: BatchState) -> None:
        self.get_batch(batch_id)  # a state change on a missing batch is a bug, not a no-op
        self._db.execute(
            "UPDATE control.batch_control SET state = %s, completed_ts = "
            "CASE WHEN %s IN ('COMPLETED','FAILED') THEN now() ELSE completed_ts END "
            "WHERE batch_id = %s",
            (state.value, state.value, batch_id),
        )

    def record_model_version(self, batch_id: str, model_version: str) -> None:
        self.get_batch(batch_id)  # a stamp on a missing batch is a bug, not a no-op
        self._db.execute(
            "UPDATE control.batch_control SET model_version = %s WHERE batch_id = %s",
            (model_version, batch_id),
        )

    def record_stage(self, status: StageStatus) -> None:
        # One row per stage per batch. Two rows would double every count a
        # screen or a recon query reads, so the upsert is on the key.
        self._db.execute(
            "INSERT INTO control.batch_stage_status (batch_id, stage_name, state, started_ts, "
            "completed_ts, records_in, records_out, quarantined, attributed_drops) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (batch_id, stage_name) DO UPDATE SET "
            "state = EXCLUDED.state, completed_ts = EXCLUDED.completed_ts, "
            "records_in = EXCLUDED.records_in, records_out = EXCLUDED.records_out, "
            "quarantined = EXCLUDED.quarantined, attributed_drops = EXCLUDED.attributed_drops",
            (
                status.batch_id,
                status.stage.value,
                status.state.value,
                status.started_ts,
                status.completed_ts,
                status.records_in,
                status.records_out,
                status.quarantined,
                status.attributed_drops,
            ),
        )

    def register_input_file(self, file: InputFile) -> None:
        # ON CONFLICT on the fingerprint IS exactly-once: a second registration
        # of the same content is a no-op even from a path that forgot to look.
        self._db.execute(
            "INSERT INTO control.input_registry (input_id, batch_id, feed_id, file_key, "
            "filename, size_bytes, fingerprint, state, arrived_ts, rejection_reason, "
            "record_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (fingerprint) DO NOTHING",
            (
                str(uuid.uuid4()),
                file.batch_id,
                file.feed_id,
                file.key,
                file.filename,
                file.size_bytes,
                file.fingerprint,
                file.state.value,
                file.arrived_ts,
                file.rejection_reason,
                file.record_count,
            ),
        )

    def link_input_to_batch(self, fingerprint: str, batch_id: str) -> None:
        self._db.execute(
            "UPDATE control.input_registry SET batch_id = %s WHERE fingerprint = %s",
            (batch_id, fingerprint),
        )

    def record_error(self, error: ErrorRecord) -> None:
        # Idempotent by hash: replay cannot manufacture a duplicate incident.
        self._db.execute(
            "INSERT INTO control.error_log (error_id_hash, batch_id, stage_name, "
            "error_category, message, record_key, rule_id, occurred_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (error_id_hash) DO NOTHING",
            (
                error.error_id_hash,
                error.batch_id,
                error.stage.value,
                error.category.value,
                error.message,
                error.record_key,
                error.rule_id,
                error.occurred_ts,
            ),
        )

    def record_quarantine(self, summary: QuarantineSummary) -> None:
        self._db.execute(
            "INSERT INTO control.quarantine_records (quarantine_id, batch_id, stage_name, "
            "rule_id, reason, column_names, record_count, quarantined_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s, now())",
            (
                str(uuid.uuid4()),
                summary.batch_id,
                summary.stage.value,
                summary.rule_id,
                summary.reason,
                json.dumps(list(summary.column_names)),
                summary.record_count,
            ),
        )

    def record_reconciliation(self, recon: Reconciliation) -> None:
        rows = recon.drop_ledger or (None,)
        for entry in rows:
            self._db.execute(
                "INSERT INTO control.batch_reconciliation (recon_id, batch_id, stage_name, "
                "records_in, records_out, quarantined, attributed_drops, drop_rule_id, "
                "drop_reason, drop_count, financial_impact, balanced, reconciled_ts) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
                (
                    str(uuid.uuid4()),
                    recon.batch_id,
                    recon.stage.value,
                    recon.records_in,
                    recon.records_out,
                    recon.quarantined,
                    recon.attributed_drops,
                    entry.rule_id if entry else None,
                    entry.reason if entry else None,
                    entry.record_count if entry else None,
                    entry.financial_impact if entry else None,
                    recon.balances,
                ),
            )

    def record_schema_drift(self, drift: SchemaDrift) -> None:
        self._db.execute(
            "INSERT INTO control.schema_drift_log (drift_id, batch_id, feed_id, "
            "classification, column_name, detail, blocked_batch, detected_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                drift.batch_id,
                drift.feed_id,
                drift.classification,
                drift.column_name,
                drift.detail,
                drift.blocked_batch,
                drift.detected_ts,
            ),
        )

    # ── the identity stage · CF-V3-E9-01 ──────────────────────────────────────
    def record_identity_request(self, entry: IdentityRequestLogEntry) -> None:
        self._db.execute(
            "INSERT INTO identity.verato_request_log (request_id, batch_id, source_system, "
            "source_member_id, payload, payload_hash, sent_ts) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                entry.request_id,
                entry.batch_id,
                entry.source_system,
                entry.source_member_id,
                json.dumps(entry.payload),
                entry.payload_hash,
                entry.sent_ts,
            ),
        )

    def record_identity_response(self, entry: IdentityResponseLogEntry) -> None:
        self._db.execute(
            "INSERT INTO identity.verato_response_log (response_id, request_id, batch_id, "
            "payload, payload_hash, outcome, received_ts) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                entry.response_id,
                entry.request_id,
                entry.batch_id,
                json.dumps(entry.payload),
                entry.payload_hash,
                entry.outcome.value,
                entry.received_ts,
            ),
        )

    def record_crosswalk(self, entry: CrosswalkEntry) -> None:
        # Upsert on the schema's own unique constraint — the idempotency
        # guarantee itself, not a check the caller performs first.
        self._db.execute(
            "INSERT INTO identity.bridge_member_source_to_verato (crosswalk_id, source_system, "
            "source_member_id, internal_member_id, verato_person_id, outcome, "
            "match_confidence_score, batch_id, effective_date, created_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, CURRENT_DATE, now()) "
            "ON CONFLICT (source_system, source_member_id, batch_id) DO UPDATE SET "
            "internal_member_id = EXCLUDED.internal_member_id, "
            "verato_person_id = EXCLUDED.verato_person_id, outcome = EXCLUDED.outcome, "
            "match_confidence_score = EXCLUDED.match_confidence_score",
            (
                str(uuid.uuid4()),
                entry.source_system,
                entry.source_member_id,
                entry.internal_member_id,
                entry.verato_person_id,
                entry.outcome.value,
                entry.match_confidence_score,
                entry.batch_id,
            ),
        )

    # ── the SLA clock — CF-V2-E12-01/05, the missing writer ──────────────────
    def upsert_feed_sla_config(self, config: FeedSlaConfig) -> None:
        self._db.execute(
            "INSERT INTO control.feed_sla_config (feed_id, feed_version, domain, "
            "source_system, file_format, landing_path, file_pattern, schedule_cron, "
            "expected_file_count, min_size_bytes, max_size_bytes, grace_period_minutes, "
            "created_ts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (feed_id, feed_version) DO UPDATE SET "
            "domain = EXCLUDED.domain, source_system = EXCLUDED.source_system, "
            "file_format = EXCLUDED.file_format, landing_path = EXCLUDED.landing_path, "
            "file_pattern = EXCLUDED.file_pattern, schedule_cron = EXCLUDED.schedule_cron, "
            "expected_file_count = EXCLUDED.expected_file_count, "
            "min_size_bytes = EXCLUDED.min_size_bytes, max_size_bytes = EXCLUDED.max_size_bytes, "
            "grace_period_minutes = EXCLUDED.grace_period_minutes",
            (
                config.feed_id,
                config.feed_version,
                config.domain,
                config.source_system,
                config.file_format,
                config.landing_path,
                config.file_pattern,
                config.schedule_cron,
                config.expected_file_count,
                config.min_size_bytes,
                config.max_size_bytes,
                config.grace_period_minutes,
                config.created_ts,
            ),
        )

    def upsert_sla_instance(self, cycle: SlaCycle) -> None:
        # NEVER OVERWRITE actual_ts on conflict — arrival is the pipeline's
        # fact, written through `record_sla_arrival`, never re-derived here.
        # A worker replaying today's materialisation must not blank an
        # arrival ten minutes old, which is exactly what `DO UPDATE SET
        # actual_ts = EXCLUDED.actual_ts` would do on every re-run.
        self._db.execute(
            "INSERT INTO control.sla_instance (sla_instance_id, feed_id, batch_id, "
            "cycle_date, expected_ts, actual_ts, sla_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (feed_id, cycle_date) DO UPDATE SET "
            "expected_ts = EXCLUDED.expected_ts",
            (
                str(uuid.uuid4()),
                cycle.feed_id,
                cycle.batch_id,
                cycle.cycle_date,
                cycle.expected_ts,
                cycle.actual_ts,
                cycle.sla_status,
            ),
        )

    def record_sla_arrival(
        self,
        *,
        feed_id: str,
        cycle_date: date,
        actual_ts: datetime,
        status: str,
        batch_id: str | None = None,
    ) -> None:
        result = self._db.fetch_one(
            "UPDATE control.sla_instance SET actual_ts = %s, batch_id = %s, sla_status = %s "
            "WHERE feed_id = %s AND cycle_date = %s RETURNING sla_instance_id",
            (actual_ts, batch_id, status, feed_id, cycle_date),
        )
        if result is None:
            raise ControlTableError(
                f"{feed_id}: no cycle materialised for {cycle_date} — the clock must run "
                "before an arrival can be recorded against it"
            )

    def record_sla_alert(self, alert: SlaAlert) -> None:
        # The table's foreign key is `sla_instance_id`; the caller thinks in
        # (feed_id, cycle_date). Resolving it here means every caller asks the
        # same question the same way, instead of each repeating a lookup that
        # is easy to get subtly wrong.
        instance = self._db.fetch_one(
            "SELECT sla_instance_id FROM control.sla_instance WHERE feed_id = %s "
            "AND cycle_date = %s",
            (alert.feed_id, alert.cycle_date),
        )
        if instance is None:
            raise ControlTableError(
                f"{alert.feed_id}: no cycle materialised for {alert.cycle_date} — an alert "
                "about a cycle that does not exist is a bug, not a row worth writing"
            )
        self._db.execute(
            "INSERT INTO control.sla_alerts (alert_id, batch_id, sla_instance_id, severity, "
            "summary, citations, dispatched_ts, acknowledged_by, acknowledged_ts, raised_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                alert.alert_id,
                alert.batch_id,
                instance[0],
                alert.severity,
                alert.summary,
                json.dumps(list(alert.citations)),
                alert.dispatched_ts,
                alert.acknowledged_by or None,
                alert.acknowledged_ts,
                alert.raised_ts,
            ),
        )

    def acknowledge_alert(self, alert_id: str, *, by: str, at: datetime) -> None:
        result = self._db.fetch_one(
            "UPDATE control.sla_alerts SET acknowledged_by = %s, acknowledged_ts = %s "
            "WHERE alert_id = %s RETURNING alert_id",
            (by, at, alert_id),
        )
        if result is None:
            raise ControlTableError(f"no such alert: {alert_id!r}")

    # ── reads ────────────────────────────────────────────────────────────────
    def get_batch(self, batch_id: str) -> BatchControl:
        row = self._db.fetch_one(
            "SELECT batch_id, feed_id, feed_version, business_date, state, started_ts, "
            "completed_ts, restart_count, model_version FROM control.batch_control "
            "WHERE batch_id = %s",
            (batch_id,),
        )
        if row is None:
            raise BatchNotFoundError(batch_id)
        return _batch(row)

    def list_batches(self, feed_id: str, limit: int = 50) -> Sequence[BatchControl]:
        rows = self._db.fetch_all(
            "SELECT batch_id, feed_id, feed_version, business_date, state, started_ts, "
            "completed_ts, restart_count, model_version FROM control.batch_control "
            "WHERE feed_id = %s ORDER BY started_ts DESC LIMIT %s",
            (feed_id, limit),
        )
        return tuple(_batch(row) for row in rows)

    def get_stages(self, batch_id: str) -> Sequence[StageStatus]:
        rows = self._db.fetch_all(
            "SELECT batch_id, stage_name, state, started_ts, completed_ts, records_in, "
            "records_out, quarantined, attributed_drops FROM control.batch_stage_status "
            "WHERE batch_id = %s ORDER BY started_ts",
            (batch_id,),
        )
        return tuple(
            StageStatus(
                batch_id=row[0],
                stage=Layer(row[1]),
                state=BatchState(row[2]),
                started_ts=row[3],
                completed_ts=row[4],
                records_in=row[5],
                records_out=row[6],
                quarantined=row[7],
                attributed_drops=row[8],
            )
            for row in rows
        )

    def get_reconciliation(self, batch_id: str) -> Sequence[Reconciliation]:
        rows = self._db.fetch_all(
            "SELECT stage_name, records_in, records_out, quarantined, attributed_drops, "
            "drop_rule_id, drop_reason, drop_count, financial_impact "
            "FROM control.batch_reconciliation WHERE batch_id = %s ORDER BY stage_name",
            (batch_id,),
        )
        by_stage: dict[str, list[Any]] = {}
        for row in rows:
            by_stage.setdefault(row[0], []).append(row)
        return tuple(
            Reconciliation(
                batch_id=batch_id,
                stage=Layer(stage),
                records_in=group[0][1],
                records_out=group[0][2],
                quarantined=group[0][3],
                attributed_drops=group[0][4],
                drop_ledger=tuple(
                    DropLedgerEntry(
                        rule_id=r[5], reason=r[6], record_count=r[7], financial_impact=r[8]
                    )
                    for r in group
                    if r[5] is not None
                ),
            )
            for stage, group in sorted(by_stage.items())
        )

    def record_rule_result(self, result: RuleResult) -> None:
        self._db.execute(
            "INSERT INTO recon.rule_results (result_id, batch_id, feed_id, rule_id, "
            "evaluated, failed, excluded, recorded_ts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                result.batch_id,
                result.feed_id,
                result.rule_id,
                result.evaluated,
                result.failed,
                result.excluded,
                result.recorded_ts,
            ),
        )

    def rule_results(self, batch_id: str) -> Sequence[RuleResult]:
        rows = self._db.fetch_all(
            "SELECT DISTINCT ON (rule_id) batch_id, feed_id, rule_id, evaluated, failed, "
            "excluded, recorded_ts FROM recon.rule_results WHERE batch_id = %s "
            "ORDER BY rule_id, recorded_ts DESC, result_id DESC",
            (batch_id,),
        )
        return tuple(sorted((_rule_result(row) for row in rows), key=lambda r: r.rule_id))

    def rule_result_history(self, feed_id: str, *, limit: int = 200) -> Sequence[RuleResult]:
        rows = self._db.fetch_all(
            "SELECT batch_id, feed_id, rule_id, evaluated, failed, excluded, recorded_ts "
            "FROM recon.rule_results WHERE feed_id = %s "
            "ORDER BY recorded_ts DESC, result_id DESC LIMIT %s",
            (feed_id, limit),
        )
        return tuple(_rule_result(row) for row in rows)

    def get_schema_drift(self, batch_id: str) -> Sequence[SchemaDrift]:
        rows = self._db.fetch_all(
            "SELECT batch_id, feed_id, classification, column_name, detail, "
            "blocked_batch, detected_ts FROM control.schema_drift_log "
            "WHERE batch_id = %s ORDER BY detected_ts",
            (batch_id,),
        )
        return tuple(
            SchemaDrift(
                batch_id=row[0],
                feed_id=row[1],
                classification=row[2],
                column_name=row[3],
                detail=row[4],
                blocked_batch=row[5],
                detected_ts=row[6],
            )
            for row in rows
        )

    def get_crosswalk(
        self, *, source_system: str, source_member_id: str, batch_id: str
    ) -> CrosswalkEntry | None:
        rows = self._db.fetch_all(
            "SELECT source_system, source_member_id, internal_member_id, verato_person_id, "
            "outcome, match_confidence_score, batch_id "
            "FROM identity.bridge_member_source_to_verato "
            "WHERE source_system = %s AND source_member_id = %s AND batch_id = %s",
            (source_system, source_member_id, batch_id),
        )
        if not rows:
            return None
        return _crosswalk_entry(rows[0])

    def list_crosswalk(self, batch_id: str) -> Sequence[CrosswalkEntry]:
        rows = self._db.fetch_all(
            "SELECT source_system, source_member_id, internal_member_id, verato_person_id, "
            "outcome, match_confidence_score, batch_id "
            "FROM identity.bridge_member_source_to_verato "
            "WHERE batch_id = %s ORDER BY source_system, source_member_id",
            (batch_id,),
        )
        return tuple(_crosswalk_entry(row) for row in rows)

    # ── coverage and parity telemetry · CF-V3-E9-04 ───────────────────────────
    def record_coverage_snapshot(self, snapshot: CoverageSnapshot) -> None:
        self._db.execute(
            "INSERT INTO identity.identity_coverage_snapshot (source_system, business_date, "
            "total, with_link_id, with_our_id, with_both, computed_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (source_system, business_date) DO UPDATE SET "
            "total = EXCLUDED.total, with_link_id = EXCLUDED.with_link_id, "
            "with_our_id = EXCLUDED.with_our_id, with_both = EXCLUDED.with_both, "
            "computed_ts = now()",
            (
                snapshot.source_system,
                snapshot.business_date,
                snapshot.total,
                snapshot.with_link_id,
                snapshot.with_our_id,
                snapshot.with_both,
            ),
        )

    def coverage_history(self, source_system: str, *, days: int = 90) -> Sequence[CoverageSnapshot]:
        rows = self._db.fetch_all(
            "SELECT source_system, business_date, total, with_link_id, with_our_id, with_both "
            "FROM identity.identity_coverage_snapshot WHERE source_system = %s "
            "ORDER BY business_date DESC LIMIT %s",
            (source_system, days),
        )
        return tuple(_coverage_snapshot(row) for row in rows)

    def record_parity_check(self, summary: ParityCheckSummary) -> None:
        self._db.execute(
            "INSERT INTO identity.identity_parity_check (source_system, business_date, "
            "checked, matched, mismatched, computed_ts) VALUES (%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (source_system, business_date) DO UPDATE SET "
            "checked = EXCLUDED.checked, matched = EXCLUDED.matched, "
            "mismatched = EXCLUDED.mismatched, computed_ts = now()",
            (
                summary.source_system,
                summary.business_date,
                summary.checked,
                summary.matched,
                summary.mismatched,
            ),
        )

    def parity_check_history(
        self, source_system: str, *, days: int = 90
    ) -> Sequence[ParityCheckSummary]:
        rows = self._db.fetch_all(
            "SELECT source_system, business_date, checked, matched, mismatched "
            "FROM identity.identity_parity_check WHERE source_system = %s "
            "ORDER BY business_date DESC LIMIT %s",
            (source_system, days),
        )
        return tuple(_parity_check_summary(row) for row in rows)

    def get_quarantine_summary(self, batch_id: str) -> Sequence[QuarantineSummary]:
        rows = self._db.fetch_all(
            "SELECT stage_name, rule_id, reason, column_names, record_count "
            "FROM control.quarantine_records WHERE batch_id = %s ORDER BY rule_id",
            (batch_id,),
        )
        return tuple(
            QuarantineSummary(
                batch_id=batch_id,
                stage=Layer(row[0]),
                rule_id=row[1],
                reason=row[2],
                column_names=tuple(row[3]),
                record_count=row[4],
            )
            for row in rows
        )

    def list_errors(
        self, batch_id: str, category: ErrorCategory | None = None
    ) -> Sequence[ErrorRecord]:
        statement = (
            "SELECT error_id_hash, batch_id, stage_name, error_category, message, "
            "record_key, rule_id, occurred_ts FROM control.error_log WHERE batch_id = %s"
        )
        parameters: tuple[Any, ...] = (batch_id,)
        if category is not None:
            statement += " AND error_category = %s"
            parameters += (category.value,)
        rows = self._db.fetch_all(statement + " ORDER BY occurred_ts", parameters)
        return tuple(_error(row) for row in rows)

    def find_error_by_hash(self, error_id_hash: str) -> ErrorRecord | None:
        row = self._db.fetch_one(
            "SELECT error_id_hash, batch_id, stage_name, error_category, message, "
            "record_key, rule_id, occurred_ts FROM control.error_log WHERE error_id_hash = %s",
            (error_id_hash,),
        )
        return _error(row) if row else None

    def find_input_by_fingerprint(self, fingerprint: str) -> InputFile | None:
        row = self._db.fetch_one(
            "SELECT batch_id, feed_id, file_key, filename, size_bytes, fingerprint, state, "
            "arrived_ts, rejection_reason, record_count FROM control.input_registry "
            "WHERE fingerprint = %s",
            (fingerprint,),
        )
        return _input(row) if row else None

    def get_input_registry(self, feed_id: str, limit: int = 50) -> Sequence[InputFile]:
        rows = self._db.fetch_all(
            "SELECT batch_id, feed_id, file_key, filename, size_bytes, fingerprint, state, "
            "arrived_ts, rejection_reason, record_count FROM control.input_registry "
            "WHERE feed_id = %s ORDER BY arrived_ts DESC LIMIT %s",
            (feed_id, limit),
        )
        return tuple(_input(row) for row in rows)

    def list_batch_inputs(self, batch_id: str) -> Sequence[InputFile]:
        rows = self._db.fetch_all(
            "SELECT batch_id, feed_id, file_key, filename, size_bytes, fingerprint, state, "
            "arrived_ts, rejection_reason, record_count FROM control.input_registry "
            "WHERE batch_id = %s ORDER BY arrived_ts",
            (batch_id,),
        )
        return tuple(_input(row) for row in rows)

    def feed_sla_configs(self, *, feed_ids: Sequence[str] = ()) -> Sequence[FeedSlaConfig]:
        # The filter is always PRESENT in the SQL and always a placeholder —
        # never a clause assembled from a string, which is how a query stops
        # being reviewable as one literal. An empty `feed_ids` matches every
        # row via `cardinality(...) = 0`, not via a fragment that toggles.
        rows = self._db.fetch_all(
            "SELECT feed_id, feed_version, domain, source_system, file_format, landing_path, "
            "file_pattern, schedule_cron, expected_file_count, min_size_bytes, max_size_bytes, "
            "grace_period_minutes, created_ts FROM control.feed_sla_config "
            "WHERE cardinality(%s::text[]) = 0 OR feed_id = ANY(%s::text[]) "
            "ORDER BY feed_id, feed_version",
            (list(feed_ids), list(feed_ids)),
        )
        return tuple(_sla_config(row) for row in rows)

    def sla_instances(
        self, *, cycle_date: date, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaCycle]:
        rows = self._db.fetch_all(
            "SELECT sla_instance_id, feed_id, batch_id, cycle_date, expected_ts, actual_ts, "
            "sla_status FROM control.sla_instance "
            "WHERE cycle_date = %s AND (cardinality(%s::text[]) = 0 OR feed_id = ANY(%s::text[])) "
            "ORDER BY feed_id",
            (cycle_date, list(feed_ids), list(feed_ids)),
        )
        return tuple(_sla_cycle(row) for row in rows)

    def sla_history(self, feed_id: str, *, days: int = 90) -> Sequence[SlaCycle]:
        rows = self._db.fetch_all(
            "SELECT sla_instance_id, feed_id, batch_id, cycle_date, expected_ts, actual_ts, "
            "sla_status FROM control.sla_instance WHERE feed_id = %s "
            "ORDER BY cycle_date DESC LIMIT %s",
            (feed_id, days),
        )
        return tuple(_sla_cycle(row) for row in rows)

    def sla_alerts(
        self, *, cycle_date: date | None = None, feed_ids: Sequence[str] = ()
    ) -> Sequence[SlaAlert]:
        # `sla_alerts` carries no `feed_id` or `cycle_date` of its own — it is
        # reached through the cycle it was raised for, which is why this is a
        # join rather than two denormalised columns that could disagree with
        # the instance they describe.
        rows = self._db.fetch_all(
            "SELECT a.alert_id, i.feed_id, i.cycle_date, a.batch_id, a.severity, "
            "a.summary, a.citations, a.dispatched_ts, a.acknowledged_by, a.acknowledged_ts, "
            "a.raised_ts FROM control.sla_alerts a "
            "JOIN control.sla_instance i ON i.sla_instance_id = a.sla_instance_id "
            "WHERE (%s::date IS NULL OR i.cycle_date = %s) "
            "AND (cardinality(%s::text[]) = 0 OR i.feed_id = ANY(%s::text[])) "
            "ORDER BY a.raised_ts",
            (cycle_date, cycle_date, list(feed_ids), list(feed_ids)),
        )
        return tuple(_sla_alert(row) for row in rows)


def _batch(row: tuple[Any, ...]) -> BatchControl:
    return BatchControl(
        batch_id=row[0],
        feed_id=row[1],
        feed_version=row[2],
        business_date=str(row[3]),
        state=BatchState(row[4]),
        started_ts=row[5],
        completed_ts=row[6],
        restart_count=row[7],
        model_version=row[8],
    )


def _input(row: tuple[Any, ...]) -> InputFile:
    return InputFile(
        batch_id=row[0],
        feed_id=row[1],
        key=row[2],
        filename=row[3],
        size_bytes=row[4],
        fingerprint=row[5],
        state=FileState(row[6]),
        arrived_ts=row[7],
        rejection_reason=row[8],
        record_count=row[9],
    )


def _error(row: tuple[Any, ...]) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=row[0],
        batch_id=row[1],
        stage=Layer(row[2]),
        category=ErrorCategory(row[3]),
        message=row[4],
        record_key=row[5],
        rule_id=row[6],
        occurred_ts=row[7],
    )


def _sla_config(row: tuple[Any, ...]) -> FeedSlaConfig:
    return FeedSlaConfig(
        feed_id=row[0],
        feed_version=row[1],
        domain=row[2],
        source_system=row[3],
        file_format=row[4],
        landing_path=row[5],
        file_pattern=row[6],
        schedule_cron=row[7],
        expected_file_count=row[8],
        min_size_bytes=row[9],
        max_size_bytes=row[10],
        grace_period_minutes=row[11],
        created_ts=row[12],
    )


def _sla_cycle(row: tuple[Any, ...]) -> SlaCycle:
    return SlaCycle(
        sla_instance_id=str(row[0]),
        feed_id=row[1],
        batch_id=row[2],
        cycle_date=row[3],
        expected_ts=row[4],
        actual_ts=row[5],
        sla_status=row[6],
    )


def _sla_alert(row: tuple[Any, ...]) -> SlaAlert:
    return SlaAlert(
        alert_id=str(row[0]),
        feed_id=row[1],
        cycle_date=row[2],
        batch_id=row[3],
        severity=row[4],
        summary=row[5],
        citations=tuple(row[6]) if row[6] else (),
        dispatched_ts=row[7],
        acknowledged_by=row[8] or "",
        acknowledged_ts=row[9],
        raised_ts=row[10],
    )


def _coverage_snapshot(row: tuple[Any, ...], /) -> CoverageSnapshot:
    return CoverageSnapshot(
        source_system=row[0],
        business_date=str(row[1]),
        total=row[2],
        with_link_id=row[3],
        with_our_id=row[4],
        with_both=row[5],
    )


def _parity_check_summary(row: tuple[Any, ...], /) -> ParityCheckSummary:
    return ParityCheckSummary(
        source_system=row[0],
        business_date=str(row[1]),
        checked=row[2],
        matched=row[3],
        mismatched=row[4],
    )


def _crosswalk_entry(row: tuple[Any, ...], /) -> CrosswalkEntry:
    return CrosswalkEntry(
        source_system=row[0],
        source_member_id=row[1],
        internal_member_id=row[2],
        verato_person_id=row[3],
        outcome=MatchOutcome(row[4]),
        match_confidence_score=row[5],
        batch_id=row[6],
    )


def _rule_result(row: tuple[Any, ...], /) -> RuleResult:
    return RuleResult(
        batch_id=row[0],
        feed_id=row[1],
        rule_id=row[2],
        evaluated=row[3],
        failed=row[4],
        excluded=row[5],
        recorded_ts=row[6],
    )
