"""CF-V1-E3-02 — the operational envelope, and the activation checklist.

Pure. Every refusal in this file is a real incident from the estate's history
or a real failure mode of the incumbent platform, and each one is written as a
NEGATIVE TEST that makes the attempt — a guardrail nobody tries is a comment.

The organising idea: SAVE IS PERMISSIVE, ACTIVATION IS NOT. A half-gathered
feed must store, because an analyst waiting three days for a payer's SLA needs
somewhere to keep what they already have. What is refused is asking somebody
to review a feed nobody could operate.
"""

from __future__ import annotations

import pytest

from cinqflow.core.lifecycle import submit
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.operations import (
    ActivationBlockedError,
    AlertChannel,
    AlertTier,
    DeliveryCalendar,
    DeliveryMethod,
    DocumentKind,
    FeedOperations,
    LinkedDocument,
    OperationsValidationError,
    Owner,
    OwnerRole,
    ServiceLevel,
    VolumeExpectation,
    readiness,
    readiness_of,
)

pytestmark = pytest.mark.unit

BA = Actor(subject="meera@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera Rao")

BUSINESS = Owner(role=OwnerRole.BUSINESS, subject="meera@cinqcare.test", display_name="Meera Rao")
TECHNICAL = Owner(role=OwnerRole.TECHNICAL, subject="sam@cinqcare.test", display_name="Sam Okafor")
SLA = ServiceLevel(expected_by_local_time="06:00", timezone="America/New_York")
CHAIN = (
    AlertTier(after_minutes=30, channel=AlertChannel.EMAIL, notify=("sam@cinqcare.test",)),
    AlertTier(after_minutes=120, channel=AlertChannel.PAGER, notify=("meera@cinqcare.test",)),
)


def _complete(**overrides) -> FeedOperations:  # type: ignore[no-untyped-def]
    defaults = {
        "source_id": "fidelis-ny",
        "endpoint_ref": "fidelis-downstate-sftp",
        "owners": (BUSINESS, TECHNICAL),
        "service_level": SLA,
        "volume": VolumeExpectation(typical_records=40_000),
        "alert_chain": CHAIN,
    }
    return FeedOperations(**{**defaults, **overrides})


# ── owners: a named person, not an inbox ─────────────────────────────────────


def test_an_owner_needs_a_name_not_only_an_address() -> None:
    with pytest.raises(OperationsValidationError, match="an owner needs a name"):
        Owner(role=OwnerRole.BUSINESS, subject="data@cinqcare.test", display_name="")


def test_an_unattended_mailbox_cannot_own_a_feed() -> None:
    """The case that actually happened: an alert chain whose last tier was an
    inbox nobody read, discovered during an incident."""
    with pytest.raises(OperationsValidationError, match="unattended mailbox"):
        Owner(role=OwnerRole.TECHNICAL, subject="noreply@cinqcare.test", display_name="Alerts")


def test_a_normal_team_address_is_still_allowed() -> None:
    """The check is a short explicit list, not a heuristic about what looks
    like a distribution list. A rule that guesses refuses `data-team-lead@`
    one day and is disabled the next."""
    assert Owner(
        role=OwnerRole.TECHNICAL, subject="data-team-lead@cinqcare.test", display_name="Sam Okafor"
    )


def test_two_owners_in_one_role_is_refused() -> None:
    with pytest.raises(OperationsValidationError, match="Shared accountability"):
        FeedOperations(owners=(BUSINESS, Owner(OwnerRole.BUSINESS, "x@y.test", "Someone Else")))


# ── the SLA: a timezone name, and an escalation that escalates ───────────────


def test_a_timezone_offset_is_refused_because_it_is_wrong_for_half_the_year() -> None:
    """`-05:00` is right until March, and then a roster due at 06:00 Eastern
    arrives an hour "late" every day for a week until somebody notices."""
    with pytest.raises(OperationsValidationError, match="IANA timezone name"):
        ServiceLevel(expected_by_local_time="06:00", timezone="-05:00")


def test_a_due_time_must_be_a_24_hour_local_time() -> None:
    with pytest.raises(OperationsValidationError, match="24-hour local time"):
        ServiceLevel(expected_by_local_time="6am", timezone="America/New_York")


