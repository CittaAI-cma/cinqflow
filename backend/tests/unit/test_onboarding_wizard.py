"""CF-V1-E4-01 — the wizard whose checklist cannot lie.

    "Given a BA has a sample file for a new payer roster, when she works
     through the five steps over three sessions, then each return resumes
     exactly where she left off, the checklist fills in as approvals land, and
     step 5 unlocks only when everything upstream is genuinely approved."
    "Given her mapping contains two UNMAPPED fields, when she opens step 5,
     then publishing is blocked with the two fields named and one-click
     navigation back to them."
    — CF-V1-E4-01

The don't that shapes every test here — "let a step be marked complete while
its underlying object is unapproved" — is asserted directly by
`test_a_submitted_object_is_not_a_complete_step`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.citations import CitationKind
from cinqflow.core.mapping import FeedMapping, MappingLine, mapping_as_governed
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, StatusWord
from cinqflow.core.onboarding import (
    OnboardingInputs,
    Step,
    StepState,
    latest,
    unmapped_fields,
    wizard,
)
from cinqflow.core.registry.canonical import CanonicalModel
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "centene-medicare-roster"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Dana")
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)

DEPLOYED = Schema(
    name="silver_ods",
    description="the one deployed entity",
    tables=(
        Table(
            name="members",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                # NOT NULL — a required canonical field.
                Column("date_of_birth", TypeName.DATE, nullable=False, is_phi=True),
                # Nullable — an optional one.
                Column("middle_name", TypeName.STRING),
            ),
            primary_key=("member_row_id",),
        ),
    ),
)


def model() -> CanonicalModel:
    from cinqflow.core.registry.canonical import build
    from cinqflow.core.registry.glossary import Glossary

    return build((DEPLOYED,), Glossary(terms=()))


def governed(
    object_type: ObjectType,
    state: LifecycleState,
    *,
    version: int = 1,
    body: dict | None = None,
) -> GovernedObject:
    approved = state in {LifecycleState.APPROVED, LifecycleState.PUBLISHED}
    return GovernedObject(
        object_type=object_type,
        object_id=FEED,
        version=version,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body=body or {},
        approved_by=STEWARD if approved else None,
        approved_ts=NOW if approved else None,
    )


def mapping_object(
    state: LifecycleState, *, unmapped: tuple[tuple[str, str], ...] = ()
) -> GovernedObject:
    lines = [
        MappingLine(
            target_entity="members",
            target_field="member_row_id",
            source_columns=("MEMBER_ID",),
        )
    ]
    lines.extend(
        MappingLine(
            target_entity="members",
            target_field=field_name,
            unmapped_reason=reason,
        )
        for field_name, reason in unmapped
    )
    obj = mapping_as_governed(
        FeedMapping(feed_id=FEED, lines=tuple(lines)), author=BA, created_ts=NOW
    )
    approved = state in {LifecycleState.APPROVED, LifecycleState.PUBLISHED}
    return GovernedObject(
        object_type=obj.object_type,
        object_id=obj.object_id,
        version=obj.version,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body=obj.body,
        approved_by=STEWARD if approved else None,
        approved_ts=NOW if approved else None,
    )


APPROVED_STACK = (
    governed(ObjectType.CONTRACT, LifecycleState.APPROVED),
    mapping_object(LifecycleState.APPROVED),
    governed(ObjectType.DQ_RULE, LifecycleState.APPROVED),
    governed(ObjectType.FEED, LifecycleState.APPROVED, body={"schedule_cron": "0 6 1 * *"}),
)


# ── the empty start ──────────────────────────────────────────────────────────
def test_a_new_feed_starts_at_step_one_with_everything_after_it_locked() -> None:
    view = wizard(OnboardingInputs(feed_id=FEED))
    assert view.resume_at is Step.SAMPLE
    assert view.status(Step.SAMPLE).state is StepState.NOT_STARTED
    assert view.status(Step.SCHEMA).state is StepState.LOCKED
    assert not view.is_publishable


def test_uploading_a_sample_completes_step_one_and_unlocks_step_two() -> None:
    """A sample is an OBSERVATION — nothing approves it, so it is complete the
    moment one exists."""
    view = wizard(OnboardingInputs(feed_id=FEED, sample_profile_ids=("sha256-abc",)))
    assert view.status(Step.SAMPLE).is_complete
    assert view.status(Step.SCHEMA).state is StepState.NOT_STARTED
    assert view.resume_at is Step.SCHEMA
    assert view.status(Step.SAMPLE).citation is not None
    assert view.status(Step.SAMPLE).citation.kind is CitationKind.PROFILE


# ── the don't ────────────────────────────────────────────────────────────────
def test_a_submitted_object_is_not_a_complete_step() -> None:
    """THE DON'T, ASSERTED.

    "Let a step be marked complete while its underlying object is unapproved —
    the checklist reflects real states, not optimism."
    """
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("sha256-abc",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.PENDING_REVIEW),),
        )
    )
    schema = view.status(Step.SCHEMA)
    assert schema.state is StepState.AWAITING_APPROVAL
    assert not schema.is_complete
    assert schema.state.status_word is StatusWord.NEEDS_REVIEW
    assert any(o.key == "schema_in_review" for o in schema.blocking)


def test_a_rejected_object_blocks_and_says_what_to_do() -> None:
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("sha256-abc",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.REJECTED),),
        )
    )
    schema = view.status(Step.SCHEMA)
    assert schema.state is StepState.BLOCKED
    assert "resubmit" in schema.blocking[0].how_to_fix


def test_the_checklist_fills_in_as_approvals_land() -> None:
    partial = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("sha256-abc",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.APPROVED),),
        )
    )
    assert partial.completed == (Step.SAMPLE, Step.SCHEMA)
    assert partial.resume_at is Step.MAPPING


# ── save and resume ──────────────────────────────────────────────────────────
def test_resume_is_computed_so_a_return_after_weeks_lands_correctly() -> None:
    """The wizard has no `current_step`. Three sessions, three resumes, and
    nothing was stored between them."""
    session_one = wizard(OnboardingInputs(feed_id=FEED, sample_profile_ids=("s",)))
    session_two = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.APPROVED),),
        )
    )
    session_three = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(
                governed(ObjectType.CONTRACT, LifecycleState.APPROVED),
                mapping_object(LifecycleState.APPROVED),
            ),
        )
    )
    assert (session_one.resume_at, session_two.resume_at, session_three.resume_at) == (
        Step.SCHEMA,
        Step.MAPPING,
        Step.RULES,
    )


def test_the_wizard_holds_no_state_of_its_own() -> None:
    """A structural assertion: `Wizard` carries only what it computed, so there
    is no pointer that could disagree with the objects."""
    from dataclasses import fields

    from cinqflow.core.onboarding import Wizard

    names = {f.name for f in fields(Wizard)}
    assert names == {"feed_id", "steps", "operations"}
    assert "current_step" not in names


def test_the_latest_version_is_chosen_by_version_not_by_timestamp() -> None:
    """A clock skew between two writers must never change which contract the
    wizard reads."""
    older_stamp_higher_version = GovernedObject(
        object_type=ObjectType.CONTRACT,
        object_id=FEED,
        version=3,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=datetime(2020, 1, 1, tzinfo=UTC),
    )
    newer_stamp_lower_version = governed(ObjectType.CONTRACT, LifecycleState.APPROVED, version=2)
    chosen = latest((newer_stamp_lower_version, older_stamp_higher_version), ObjectType.CONTRACT)
    assert chosen is not None and chosen.version == 3


# ── the exception: two UNMAPPED fields ───────────────────────────────────────
def test_two_unmapped_required_fields_block_publishing_and_are_named() -> None:
    """The story's own exception, with `date_of_birth` required by the model."""
    mapping = mapping_object(
        LifecycleState.APPROVED,
        unmapped=(("date_of_birth", "the payer does not send it"),),
    )
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(
                governed(ObjectType.CONTRACT, LifecycleState.APPROVED),
                mapping,
                governed(ObjectType.DQ_RULE, LifecycleState.APPROVED),
                governed(ObjectType.FEED, LifecycleState.APPROVED),
            ),
            model=model(),
            evidence_fingerprint="sha256-x",
            configuration_fingerprint="sha256-x",
        )
    )
    assert not view.is_publishable
    named = [o.what for o in view.status(Step.PUBLISH).blocking]
    assert any("members.date_of_birth" in what for what in named)


