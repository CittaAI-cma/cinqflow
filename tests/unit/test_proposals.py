"""The universal HITL object — what an agent may write, and what it may never do.

    "Agents propose; humans dispose. R4 is human-always and not configurable."
    — docs/architecture/plates/11-agent-runtime-and-the-risk-router.md

    "no agent path writes production state"
    — docs/architecture/INVARIANTS.md, intelligence

THE NEGATIVES ARE THE STORY. Every R2 agent in Wave 1 writes through this one
object, so a hole here is a hole in four capabilities at once — which is
precisely why there is one object rather than four.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import (
    AgentDecisionError,
    Correction,
    NotAutomatableError,
    Proposal,
    ProposalError,
    ProposalState,
    apply,
    approve,
    diff_fields,
    measure,
    reject,
    submit,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
AGENT = Actor(subject="schema-inference", actor_type=ActorType.AI, display_name="Schema inference")
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)

PAYLOAD = {
    "key": "source_name",
    "records": [
        {"source_name": "MemberID", "name": "source_member_id", "type": "string", "is_phi": True},
        {"source_name": "DOB", "name": "date_of_birth", "type": "date", "is_phi": True},
        {"source_name": "LOB", "name": "line_of_business", "type": "string", "is_phi": False},
    ],
}


def _proposal(**overrides: object) -> Proposal:
    return Proposal(
        **{
            "proposal_id": "prop-1",
            "agent": "schema-inference",
            "capability": "propose_schema_contract",
            "risk_class": RiskClass.R2,
            "run_id": "run-1",
            "feed_id": "fidelis-downstate-roster",
            "payload": PAYLOAD,
            "created_by": AGENT,
            "created_ts": NOW,
            **overrides,  # type: ignore[dict-item]
        }
    )


# ── what an agent may never do ───────────────────────────────────────────────


def test_an_r4_proposal_cannot_be_constructed_at_all() -> None:
    """ "R4 is human-always and NOT CONFIGURABLE — at any confidence."

    Refused in code as well as at the schema's CHECK constraint. Belt and
    braces on purpose: a class that cannot be written cannot be automated by a
    later refactor either.
    """
    with pytest.raises(NotAutomatableError, match="human-always"):
        _proposal(risk_class=RiskClass.R4)


def test_confidence_cannot_promote_a_proposal_out_of_its_class() -> None:
    """The single most consequential rule in the intelligence plane, asserted
    where an agent would try to bend it."""
    assert RiskClass.R4.at_confidence(0.999) is RiskClass.R4
    with pytest.raises(NotAutomatableError):
        _proposal(risk_class=RiskClass.R4.at_confidence(0.999))


def test_an_agent_may_not_approve_a_proposal() -> None:
    """ "Agents propose; humans dispose." Including its own — but including any
    other agent's too, which is the loophole a per-agent check would leave."""
    with pytest.raises(AgentDecisionError, match="Agents propose"):
        approve(submit(_proposal(), now=NOW), approver=AGENT, now=NOW)


def test_an_agent_may_not_reject_a_proposal_either() -> None:
    """A rejection is a decision. An agent that could reject could quietly
    dispose of the suggestions a human was meant to see."""
    with pytest.raises(AgentDecisionError):
        reject(submit(_proposal(), now=NOW), approver=AGENT, comment="no", now=NOW)


def test_a_human_cannot_create_a_proposal() -> None:
    """A person who wants a change makes a DRAFT, which travels the ordinary
    lifecycle. Letting a human file a proposal would create a second route to
    a governed object with a different set of gates on it."""
    with pytest.raises(ProposalError, match="A proposal is an AGENT"):
        _proposal(created_by=BA)


def test_an_undecided_proposal_cannot_be_applied() -> None:
    """Applying an unapproved proposal IS an agent writing production state."""
    with pytest.raises(ProposalError, match="only an approved proposal"):
        apply(
            submit(_proposal(), now=NOW),
            object_type=ObjectType.CONTRACT,
            object_id="fidelis-downstate-roster",
            body={},
            now=NOW,
        )


def test_a_rejection_without_a_reason_is_refused() -> None:
    """A rejection is the most informative event an agent's output can produce,
    and discarding the reason wastes it."""
    with pytest.raises(ProposalError, match="most informative"):
        reject(submit(_proposal(), now=NOW), approver=BA, comment="   ", now=NOW)


