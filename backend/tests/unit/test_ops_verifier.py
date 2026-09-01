"""CF-V2-E12-03 — the verifier's three honest outcomes.

'retry requested' is not 'retry succeeded', and the phase only moves when
something re-read the control tables. The third outcome is the one that keeps
the other two honest: NOT YET is neither success nor failure, and a verifier
without it would either race the engine or rubber-stamp it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState
from cinqflow.core.operations.actions import (
    ActionPhase,
    ActionRequest,
    OpsAction,
    request_action,
)
from cinqflow.core.registry.suspension import pause
from cinqflow.ports.control_tables import BatchControl
from cinqflow.ports.metadata_db import ActionRecordRow
from cinqflow.workers.ops import OpsVerifier

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
OPERATOR = Actor(subject="ops@cinqcare.test", actor_type=ActorType.HUMAN)
FEED = "centene-medicare-enrollment"
BATCH = "B-1244"


def _plane(state: BatchState) -> tuple[MemStoreControlTables, MemMetadataDb, OpsVerifier]:
    control = MemStoreControlTables()
    control.open_batch(
        BatchControl(
            batch_id=BATCH,
            feed_id=FEED,
            feed_version=1,
            business_date="2026-08-30",
            state=BatchState.RECEIVED,
            started_ts=NOW - timedelta(hours=3),
        )
    )
    control.update_batch_state(BATCH, state)
    metadata = MemMetadataDb()
    return control, metadata, OpsVerifier(control=control, metadata=metadata)


def _requested(
    metadata: MemMetadataDb, action: OpsAction, *, at: datetime = NOW
) -> ActionRecordRow:
    record = request_action(
        ActionRequest(action=action, target=BATCH, actor=OPERATOR, reason="Because the demo."),
        now=at,
    )
    return metadata.record_action_event(
        ActionRecordRow(record_id=f"act-{action.value}", feed_id=FEED, record=record)
    )


def test_success_observed_becomes_verified_with_the_observation_in_words() -> None:
    _control, metadata, verifier = _plane(BatchState.COMPLETED)
    _requested(metadata, OpsAction.RETRY)

    row = verifier.verify_record("act-retry", now=NOW + timedelta(minutes=18))
    assert row.record.phase is ActionPhase.VERIFIED
    assert row.record.observed_state is BatchState.COMPLETED
    assert "control tables say" in row.record.outcome
    # And the ledger holds BOTH phases — the move was a second row.
    assert metadata.get_action_record("act-retry").record.is_complete is True


def test_not_yet_is_neither_success_nor_failure() -> None:
    """The engine has not run. Failing here punishes the operator for a slow
    queue; verifying here is the green tick. The record WAITS."""
    _, metadata, verifier = _plane(BatchState.FAILED)
    _requested(metadata, OpsAction.RETRY)

    row = verifier.verify_record("act-retry", now=NOW + timedelta(minutes=5))
    assert row.record.phase is ActionPhase.REQUESTED


def test_a_stale_request_fails_with_what_was_observed_instead() -> None:
    """A record that stops at REQUESTED forever is one nobody checked — after
    the grace window the verifier says so, in words."""
    _, metadata, verifier = _plane(BatchState.FAILED)
    _requested(metadata, OpsAction.RETRY)

    row = verifier.verify_record("act-retry", now=NOW + timedelta(hours=3))
    assert row.record.phase is ActionPhase.FAILED
    assert "no success observed" in row.record.outcome
    assert "failed" in row.record.outcome.lower()


def test_a_pause_verifies_against_the_suspension_ledger_not_the_batch() -> None:
    """ "a pause expects the FEED paused and the batch state is irrelevant" —
    one hard-coded success state would make this action lie."""
    _, metadata, verifier = _plane(BatchState.IN_PROGRESS)
    _requested(metadata, OpsAction.PAUSE)
    metadata.record_suspension(
        pause(FEED, reason="mapping change pending", actor=OPERATOR, now=NOW)
    )

    row = verifier.verify_record("act-pause", now=NOW + timedelta(minutes=1))
    assert row.record.phase is ActionPhase.VERIFIED
    assert "suspension ledger" in row.record.outcome


def test_bookkeeping_is_complete_when_it_is_written_down() -> None:
    _, metadata, verifier = _plane(BatchState.FAILED)
    _requested(metadata, OpsAction.ACKNOWLEDGE)

    row = verifier.verify_record("act-acknowledge", now=NOW)
    assert row.record.phase is ActionPhase.VERIFIED
    assert "written down" in row.record.outcome


def test_verification_is_idempotent_a_moved_record_is_returned_as_it_stands() -> None:
    _, metadata, verifier = _plane(BatchState.COMPLETED)
    _requested(metadata, OpsAction.RETRY)
    first = verifier.verify_record("act-retry", now=NOW + timedelta(minutes=1))
    second = verifier.verify_record("act-retry", now=NOW + timedelta(minutes=2))
    assert first.record.phase is ActionPhase.VERIFIED
    assert second.record == first.record


def test_the_sweep_moves_only_what_can_honestly_move() -> None:
    control, metadata, verifier = _plane(BatchState.COMPLETED)
    _requested(metadata, OpsAction.RETRY)  # success observable
    _requested(metadata, OpsAction.REPROCESS_BATCH, at=NOW)  # also COMPLETED — moves

    control.open_batch(
        BatchControl(
            batch_id="B-9999",
            feed_id=FEED,
            feed_version=1,
            business_date="2026-08-30",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    control.update_batch_state("B-9999", BatchState.FAILED)
    stuck = request_action(
        ActionRequest(action=OpsAction.RETRY, target="B-9999", actor=OPERATOR, reason="x"),
        now=NOW,
    )
    metadata.record_action_event(ActionRecordRow("act-stuck", FEED, stuck))

    moved = verifier.sweep(now=NOW + timedelta(minutes=10))
    assert {row.record_id for row in moved} == {"act-retry", "act-reprocess_batch"}
    assert metadata.get_action_record("act-stuck").record.phase is ActionPhase.REQUESTED