def test_escalating_inside_the_grace_period_is_refused() -> None:
    """It would page somebody about a file that is not late yet — which is how
    a team learns that the pager means nothing."""
    with pytest.raises(OperationsValidationError, match=r"inside the .* grace period"):
        ServiceLevel(
            expected_by_local_time="06:00",
            timezone="America/New_York",
            grace_minutes=60,
            escalate_after_minutes=30,
        )


# ── the alert chain: an escalation must escalate ─────────────────────────────


def test_a_tier_that_notifies_nobody_is_refused() -> None:
    with pytest.raises(OperationsValidationError, match="notifies nobody"):
        AlertTier(after_minutes=30, channel=AlertChannel.EMAIL, notify=())


def test_tiers_must_strictly_increase() -> None:
    """Two tiers at the same minute is two pages for one event."""
    with pytest.raises(OperationsValidationError, match="must escalate"):
        FeedOperations(
            alert_chain=(
                AlertTier(30, AlertChannel.EMAIL, ("a@x.test",)),
                AlertTier(30, AlertChannel.PAGER, ("b@x.test",)),
            )
        )


def test_a_chain_that_always_pages_the_same_person_is_not_an_escalation() -> None:
    """The incident. Three tiers, all to the on-call inbox — which is the same
    alert sent three times, and every one of them ignored by the same asleep
    person."""
    with pytest.raises(OperationsValidationError, match="escalation"):
        FeedOperations(
            alert_chain=(
                AlertTier(30, AlertChannel.EMAIL, ("sam@x.test",)),
                AlertTier(60, AlertChannel.PAGER, ("sam@x.test",)),
            )
        )


# ── volumes: the incident this field exists for ──────────────────────────────


def test_a_delivery_far_below_the_typical_count_is_not_normal() -> None:
    """A roster with 40 members instead of 40,000 loads cleanly and passes
    every gate. Nine days of a wrong membership report later, somebody
    notices — because nobody had said what normal was."""
    volume = VolumeExpectation(typical_records=40_000, tolerance_percent=20)
    assert volume.is_normal(39_000)
    assert not volume.is_normal(40)


def test_an_impossible_volume_range_is_refused() -> None:
    with pytest.raises(OperationsValidationError, match="no delivery could be normal"):
        VolumeExpectation(minimum_records=1000, maximum_records=10)


def test_a_zero_tolerance_would_alert_on_every_delivery() -> None:
    with pytest.raises(OperationsValidationError, match="alert on"):
        VolumeExpectation(typical_records=100, tolerance_percent=0)


# ── Law 1, applied to the DATA in a registry row ─────────────────────────────


def test_an_endpoint_reference_may_not_be_a_location() -> None:
    """ "All environment difference lives in the connection profile."

    A host here would make this registry row environment-specific, and the
    registry un-promotable — which is the property Wave 0 exists to have.
    """
    with pytest.raises(OperationsValidationError, match="looks like a location"):
        FeedOperations(endpoint_ref="sftp://files.fidelis.example.com/roster")


def test_a_document_link_carrying_a_credential_is_refused() -> None:
    """A registry row is read by more people than any config file, and a
    shared secret in one is a secret in everybody's browser history."""
    with pytest.raises(OperationsValidationError, match="carries a credential"):
        LinkedDocument(
            kind=DocumentKind.COMPANION_GUIDE,
            label="Fidelis 834 companion guide",
            reference="https://files.example.test/guide.pdf?sig=abc123",
        )
    with pytest.raises(OperationsValidationError, match="carries a credential"):
        LinkedDocument(
            kind=DocumentKind.SPECIFICATION,
            label="Spec",
            reference="https://user:hunter2@files.example.test/spec.pdf",
        )


def test_an_ordinary_document_link_is_stored_as_given() -> None:
    """A URI here is DATA, as legitimate as `landing_path` is. Law 1 is about
    what the CODE contains."""
    doc = LinkedDocument(
        kind=DocumentKind.RUNBOOK,
        label="RB-04 onboard a feed",
        reference="https://wiki.example.test/runbooks/RB-04",
    )
    assert doc.reference.endswith("RB-04")


# ── the checklist ────────────────────────────────────────────────────────────


def test_a_complete_envelope_is_ready() -> None:
    assert readiness("fidelis-downstate-roster", _complete()).is_ready


