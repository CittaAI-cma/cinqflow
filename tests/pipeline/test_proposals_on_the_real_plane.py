"""CF-V1-E5-02's proposal, on the REAL rung-0.5 plane.

The mock proves the semantics; this proves they survive the row that actually
stores them. Two things only Postgres can show:

  • the CHECK constraint refuses an R4 risk class, so "R4 is never automated"
    holds even against a caller that bypassed `Proposal.__post_init__`;
  • `payload` survives every decision, because the UPDATE concatenates only
    the corrections back onto the stored document.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import (
    Correction,
    Proposal,
    ProposalState,
    apply,
    approve,
    reject,
    submit,
)

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
AGENT = Actor(subject="schema-inference", actor_type=ActorType.AI, display_name="Schema inference")
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
FEED = "fidelis-downstate-roster"

PAYLOAD = {
    "key": "source_name",
    "records": [
        {"source_name": "MemberID", "name": "source_member_id", "type": "string", "is_phi": True},
        {"source_name": "DOB", "name": "member_date_of_birth", "type": "date", "is_phi": True},
    ],
    "refusals": [],
    "needs_input": [],
}


def _proposal(proposal_id: str = "11111111-1111-4111-8111-111111111111") -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        agent="schema-inference",
        capability="propose_schema_contract",
        risk_class=RiskClass.R2,
        run_id="run-1",
        feed_id=FEED,
        payload=PAYLOAD,
        created_by=AGENT,
        created_ts=NOW,
        confidence=0.9,
        grounding_citations=(
            CitationId(kind=CitationKind.PROFILE, subject="sha256-aaa"),
            CitationId(kind=CitationKind.TERM, subject="member-date-of-birth"),
        ),
        prompt_hash="abc123",
    )


def test_a_proposal_round_trips_through_the_row(plane: object) -> None:
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    written = store.record_proposal(submit(_proposal(), now=NOW))

    assert written.state is ProposalState.PENDING_REVIEW
    assert written.payload == PAYLOAD
    assert written.created_by.actor_type is ActorType.AI
    assert [str(c) for c in written.grounding_citations] == [
        "profile:sha256-aaa",
        "term:member-date-of-birth",
    ]
    assert store.get_proposal(written.proposal_id) == written


def test_the_payload_survives_a_decision_untouched(plane: object) -> None:
    """The UPDATE leaves `payload` alone and concatenates only the corrections.

    Same reason `record_transition` leaves `body` out of its UPDATE: the
    correction set is measured against what the agent said, and a decision able
    to rewrite that erases the evidence it is evidence of.
    """
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    store.record_proposal(submit(_proposal(), now=NOW))

    decided = approve(
        store.get_proposal("11111111-1111-4111-8111-111111111111"),
        approver=BA,
        comment="one change",
        corrections=(Correction("DOB.type", "date", "string"),),
        now=NOW,
    )
    stored = store.record_proposal(decided)

    assert stored.payload == PAYLOAD, "the agent's own output is intact"
    assert stored.state is ProposalState.APPROVED
    assert stored.decided_by is not None and stored.decided_by.subject == BA.subject
    assert [c.field_path for c in stored.corrections] == ["DOB.type"]
    assert stored.corrections[0].proposed == "date"


def test_an_r4_proposal_is_refused_by_the_database_as_well_as_the_code(plane: object) -> None:
    """Belt and braces, and the braces are the CHECK constraint.

    `Proposal.__post_init__` refuses R4, so this reaches past it with raw SQL —
    which is exactly the shape a future refactor or a stray migration script
    would take. A class that cannot be written cannot be automated.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        plane.execute(  # type: ignore[attr-defined]
            "INSERT INTO proposals.proposal (proposal_id, agent, capability, risk_class, "
            "run_id, state, payload, created_by_subject, created_by_type, created_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "22222222-2222-4222-8222-222222222222",
                "identity-merge",
                "merge_members",
                "R4",
                "run-2",
                "pending_review",
                "{}",
                "identity-merge",
                "ai",
                NOW,
            ),
        )


def test_the_review_queue_is_a_query_not_a_scan(plane: object) -> None:
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    store.record_proposal(submit(_proposal("33333333-3333-4333-8333-333333333333"), now=NOW))
    store.record_proposal(
        reject(
            submit(_proposal("44444444-4444-4444-8444-444444444444"), now=NOW),
            approver=BA,
            comment="wrong feed",
            now=NOW,
        )
    )

    pending = store.list_proposals(state=ProposalState.PENDING_REVIEW)
    assert [p.proposal_id for p in pending] == ["33333333-3333-4333-8333-333333333333"]
    assert len(store.list_proposals(feed_id=FEED)) == 2
    assert len(store.list_proposals(agent="schema-inference")) == 2
    assert store.list_proposals(agent="nobody") == ()


def test_applying_a_proposal_writes_a_draft_contract_and_nothing_published(
    plane: object,
) -> None:
    """The end-to-end shape, on the real store: an agent's suggestion becomes a
    DRAFT the approver authored, and NOTHING is published by this path."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    store.record_proposal(submit(_proposal("55555555-5555-4555-8555-555555555555"), now=NOW))

    decided = approve(
        store.get_proposal("55555555-5555-4555-8555-555555555555"), approver=BA, now=NOW
    )
    applied, draft = apply(
        decided,
        object_type=ObjectType.CONTRACT,
        object_id=FEED,
        body={"key_columns": ["source_member_id"], "columns": []},
        version=1,
        now=NOW,
    )
    store.save(draft)
    stored = store.record_proposal(applied)

    contract = store.get(ObjectType.CONTRACT, FEED)
    assert contract.lifecycle_state is LifecycleState.DRAFT
    assert contract.created_by.subject == BA.subject
    assert contract.approved_by is None, "nothing on this path signs anything"
    assert stored.state is ProposalState.APPLIED
    assert stored.applied_object_type is ObjectType.CONTRACT
    assert stored.applied_version == 1
