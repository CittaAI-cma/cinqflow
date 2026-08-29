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
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, FileState, Layer
from cinqflow.ports import port
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    DropLedgerEntry,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
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
            "rule_id, reason, column_names, quarantined_ts) VALUES (%s,%s,%s,%s,%s,%s, now())",
            (
                str(uuid.uuid4()),
                summary.batch_id,
                summary.stage.value,
                summary.rule_id,
                summary.reason,
                json.dumps(list(summary.column_names)),
            ),
        )
        # The summary carries a count; the control table stores one row per
        # (rule, batch). The count lives in the recon ledger, which is the one
        # place totals are reconciled.

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

    def get_quarantine_summary(self, batch_id: str) -> Sequence[QuarantineSummary]:
        rows = self._db.fetch_all(
            "SELECT q.stage_name, q.rule_id, q.reason, q.column_names, "
            "COALESCE(r.drop_count, 0) FROM control.quarantine_records q "
            "LEFT JOIN control.batch_reconciliation r "
            "  ON r.batch_id = q.batch_id AND r.drop_rule_id = q.rule_id "
            "WHERE q.batch_id = %s ORDER BY q.rule_id",
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
        return tuple(
            ErrorRecord(
                error_id_hash=row[0],
                batch_id=row[1],
                stage=Layer(row[2]),
                category=ErrorCategory(row[3]),
                message=row[4],
                record_key=row[5],
                rule_id=row[6],
                occurred_ts=row[7],
            )
            for row in rows
        )

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