def test_an_empty_payload_is_refused() -> None:
    with pytest.raises(ProposalError, match="nothing to review"):
        _proposal(payload={})


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (ProposalState.DRAFT, ProposalState.APPROVED),
        (ProposalState.REJECTED, ProposalState.APPROVED),
        (ProposalState.APPLIED, ProposalState.APPROVED),
    ],
)
def test_illegal_transitions_are_refused(start: ProposalState, target: ProposalState) -> None:
    """A proposal that could go from Rejected back to Approved is a proposal
    somebody can re-run until it passes."""
    _ = target
    with pytest.raises(ProposalError, match="not a legal"):
        approve(_proposal(state=start), approver=BA, now=NOW)


# ── the applied object, and why it is a Draft ────────────────────────────────


def test_approval_creates_a_draft_authored_by_the_approver() -> None:
    """THE SHAPE THAT MAKES THIS SAFE.

    The object arrives DRAFT, authored by the human who accepted it — so it
    travels E11-01's lifecycle like a hand-typed one, and the universal
    negative bites: they cannot then approve the object they just accepted.
    """
    decided = approve(submit(_proposal(), now=NOW), approver=BA, now=NOW)
    applied, draft = apply(
        decided,
        object_type=ObjectType.CONTRACT,
        object_id="fidelis-downstate-roster",
        body={"columns": []},
        now=NOW,
    )

    assert draft.lifecycle_state is LifecycleState.DRAFT
    assert draft.created_by == BA, "the approver authors it — not the agent"
    assert draft.created_by.actor_type is ActorType.HUMAN
    assert applied.state is ProposalState.APPLIED
    assert applied.applied_object_type is ObjectType.CONTRACT
    assert applied.applied_version == 1


def test_the_applied_body_is_the_humans_version_not_the_agents() -> None:
    """`apply` never reads the payload. Taking it here would silently discard
    every correction the reviewer just made."""
    decided = approve(
        submit(_proposal(), now=NOW),
        approver=BA,
        corrections=(Correction("DOB.type", "string", "date"),),
        now=NOW,
    )
    _, draft = apply(
        decided,
        object_type=ObjectType.CONTRACT,
        object_id="f",
        body={"columns": [{"name": "date_of_birth", "type": "date"}]},
        now=NOW,
    )
    assert draft.body["columns"][0]["type"] == "date"


def test_the_payload_survives_every_transition() -> None:
    """What the agent said is what the eval set is measured against. A decision
    that rewrote it would erase the evidence it is evidence of."""
    proposal = submit(_proposal(), now=NOW)
    decided = approve(
        proposal,
        approver=BA,
        corrections=(Correction("LOB.name", "line_of_business", "lob"),),
        now=NOW,
    )
    applied, _ = apply(decided, object_type=ObjectType.CONTRACT, object_id="f", body={}, now=NOW)
    assert applied.payload == PAYLOAD


# ── corrections as fuel ──────────────────────────────────────────────────────


def test_an_untouched_approval_records_no_corrections() -> None:
    """The unit the >= 90% gate counts in."""
    decided = approve(submit(_proposal(), now=NOW), approver=BA, now=NOW)
    assert decided.corrections == ()
    assert decided.is_accepted_untouched is True


def test_a_changed_field_is_captured_with_both_values() -> None:
    """A correction recording only the new value tells the eval set what is
    right and not what was wrong — and the second half is what improves a
    prompt."""
    accepted = {
        "records": [
            {**PAYLOAD["records"][0]},  # type: ignore[index]
            {**PAYLOAD["records"][1], "type": "string"},  # type: ignore[index]
            {**PAYLOAD["records"][2]},  # type: ignore[index]
        ]
    }
    found = diff_fields(
        PAYLOAD, accepted, key="source_name", fields=("name", "type", "nullable", "is_phi")
    )
    assert len(found) == 1
    assert found[0].field_path == "DOB.type"
    assert (found[0].proposed, found[0].accepted) == ("date", "string")
    assert found[0].is_addition is False


