"""CF-V2-E12-04 — the incident ledger: decisions stored, evidence recomputed.

The evidence half (cascade, signature, match) derives from control.error_log
on every read; what the ledger holds is what PEOPLE did. These tests prove the
two halves fold together losslessly, and that the worker writing the opening
event is idempotent — a batch that fails twice is one incident continuing,
never two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, Layer
from cinqflow.core.operations.fingerprint import (
    FingerprintError,
    IncidentState,
    event_for,
    fingerprint_batch,
    hydrate,
    signature,
)
from cinqflow.ports.control_tables import BatchControl, ErrorRecord
from cinqflow.workers.incidents import PLATFORM_SUBJECT, IncidentWorker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 3, 14, tzinfo=UTC)
MESSAGE = (
    "evaluate_bronze_load: required key 'business_date' absent in XCom from upstream validate_input"
)
SEED = Actor(subject="seed@cinqcare.test", actor_type=ActorType.HUMAN)


def _errors(batch_id: str) -> tuple[ErrorRecord, ...]:
    return (
        ErrorRecord(
            error_id_hash=f"{batch_id}-root",
            batch_id=batch_id,
            stage=Layer.BRONZE,
            category=ErrorCategory.SYSTEM,
            message=MESSAGE,
            occurred_ts=NOW,
        ),
        ErrorRecord(
            error_id_hash=f"{batch_id}-fallout",
            batch_id=batch_id,
            stage=Layer.SILVER_RAW,
            category=ErrorCategory.SYSTEM,
            message="load_silver_raw skipped: upstream failed",
            occurred_ts=NOW + timedelta(seconds=2),
        ),
    )


def _incident(batch_id: str = "B-1244"):
    return fingerprint_batch(
        batch_id=batch_id, feed_id="centene-medicare-enrollment", errors=_errors(batch_id), now=NOW
    )


# ── event_for / hydrate ───────────────────────────────────────────────────────
def test_the_decisions_survive_the_round_trip_and_the_evidence_never_travels() -> None:
    incident = _incident().acknowledge(by="ops@cinqcare.test", assigned_to="mei@cinqcare.test")
    event = event_for(incident, actor_subject="ops@cinqcare.test", occurred_ts=NOW)

    recomputed = _incident()
    assert recomputed.state is IncidentState.OPEN  # the evidence knows nothing of people
    folded = hydrate(recomputed, event)
    assert folded.state is IncidentState.ACKNOWLEDGED
    assert folded.acknowledged_by == "ops@cinqcare.test"
    assert folded.assigned_to == "mei@cinqcare.test"
    # The computed half is KEPT from the fresh computation, not the event.
    assert folded.cascade == recomputed.cascade
    assert folded.signature == recomputed.signature


def test_hydrating_with_no_event_is_the_computed_incident_exactly() -> None:
    """An incident nobody touched is OPEN — true, and computed."""
    incident = _incident()
    assert hydrate(incident, None) == incident


def test_one_incidents_decisions_cannot_dress_anothers_evidence() -> None:
    other = _incident("B-9999")
    event = event_for(other, actor_subject=PLATFORM_SUBJECT, occurred_ts=NOW)
    with pytest.raises(FingerprintError, match="fabricate history"):
        hydrate(_incident("B-1244"), event)


# ── the worker ────────────────────────────────────────────────────────────────
def _plane(batch_id: str = "B-1244") -> tuple[MemStoreControlTables, MemMetadataDb]:
    control = MemStoreControlTables()
    control.open_batch(
        BatchControl(
            batch_id=batch_id,
            feed_id="centene-medicare-enrollment",
            feed_version=1,
            business_date="2026-08-30",
            state=BatchState.RECEIVED,
            started_ts=NOW - timedelta(hours=2),
        )
    )
    control.update_batch_state(batch_id, BatchState.FAILED)
    for error in _errors(batch_id):
        control.record_error(error)
    return control, MemMetadataDb()


def test_a_failed_batch_opens_exactly_one_incident_however_often_it_fails() -> None:
    """IDEMPOTENT ON THE BATCH — a retry that fails again is the same incident
    continuing, and the pipeline may call the hook on every failure."""
    control, metadata = _plane()
    worker = IncidentWorker(control=control, metadata=metadata)

    first = worker.on_batch_failed("B-1244", now=NOW)
    again = worker.on_batch_failed("B-1244", now=NOW + timedelta(minutes=10))

    assert first.incident_id == again.incident_id
    events = metadata.list_incident_events(batch_id="B-1244")
    assert len(events) == 1
    assert events[0].state is IncidentState.OPEN
    assert events[0].actor_subject == PLATFORM_SUBJECT


def test_a_rerun_folds_the_decisions_people_made_meanwhile() -> None:
    control, metadata = _plane()
    worker = IncidentWorker(control=control, metadata=metadata)
    opened = worker.on_batch_failed("B-1244", now=NOW)

    acknowledged = opened.acknowledge(by="ops@cinqcare.test")
    metadata.record_incident_event(
        event_for(
            acknowledged,
            actor_subject="ops@cinqcare.test",
            occurred_ts=NOW + timedelta(minutes=1),
        )
    )

    seen = worker.on_batch_failed("B-1244", now=NOW + timedelta(minutes=5))
    assert seen.state is IncidentState.ACKNOWLEDGED
    assert seen.acknowledged_by == "ops@cinqcare.test"


def test_the_worker_matches_published_guides_and_ignores_drafts() -> None:
    """The 3 AM rule: a draft guide is one person's account of what worked
    once, and it is never offered as the known fix."""
    control, metadata = _plane()
    found = signature(stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=MESSAGE)

    def _guide(guide_id: str, state: LifecycleState) -> GovernedObject:
        return GovernedObject(
            object_type=ObjectType.RUNBOOK,
            object_id=guide_id,
            version=1,
            lifecycle_state=state,
            created_by=SEED,
            created_ts=NOW,
            body={"title": guide_id, "signatures": [found], "steps": ["Retry the batch."]},
            approved_by=SEED if state is LifecycleState.PUBLISHED else None,
            approved_ts=NOW if state is LifecycleState.PUBLISHED else None,
        )

    metadata.save(_guide("BH-AF-002", LifecycleState.PUBLISHED))
    metadata.save(_guide("BH-DRAFT-9", LifecycleState.DRAFT))

    worker = IncidentWorker(control=control, metadata=metadata)
    incident = worker.on_batch_failed("B-1244", now=NOW)
    assert incident.match is not None
    assert incident.match.guide.guide_id == "BH-AF-002"