def test_one_click_navigation_opens_the_line_not_just_the_mapping() -> None:
    """The obstacle's citation carries the FIELD as its fragment, so the route
    lands on the line at fault."""
    mapping = mapping_object(LifecycleState.APPROVED, unmapped=(("date_of_birth", "not sent"),))
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.APPROVED), mapping),
            model=model(),
        )
    )
    obstacle = view.status(Step.MAPPING).blocking[0]
    assert obstacle.citation is not None
    assert obstacle.citation.fragment == "date_of_birth"
    assert obstacle.route.startswith("/data/intake/mapping/")


def test_an_optional_unmapped_field_is_an_honest_gap_and_not_a_refusal() -> None:
    """The client's own `NO MAP Fields` sheet has a Reason column. A wizard
    that refused every documented no-map would make their existing, reviewed
    decisions unrepresentable."""
    mapping = mapping_object(
        LifecycleState.APPROVED, unmapped=(("middle_name", "this payer never sends it"),)
    )
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.APPROVED), mapping),
            model=model(),
        )
    )
    mapping_step = view.status(Step.MAPPING)
    assert mapping_step.blocking == ()
    assert [o.key for o in mapping_step.advisory] == ["unmapped:members.middle_name"]


def test_an_unknown_target_never_manufactures_a_blocker() -> None:
    """Unknown means NOT required. Refusing for a reason nobody can act on is
    how a platform teaches people to route around it."""
    mapping = mapping_object(LifecycleState.APPROVED, unmapped=(("nowhere", "unknown"),))
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=(governed(ObjectType.CONTRACT, LifecycleState.APPROVED), mapping),
            model=model(),
        )
    )
    assert view.status(Step.MAPPING).blocking == ()


