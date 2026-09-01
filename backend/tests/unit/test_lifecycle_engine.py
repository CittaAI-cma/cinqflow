"""CF-V1-E11-01 — the one lifecycle engine, and its routing.

    "No state change without the required approver; author never approves own
     change."
    — CF-V1-E11-01, measurable

    "Given an approval is attempted by the author or an unauthorized role, when
     they approve, then the system blocks it and records the attempt."
    — CF-V1-E11-01, guardrail

THE NEGATIVES ARE FIRST IN THIS FILE, deliberately — they were written before
the routes existed, and archetype D's recipe says the two universal negatives
are built before the happy path. Governance you can demonstrate failing is
governance you can trust.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core import lifecycle
from cinqflow.core.lifecycle import APPROVAL_ROUTING, ApprovalRoutingError
from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
    SelfApprovalError,
    UnnamedApproverError,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)

BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
STEWARD = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Daniel"
)
PLATFORM = Actor(
    subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ravi"
)
AGENT = Actor(subject="onboarding_copilot", actor_type=ActorType.AI, display_name="copilot")

STEWARD_ROLES = frozenset({Role.DATA_STEWARD})
PLATFORM_ROLES = frozenset({Role.PLATFORM_ENGINEER})


def _object(
    object_type: ObjectType = ObjectType.MAPPING,
    state: LifecycleState = LifecycleState.PENDING_REVIEW,
    author: Actor = BA,
) -> GovernedObject:
    return GovernedObject(
        object_type=object_type,
        object_id="fidelis-downstate-roster",
        version=1,
        lifecycle_state=state,
        created_by=author,
        created_ts=NOW,
        body={"lines": 45},
    )


# ── the two universal negatives ──────────────────────────────────────────────


def test_the_author_of_a_change_never_approves_it() -> None:
    """Universal negative #1. The BA who wrote the mapping holds no APPROVE at
    all — but even if she did, the engine refuses, which is why this is a
    property of the core rather than of the permission table."""
    mapping = _object(author=BA)
    with pytest.raises(SelfApprovalError):
        lifecycle.approve(
            mapping, actor=BA, roles=frozenset({Role.DATA_STEWARD}), comment="mine, but fine"
        )


def test_nothing_reaches_published_without_a_named_approver() -> None:
    """Universal negative #2. An Approved object always carries its approver,
    so the only way to reach this is to forge the state — and it is still
    refused."""
    forged = GovernedObject(
        object_type=ObjectType.MAPPING,
        object_id="f",
        version=1,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=BA,
        created_ts=NOW,
    )
    with pytest.raises(UnnamedApproverError):
        lifecycle.publish(forged, actor=STEWARD, roles=STEWARD_ROLES)


def test_an_agent_can_never_be_the_approver() -> None:
    """ "Agents propose; humans dispose" — as a raise, at any confidence."""
    with pytest.raises(UnnamedApproverError):
        lifecycle.approve(_object(), actor=AGENT, roles=STEWARD_ROLES, comment="high confidence")


# ── the routing refusals ─────────────────────────────────────────────────────


def test_a_steward_may_not_approve_an_engineered_type() -> None:
    """The permission matrix says what a role may attempt; the router says
    where. A steward holding APPROVE still cannot approve a schema contract."""
    with pytest.raises(ApprovalRoutingError, match="platform_engineer"):
        lifecycle.approve(
            _object(ObjectType.CONTRACT), actor=STEWARD, roles=STEWARD_ROLES, comment="ok"
        )


def test_a_platform_engineer_may_not_approve_a_stewarded_type() -> None:
    with pytest.raises(ApprovalRoutingError, match="data_steward"):
        lifecycle.approve(
            _object(ObjectType.MAPPING), actor=PLATFORM, roles=PLATFORM_ROLES, comment="ok"
        )


def test_every_object_type_is_routed() -> None:
    """A new ObjectType cannot ship without the guardrails the first ten have —
    which is ADR-0006's "no object type may opt out" as a test rather than a
    hope."""
    assert set(APPROVAL_ROUTING) == set(ObjectType)


def test_an_illegal_transition_is_refused_with_the_permitted_ones_named() -> None:
    """ "Given an illegal transition (Draft -> Retired) is attempted" — and the
    refusal has to say what WAS permitted, or the author is left guessing."""
    with pytest.raises(LifecycleViolationError, match="pending_review"):
        lifecycle.publish(_object(state=LifecycleState.DRAFT), actor=STEWARD, roles=STEWARD_ROLES)


def test_request_changes_without_a_comment_is_refused() -> None:
    """A change request with nothing said is a rejection the author cannot act
    on."""
    with pytest.raises(LifecycleViolationError, match="comment"):
        lifecycle.request_changes(_object(), actor=STEWARD, roles=STEWARD_ROLES, comment="   ")


def test_a_rejection_must_say_why() -> None:
    with pytest.raises(LifecycleViolationError, match="say why"):
        lifecycle.reject(_object(), actor=STEWARD, roles=STEWARD_ROLES, comment="")


# ── the happy path, and the conversation it preserves ────────────────────────


def test_a_mapping_travels_draft_to_published_through_its_steward() -> None:
    draft = _object(state=LifecycleState.DRAFT)
    submitted, submit_entry = lifecycle.submit(draft, actor=BA, comment="ready for review")
    assert submitted.lifecycle_state is LifecycleState.PENDING_REVIEW
    assert submit_entry.detail == "ready for review"

    approved, approve_entry = lifecycle.approve(
        submitted, actor=STEWARD, roles=STEWARD_ROLES, comment="precedents check out"
    )
    assert approved.approved_by == STEWARD
    assert approve_entry.actor_type is ActorType.HUMAN

    published, _ = lifecycle.publish(approved, actor=STEWARD, roles=STEWARD_ROLES)
    assert published.is_executable is True


def test_request_changes_returns_it_to_draft_with_the_comment_on_the_trail() -> None:
    """ "re-submission that preserves the conversation" — the conversation is
    the audit trail, so the comment has to land ON the entry."""
    returned, entry = lifecycle.request_changes(
        _object(), actor=STEWARD, roles=STEWARD_ROLES, comment="MBR_DOB needs CCYYMMDD"
    )
    assert returned.lifecycle_state is LifecycleState.DRAFT
    assert entry.detail == "MBR_DOB needs CCYYMMDD"
    assert entry.from_state is LifecycleState.PENDING_REVIEW


# ── the Work Queue ───────────────────────────────────────────────────────────


def test_the_queue_never_offers_a_reviewer_their_own_work() -> None:
    """The engine would refuse it, so the queue must not offer it — a queue
    full of work you cannot action is how people learn to ignore a queue."""
    own = _object(author=STEWARD)
    someone_elses = _object(author=BA)
    queued = lifecycle.awaiting_review_by(
        (own, someone_elses), roles=STEWARD_ROLES, subject=STEWARD.subject
    )
    assert [o.created_by.subject for o in queued] == [BA.subject]


def test_the_queue_shows_only_what_routes_to_the_callers_roles() -> None:
    contract = _object(ObjectType.CONTRACT)
    mapping = _object(ObjectType.MAPPING)
    for_steward = lifecycle.awaiting_review_by(
        (contract, mapping), roles=STEWARD_ROLES, subject=STEWARD.subject
    )
    assert [o.object_type for o in for_steward] == [ObjectType.MAPPING]


def test_an_author_sees_their_own_work_still_in_flight() -> None:
    in_flight = _object(state=LifecycleState.PENDING_REVIEW, author=BA)
    published = _object(state=LifecycleState.DRAFT, author=STEWARD)
    mine = lifecycle.submitted_by((in_flight, published), subject=BA.subject)
    assert [o.created_by.subject for o in mine] == [BA.subject]
