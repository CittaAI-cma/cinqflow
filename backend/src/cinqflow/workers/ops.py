"""CF-V2-E12-03 — the verifier: the worker that turns REQUESTED into a fact.

    "The response is REQUESTED. The UI polls until the phase moved — and the
     phase only moves when something RE-READ the control tables."

ONE PLAIN SYNCHRONOUS CLASS, like `SlaWorker` and `IncidentWorker`. `sweep`
is callable directly — after an engine run, from a test, from a CLI command —
and separately reachable through `workers.consumer.Consumer`. Which route
reached it is not this worker's concern.

THREE HONEST OUTCOMES PER RECORD, and "verified because we felt like it" is
not one of them:

  · SUCCESS OBSERVED — the batch reached what `EXPECTED_STATES` says success
    looks like for this action (a pause: the suspension ledger says paused) →
    VERIFIED, with the observation in words.
  · NOT YET — the engine simply has not run. The record STAYS REQUESTED.
    Failing it here would punish the operator for the queue being slow, and
    verifying it would be the green tick this surface exists to refuse.
  · STALE — nothing succeeded within the grace window → FAILED, saying what
    was observed instead. A record that stops at REQUESTED forever is one
    nobody checked, and the screen must be able to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cinqflow.core.model.vocabulary import BatchState
from cinqflow.core.operations.actions import (
    EXPECTED_STATES,
    ActionPhase,
    ActionRecord,
    OpsAction,
    fail,
    verify,
)
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.metadata_db import ActionRecordRow, MetadataDbPort

#: How long a pipeline action may sit REQUESTED before the verifier calls it
#: FAILED. Generous on purpose: a reprocess of 22 million rows is slow, and a
#: verifier that raced it would teach operators that FAILED means "wait".
DEFAULT_GRACE = timedelta(hours=2)


class OpsVerifier:
    def __init__(self, *, control: ControlTablesPort, metadata: MetadataDbPort) -> None:
        self._control = control
        self._metadata = metadata

    def sweep(
        self,
        *,
        batch_id: str | None = None,
        now: datetime | None = None,
        grace: timedelta = DEFAULT_GRACE,
    ) -> tuple[ActionRecordRow, ...]:
        """Verify every REQUESTED record — for one batch after its engine run,
        or across the ledger on a tick. Returns the rows that MOVED."""
        stamp = now or datetime.now(UTC)
        moved: list[ActionRecordRow] = []
        for row in self._metadata.list_action_records(batch_id=batch_id, limit=200):
            if row.record.phase is not ActionPhase.REQUESTED:
                continue
            outcome = self._verify_one(row, now=stamp, grace=grace)
            if outcome is not None:
                moved.append(outcome)
        return tuple(moved)

    def verify_record(
        self,
        record_id: str,
        *,
        now: datetime | None = None,
        grace: timedelta = DEFAULT_GRACE,
    ) -> ActionRecordRow:
        """One record, same three outcomes. Idempotent: a record already past
        REQUESTED is returned as it stands — the ledger is append-only and a
        second verification would be a second opinion nobody asked for."""
        stamp = now or datetime.now(UTC)
        row = self._metadata.get_action_record(record_id)
        if row.record.phase is not ActionPhase.REQUESTED:
            return row
        return self._verify_one(row, now=stamp, grace=grace) or row

    def _verify_one(
        self, row: ActionRecordRow, *, now: datetime, grace: timedelta
    ) -> ActionRecordRow | None:
        record = row.record
        observed, succeeded, sentence = self._observe(row, record)

        if succeeded:
            moved = verify(
                record,
                observed_state=observed,
                expected=EXPECTED_STATES[record.action],
                outcome=sentence,
                now=now,
            )
        elif now - record.requested_ts > grace:
            moved = fail(
                record,
                outcome=(
                    f"no success observed within {int(grace.total_seconds() // 60)} minutes — "
                    f"{sentence}"
                ),
                now=now,
            )
        else:
            return None  # NOT YET — the honest third outcome.
        return self._metadata.record_action_event(
            ActionRecordRow(record_id=row.record_id, feed_id=row.feed_id, record=moved)
        )

    def _observe(
        self, row: ActionRecordRow, record: ActionRecord
    ) -> tuple[BatchState | None, bool, str]:
        """What the platform read back, whether it counts as success for THIS
        action, and the sentence a person reads six weeks later."""
        if record.action in {OpsAction.PAUSE, OpsAction.RESUME}:
            suspension = self._metadata.current_suspension(row.feed_id)
            paused = suspension.is_active_at(datetime.now(UTC))
            wanted = record.action is OpsAction.PAUSE
            batch = self._control.get_batch(record.target)
            word = "paused" if paused else "not paused"
            return (
                batch.state,
                paused is wanted,
                f"the suspension ledger says {row.feed_id} is {word}",
            )
        batch = self._control.get_batch(record.target)
        if not record.action.mutates_production or record.action in {
            OpsAction.ACKNOWLEDGE,
            OpsAction.ASSIGN,
            OpsAction.NOTE,
        }:
            return (
                batch.state,
                True,
                f"recorded — {record.action.value} is complete when it is written down",
            )
        succeeded = batch.state in EXPECTED_STATES[record.action]
        return (
            batch.state,
            succeeded,
            f"the control tables say {record.target} is {batch.state.value}",
        )


def register(consumer: object, verifier: OpsVerifier) -> None:
    """Wire the verifier to the queue: one topic, one handler.

    `payload={"record_id": ...}` verifies one record; an empty payload is a
    tick and sweeps. The handler raises nothing on NOT-YET — the record stays
    REQUESTED and the next tick looks again.
    """

    def _handle(payload: dict[str, object]) -> None:
        record_id = payload.get("record_id")
        if record_id:
            verifier.verify_record(str(record_id))
        else:
            verifier.sweep()

    consumer.register("ops.verify", _handle)  # type: ignore[attr-defined]