def test_unmapped_fields_are_named_not_counted() -> None:
    mapping = mapping_object(
        LifecycleState.APPROVED,
        unmapped=(("date_of_birth", "a"), ("middle_name", "b")),
    )
    assert unmapped_fields(mapping) == ("members.date_of_birth", "members.middle_name")


# ── step 5 unlocks only when everything upstream is genuinely approved ───────
def test_step_five_needs_evidence_before_it_will_unlock() -> None:
    view = wizard(OnboardingInputs(feed_id=FEED, sample_profile_ids=("s",), objects=APPROVED_STACK))
    assert not view.is_publishable
    assert any(o.key == "no_evidence" for o in view.outstanding)


def test_stale_evidence_blocks_step_five_with_the_reason_stated() -> None:
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=APPROVED_STACK,
            evidence_fingerprint="sha256-old",
            configuration_fingerprint="sha256-new",
        )
    )
    stale = [o for o in view.outstanding if o.key == "stale_evidence"]
    assert stale and "no longer the one being approved" in stale[0].why_it_matters


def test_a_green_wizard_is_publishable_and_says_so() -> None:
    view = wizard(
        OnboardingInputs(
            feed_id=FEED,
            sample_profile_ids=("s",),
            objects=APPROVED_STACK,
            model=model(),
            evidence_fingerprint="sha256-same",
            configuration_fingerprint="sha256-same",
        )
    )
    assert view.is_publishable
    assert view.outstanding == ()
    assert "ready to publish" in view.explain()


def test_the_readiness_view_speaks_business_language() -> None:
    view = wizard(OnboardingInputs(feed_id=FEED))
    text = view.explain()
    assert "Upload a sample file" in text
    assert "governed_object" not in text
    assert "lifecycle_state" not in text
