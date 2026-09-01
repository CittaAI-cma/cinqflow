"""The ONE lifecycle every governed object travels (ADR-0006).

    "no object type may opt out of the lifecycle state machine"
    "the author of a change never approves it            # refused + logged"
    "nothing reaches Published without a named approver  # refused + logged"
    — docs/architecture/INVARIANTS.md, governance

Archetype D's recipe says to write THE TWO UNIVERSAL NEGATIVES FIRST, so they
lead this file. Both are tested by MAKING THE ATTEMPT — a guardrail nobody
tries is a comment, not a control.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
    SelfApprovalError,
    UnnamedApproverError,
)
from cinqflow.core.model.vocabulary import ActorType, StatusWord

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 1, 3, 14, tzinfo=UTC)
ARUN = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun Menon")
STEVE = Actor(
    subject="steve@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steve Mathews"
)
AGENT = Actor(subject="pipeline_insight", actor_type=ActorType.AI, display_name="Pipeline Insight")


def _draft(author: Actor = ARUN) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id="fidelis-downstate-roster",
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=NOW,
        body={"domain": "enrollments"},
    )


def _submitted(author: Actor = ARUN) -> GovernedObject:
    moved, _ = _draft(author).transition_to(LifecycleState.PENDING_REVIEW, actor=author, now=NOW)
    return moved


# ── universal negative 1 · the author never approves ─────────────────────────
def test_the_author_of_a_change_can_never_approve_it() -> None:
    """ "Given an approval is attempted by the author, when they approve, then
    the system blocks it and records the attempt."

    Structurally impossible, and proven by a test that TRIES.
    """
    with pytest.raises(SelfApprovalError) as caught:
        _submitted(ARUN).transition_to(LifecycleState.APPROVED, actor=ARUN, now=NOW)
    assert "may not approve it" in str(caught.value)
    assert ARUN.subject in str(caught.value)


def test_a_different_person_may_approve() -> None:
    """The rule is segregation of duty, not an obstacle to approval."""
    approved, entry = _submitted(ARUN).transition_to(LifecycleState.APPROVED, actor=STEVE, now=NOW)
    assert approved.approved_by == STEVE
    assert approved.approved_ts == NOW
    assert entry.actor is STEVE


def test_an_agent_may_never_approve_at_any_confidence() -> None:
    """ "Agents propose; humans dispose." An approver is a NAMED PERSON, and an
    AI actor is not one — this is the R2 boundary expressed in the lifecycle
    rather than re-implemented per agent."""
    with pytest.raises(UnnamedApproverError) as caught:
        _submitted(ARUN).transition_to(LifecycleState.APPROVED, actor=AGENT, now=NOW)
    assert "humans dispose" in str(caught.value)


# ── universal negative 2 · nothing publishes without a named approver ────────
def test_publishing_without_a_named_approver_is_refused() -> None:
    """ "Require a NAMED approver before Published; publish without one is
    refused and logged."

    "Named" means a person — not a role, not a service account. An approval
    nobody's name is attached to is an approval nobody is accountable for.
    """
    approved_state = GovernedObject(
        object_type=ObjectType.FEED,
        object_id="f",
        version=1,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=ARUN,
        created_ts=NOW,
    )
    with pytest.raises(UnnamedApproverError):
        approved_state.transition_to(LifecycleState.PUBLISHED, actor=STEVE, now=NOW)


def test_an_object_cannot_be_constructed_as_published_without_an_approver() -> None:
    """The back door, closed. Going through transition_to is not the only way
    an object comes into existence — a repository load builds one directly."""
    with pytest.raises(UnnamedApproverError):
        GovernedObject(
            object_type=ObjectType.FEED,
            object_id="f",
            version=1,
            lifecycle_state=LifecycleState.PUBLISHED,
            created_by=ARUN,
            created_ts=NOW,
        )


def test_the_full_path_to_published_works_when_the_rules_are_met() -> None:
    submitted = _submitted(ARUN)
    approved, _ = submitted.transition_to(LifecycleState.APPROVED, actor=STEVE, now=NOW)
    published, entry = approved.transition_to(LifecycleState.PUBLISHED, actor=STEVE, now=NOW)
    assert published.is_executable is True
    assert entry.to_state is LifecycleState.PUBLISHED


# ── the state machine ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (LifecycleState.DRAFT, LifecycleState.APPROVED),  # skips review
        (LifecycleState.DRAFT, LifecycleState.PUBLISHED),  # skips everything
        (LifecycleState.PENDING_REVIEW, LifecycleState.PUBLISHED),
        (LifecycleState.RETIRED, LifecycleState.PUBLISHED),  # terminal
        (LifecycleState.REJECTED, LifecycleState.APPROVED),
    ],
)
def test_a_transition_outside_the_machine_is_refused(
    start: LifecycleState, target: LifecycleState
) -> None:
    """ "Allow changes outside the object's lifecycle states" is a documented
    don't on almost every registry story."""
    obj = GovernedObject(
        object_type=ObjectType.FEED,
        object_id="f",
        version=1,
        lifecycle_state=start,
        created_by=ARUN,
        created_ts=NOW,
        approved_by=STEVE if start is LifecycleState.RETIRED else None,
    )
    with pytest.raises(LifecycleViolationError) as caught:
        obj.transition_to(target, actor=STEVE, now=NOW)
    assert "Permitted:" in str(caught.value), "a refusal should say what IS allowed"


