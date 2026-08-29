"""memstore — the eleven control tables in memory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from cinqflow.core.model.vocabulary import BatchState, ErrorCategory
from cinqflow.ports import port
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    SchemaDrift,
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

    # ── writes ───────────────────────────────────────────────────────────────
    def open_batch(self, batch: BatchControl) -> None:
        self._batches[batch.batch_id] = batch
        self._stages.setdefault(batch.batch_id, [])

    def update_batch_state(self, batch_id: str, state: BatchState) -> None:
        batch = self.get_batch(batch_id)
        self._batches[batch_id] = replace(batch, state=state)

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
