"""CF-V1-E11-01 on the REAL rung-0.5 plane — Postgres, not a stand-in.

The mock proves the semantics; this proves they survive contact with the
database that actually stores them. Two things only a real plane can show:

  • `record_transition` updates state and approver columns and appends the
    audit row through ONE connection — so a caller inside `pg_control.commit`
    gets both writes or neither;
  • the body column is untouched by a transition, which is what keeps
    "promoted configuration is byte-identical to what was approved" true when
    the storage is a row rather than a dict.

Every write here rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core import lifecycle
from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    ObjectType,
    SelfApprovalError,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.ports.metadata_db import ObjectNotFoundError

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
STEWARD = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Daniel"
)
STEWARD_ROLES = frozenset({Role.DATA_STEWARD})


@pytest.fixture
def store(plane: object) -> PostgresMetadataDb:
    return PostgresMetadataDb(plane)  # type: ignore[arg-type]


def _mapping(object_id: str = "fidelis-roster-mapping") -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.MAPPING,
        object_id=object_id,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=NOW,
        body={"lines": 45, "unmapped": ["member_name"]},
    )


def test_a_mapping_travels_the_whole_lifecycle_on_postgres(store: PostgresMetadataDb) -> None:
    """Draft -> In Review -> Approved -> Published, each state read back from
    the database rather than from the object in hand."""
    store.save(_mapping())

    submitted, entry = lifecycle.submit(_mapping(), actor=BA, comment="ready")
    store.record_transition(submitted, entry)
    assert (
        store.get(ObjectType.MAPPING, "fidelis-roster-mapping").lifecycle_state
        is LifecycleState.PENDING_REVIEW
    )

    approved, entry = lifecycle.approve(submitted, actor=STEWARD, roles=STEWARD_ROLES)
    store.record_transition(approved, entry)
    stored = store.get(ObjectType.MAPPING, "fidelis-roster-mapping")
    assert stored.approved_by is not None
    assert stored.approved_by.subject == STEWARD.subject
    assert stored.approved_ts is not None

    published, entry = lifecycle.publish(approved, actor=STEWARD, roles=STEWARD_ROLES)
    store.record_transition(published, entry)
    assert store.get(ObjectType.MAPPING, "fidelis-roster-mapping").is_executable is True


def test_a_transition_never_edits_the_body(store: PostgresMetadataDb) -> None:
    """The UPDATE deliberately omits the body column. An amendment is a new
    VERSION; a transition that could carry one would let an edit skip review."""
    store.save(_mapping())
    submitted, entry = lifecycle.submit(_mapping(), actor=BA)
    tampered = type(submitted)(
        object_type=submitted.object_type,
        object_id=submitted.object_id,
        version=submitted.version,
        lifecycle_state=submitted.lifecycle_state,
        created_by=submitted.created_by,
        created_ts=submitted.created_ts,
        body={"lines": 0, "unmapped": []},
    )
    store.record_transition(tampered, entry)
    assert store.get(ObjectType.MAPPING, "fidelis-roster-mapping").body["lines"] == 45


def test_a_transition_of_an_unsaved_version_is_refused(store: PostgresMetadataDb) -> None:
    """A state change to a phantom row is a lost approval — refused, not
    silently inserted."""
    submitted, entry = lifecycle.submit(_mapping("never-saved"), actor=BA)
    with pytest.raises(ObjectNotFoundError):
        store.record_transition(submitted, entry)


def test_every_transition_leaves_its_audit_row_on_the_real_ledger(
    store: PostgresMetadataDb,
) -> None:
    """Append-only, and the actor type is recorded rather than inferred."""
    store.save(_mapping())
    submitted, entry = lifecycle.submit(_mapping(), actor=BA, comment="ready")
    store.record_transition(submitted, entry)
    approved, entry = lifecycle.approve(
        submitted, actor=STEWARD, roles=STEWARD_ROLES, comment="precedents check out"
    )
    store.record_transition(approved, entry)

    trail = store.read_audit(object_id="fidelis-roster-mapping")
    assert {e.action for e in trail} == {
        "transition:pending_review",
        "transition:approved",
    }
    assert {e.actor_type for e in trail} == {ActorType.HUMAN}
    assert "precedents check out" in {e.detail for e in trail}


def test_the_author_cannot_approve_their_own_mapping_on_the_real_plane(
    store: PostgresMetadataDb,
) -> None:
    """The universal negative, with a database underneath it — and nothing is
    written, because the refusal happens before any call to the store."""
    store.save(_mapping())
    submitted, entry = lifecycle.submit(_mapping(), actor=BA)
    store.record_transition(submitted, entry)
    with pytest.raises(SelfApprovalError):
        lifecycle.approve(submitted, actor=BA, roles=STEWARD_ROLES)
    assert (
        store.get(ObjectType.MAPPING, "fidelis-roster-mapping").lifecycle_state
        is LifecycleState.PENDING_REVIEW
    )