def test_a_refusal_names_what_would_have_been_allowed() -> None:
    """A refusal that only says "no" sends someone to read the source."""
    with pytest.raises(LifecycleViolationError) as caught:
        _draft().transition_to(LifecycleState.PUBLISHED, actor=STEVE, now=NOW)
    assert "pending_review" in str(caught.value)


def test_only_published_is_executable() -> None:
    """The engine reads published metadata, which makes "no unapproved
    configuration reaches production" a property of the READER rather than a
    rule the writer is asked to respect."""
    for state in LifecycleState:
        assert state.is_executable == (state is LifecycleState.PUBLISHED)


# ── audit ────────────────────────────────────────────────────────────────────
def test_every_transition_returns_its_audit_entry_inseparably() -> None:
    """Returned TOGETHER, deliberately: a state change that could be persisted
    without its audit row is a state change that eventually will be."""
    moved, entry = _draft().transition_to(LifecycleState.PENDING_REVIEW, actor=ARUN, now=NOW)
    assert entry.from_state is LifecycleState.DRAFT
    assert entry.to_state is LifecycleState.PENDING_REVIEW
    assert entry.actor_type is ActorType.HUMAN
    assert entry.object_id == moved.object_id
    assert entry.version == moved.version


def test_an_actor_without_a_subject_is_refused() -> None:
    """An actor with no subject is an anonymous action, and nobody touches the
    platform anonymously."""
    with pytest.raises(ValueError, match="anonymous"):
        Actor(subject="   ", actor_type=ActorType.HUMAN)


# ── versioning ───────────────────────────────────────────────────────────────
def test_amending_creates_the_next_version_in_draft_never_an_in_place_edit() -> None:
    """ "promoted configuration is byte-identical to what was approved"

    A published object stays exactly as approved; the amendment is a new draft
    that must earn its own approval.
    """
    published = _publish(_draft())
    amended = published.new_version({"domain": "enrollments", "format": "csv"}, actor=ARUN)
    assert amended.version == 2
    assert amended.lifecycle_state is LifecycleState.DRAFT
    assert amended.approved_by is None, "a new version does not inherit approval"
    assert published.body == {"domain": "enrollments"}, "the approved version is untouched"


def test_a_version_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="versions start at 1"):
        GovernedObject(
            object_type=ObjectType.FEED,
            object_id="f",
            version=0,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=ARUN,
            created_ts=NOW,
        )


# ── the seven status words ───────────────────────────────────────────────────
def test_every_lifecycle_state_projects_onto_one_of_the_seven_words() -> None:
    """The richer machine stays internal. Users see seven words only, and a
    state with no projection would force a screen to invent an eighth."""
    for state in LifecycleState:
        assert state.status_word in set(StatusWord)


def test_pending_review_reads_as_needs_review() -> None:
    assert LifecycleState.PENDING_REVIEW.status_word is StatusWord.NEEDS_REVIEW
    assert LifecycleState.PUBLISHED.status_word is StatusWord.COMPLETED
    assert LifecycleState.REJECTED.status_word is StatusWord.NEEDS_ATTENTION


# ── no object type opts out ──────────────────────────────────────────────────
@pytest.mark.parametrize("object_type", list(ObjectType))
def test_every_object_type_travels_the_same_machine(object_type: ObjectType) -> None:
    """ "Route every object type through the one lifecycle; let any object type
    opt out of it" is the never.

    A new ObjectType inherits the lifecycle, the audit trail and both universal
    negatives — for free, and unavoidably.
    """
    obj = GovernedObject(
        object_type=object_type,
        object_id="x",
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=ARUN,
        created_ts=NOW,
    )
    submitted, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=ARUN, now=NOW)
    with pytest.raises(SelfApprovalError):
        submitted.transition_to(LifecycleState.APPROVED, actor=ARUN, now=NOW)


def _publish(obj: GovernedObject) -> GovernedObject:
    submitted, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=obj.created_by, now=NOW)
    approved, _ = submitted.transition_to(LifecycleState.APPROVED, actor=STEVE, now=NOW)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=STEVE, now=NOW)
    return published