def test_an_empty_envelope_names_everything_that_is_missing() -> None:
    result = readiness("fidelis-downstate-roster", FeedOperations())
    assert not result.is_ready
    keys = {item.key for item in result.outstanding}
    assert keys == {
        "business_owner",
        "technical_owner",
        "arrival_sla",
        "alert_chain",
        "expected_volume",
        "endpoint",
        "source",
    }


def test_every_outstanding_item_says_why_it_matters_and_what_to_do() -> None:
    """A checklist that says only "owner is required" gets
    `data@company.com` typed into it. The other two strings are the point."""
    for item in readiness("f", FeedOperations()).outstanding:
        assert item.question.endswith("?"), item.key
        assert len(item.why_it_matters) > 40, item.key
        assert item.how_to_fix, item.key


def test_a_manual_upload_is_not_asked_for_an_endpoint_or_an_arrival_sla() -> None:
    """Somebody drags a file in when they remember. There is no endpoint to
    collect from and no arrival time the platform could enforce, and demanding
    one would make people invent an SLA nobody intends to meet."""
    manual = FeedOperations(
        source_id="internal-ops",
        delivery_method=DeliveryMethod.MANUAL_UPLOAD,
        owners=(BUSINESS, TECHNICAL),
        volume=VolumeExpectation(typical_records=500),
    )
    assert readiness("manual-feed", manual).is_ready


def test_an_on_demand_calendar_is_flagged_for_an_automated_feed() -> None:
    """A feed that arrives when somebody asks cannot be Missing — so an
    automated feed declaring one has an SLA that can never fire."""
    envelope = _complete(
        service_level=ServiceLevel(
            expected_by_local_time="06:00",
            timezone="America/New_York",
            calendar=DeliveryCalendar.ON_DEMAND,
        )
    )
    outstanding = {item.key for item in readiness("f", envelope).outstanding}
    assert "calendar" in outstanding


# ── the lifecycle gate ───────────────────────────────────────────────────────


def _feed(operations: dict | None) -> GovernedObject:  # type: ignore[type-arg]
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id="fidelis-downstate-roster",
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=__import__("datetime").datetime(2026, 8, 30, tzinfo=__import__("datetime").UTC),
        body={"domain": "membership", "operations": operations or {}},
    )


def test_a_feed_nobody_could_operate_cannot_be_submitted() -> None:
    """ "activation blocked without SLA/owner with plain-language checklist"

    Refused by the ENGINE, so every path to submission is gated — not by the
    route, which is one of several ways in.
    """
    with pytest.raises(ActivationBlockedError) as refused:
        submit(_feed(None), actor=BA)

    message = str(refused.value)
    assert "cannot be activated yet" in message
    assert "Who in the business owns this feed?" in message
    assert "Why it matters:" in message and "To fix:" in message


def test_a_ready_feed_submits() -> None:
    moved, entry = submit(_feed(_complete().as_body()), actor=BA)
    assert moved.lifecycle_state is LifecycleState.PENDING_REVIEW
    assert entry.to_state is LifecycleState.PENDING_REVIEW


def test_the_gate_is_total_over_object_types() -> None:
    """`readiness_of` is called for EVERY submission, so a version that raised
    on a contract would make the lifecycle's behaviour depend on which type
    happened to be passed."""
    for object_type in ObjectType:
        obj = GovernedObject(
            object_type=object_type,
            object_id="x",
            version=1,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=BA,
            created_ts=__import__("datetime").datetime(
                2026, 8, 30, tzinfo=__import__("datetime").UTC
            ),
            body={},
        )
        result = readiness_of(obj)
        assert result.is_ready is (object_type is not ObjectType.FEED)


# ── round-tripping ───────────────────────────────────────────────────────────


def test_an_envelope_round_trips_through_its_body() -> None:
    envelope = _complete(
        documents=(
            LinkedDocument(DocumentKind.RUNBOOK, "RB-04", "https://wiki.example.test/RB-04"),
        )
    )
    assert FeedOperations.from_body(envelope.as_body()) == envelope


def test_a_feed_registered_before_this_story_reads_as_an_empty_envelope() -> None:
    """Not an error. Those feeds are not broken — they are feeds whose
    envelope nobody has filled in, which is exactly what the checklist says."""
    assert FeedOperations.from_body(None) == FeedOperations()
    assert not readiness("legacy-feed", FeedOperations.from_body(None)).is_ready