def test_a_field_the_human_added_is_an_addition_not_a_miss() -> None:
    """An agent that declines to guess and a human who fills the gap is the
    DESIGNED behaviour. Scoring it as a miss would teach the agent to guess."""
    accepted = {"records": [*PAYLOAD["records"], {"source_name": "EndDate", "type": "date"}]}  # type: ignore[misc]
    found = diff_fields(PAYLOAD, accepted, key="source_name", fields=("type",))
    assert len(found) == 1
    assert found[0].is_addition is True


# ── the eval arithmetic ──────────────────────────────────────────────────────


def test_an_untouched_proposal_scores_one_hundred_percent() -> None:
    decided = approve(submit(_proposal(), now=NOW), approver=BA, now=NOW)
    acceptance = measure(decided)
    assert (acceptance.total, acceptance.corrected) == (3, 0)
    assert acceptance.rate == 1.0
    assert acceptance.passes(0.90) is True


def test_one_corrected_column_of_three_scores_two_thirds() -> None:
    decided = approve(
        submit(_proposal(), now=NOW),
        approver=BA,
        corrections=(Correction("DOB.type", "date", "string"),),
        now=NOW,
    )
    acceptance = measure(decided)
    assert (acceptance.total, acceptance.corrected, acceptance.accepted) == (3, 1, 2)
    assert acceptance.passes(0.90) is False


def test_two_corrections_to_one_column_are_one_corrected_column() -> None:
    """The unit is a COLUMN. Counting attributes would make the gate depend on
    how many attributes a record happens to carry — a schema that gained a
    `precision` field would look better at no improvement in accuracy."""
    decided = approve(
        submit(_proposal(), now=NOW),
        approver=BA,
        corrections=(
            Correction("DOB.type", "date", "string"),
            Correction("DOB.name", "date_of_birth", "dob"),
        ),
        now=NOW,
    )
    assert measure(decided).corrected == 1


def test_the_models_share_is_reported_apart_from_the_arithmetics() -> None:
    """A 94% contract built from 90% arithmetic is not evidence that a model is
    good at inference, and a single number would read as if it were."""
    decided = approve(
        submit(_proposal(), now=NOW),
        approver=BA,
        corrections=(Correction("DOB.type", "date", "string"),),
        now=NOW,
    )
    acceptance = measure(decided, deterministic_keys=frozenset({"MemberID", "LOB"}))

    assert (acceptance.deterministic_total, acceptance.deterministic_corrected) == (2, 0)
    assert (acceptance.inferred_total, acceptance.inferred_corrected) == (1, 1)
    assert acceptance.rate > acceptance.inferred_rate
    assert acceptance.inferred_rate == 0.0
    assert "inferred 0/1" in acceptance.report(0.90)


def test_a_zero_field_measurement_fails_rather_than_passing_vacuously() -> None:
    """An eval that returns 100% because it graded nothing is the most
    dangerous green there is."""
    decided = approve(
        submit(_proposal(payload={"key": "source_name", "records": []}), now=NOW),
        approver=BA,
        now=NOW,
    )
    acceptance = measure(decided)
    assert acceptance.total == 0
    assert acceptance.passes(0.90) is False


def test_an_addition_is_not_counted_against_the_model() -> None:
    decided = approve(
        submit(_proposal(), now=NOW),
        approver=BA,
        corrections=(Correction("EndDate", None, {"type": "date"}),),
        now=NOW,
    )
    acceptance = measure(decided)
    assert acceptance.additions == 1
    assert acceptance.corrected == 0
    assert acceptance.rate == 1.0, "the three proposed columns were all accepted"


# ── the ordinary path ────────────────────────────────────────────────────────


def test_a_proposal_starts_as_a_draft_and_is_submitted_for_review() -> None:
    proposal = _proposal()
    assert proposal.state is ProposalState.DRAFT
    assert submit(proposal, now=NOW).state is ProposalState.PENDING_REVIEW


def test_a_rejection_keeps_its_reason() -> None:
    decided = reject(
        submit(_proposal(), now=NOW),
        approver=STEWARD,
        comment="DOB is an integer member id at this payer, not a date",
        now=NOW,
    )
    assert decided.state is ProposalState.REJECTED
    assert decided.decided_by == STEWARD
    assert "member id" in decided.decision_comment
