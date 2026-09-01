"""CF-V3-E6-05 — claim lineage derivation against BCDA Handling Claim
Adjustments and Claim Status's own worked example (its §5 sample lifecycle,
§7/§8/§12 derived-rule tables, §14 golden-view logic).

    "Given a BCDA weekly file with an original, its cancellation and an
     adjustment for one claim, when processing runs, then three linked
     events land with types derived and payments +500 / -500 / +450, and
     the net-position view shows $450 — matching the documented example
     exactly."
    — CF-V3-E6-05
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from cinqflow.core.claim_lineage import (
    ClaimAdjustmentType,
    ClaimEvent,
    ClaimLineageError,
    UnknownRelationshipCodeError,
    derive_adjustment_type,
    derive_lineage,
    flatten_eob,
    net_position,
    normalize_payment,
)

pytestmark = pytest.mark.unit

# The three EOB resources verbatim from the source document's §5 "BCDA FHIR
# v4 Sample Lifecycle" — not paraphrased, not restructured.
_ORIGINAL = {
    "resourceType": "ExplanationOfBenefit",
    "id": "A100",
    "status": "active",
    "payment": {"amount": {"value": 500}},
}
_CANCELLATION = {
    "resourceType": "ExplanationOfBenefit",
    "id": "A100-CANCEL",
    "status": "cancelled",
    "related": [
        {
            "relationship": {"coding": [{"code": "replacedby"}]},
            "reference": {"identifier": {"value": "A100"}},
        }
    ],
    "payment": {"amount": {"value": 500}},
}
_ADJUSTMENT = {
    "resourceType": "ExplanationOfBenefit",
    "id": "A100-ADJ",
    "status": "active",
    "related": [
        {
            "relationship": {"coding": [{"code": "prior"}]},
            "reference": {"identifier": {"value": "A100"}},
        }
    ],
    "payment": {"amount": {"value": 450}},
}


# ── derive_adjustment_type — §7 / §12's table ────────────────────────────────


def test_no_related_claim_is_original() -> None:
    assert derive_adjustment_type(None) is ClaimAdjustmentType.ORIGINAL


def test_prior_is_adjustment() -> None:
    assert derive_adjustment_type("prior") is ClaimAdjustmentType.ADJUSTMENT


def test_replaces_is_also_adjustment() -> None:
    assert derive_adjustment_type("replaces") is ClaimAdjustmentType.ADJUSTMENT


def test_replacedby_is_cancellation() -> None:
    assert derive_adjustment_type("replacedby") is ClaimAdjustmentType.CANCELLATION


def test_an_unreviewed_relationship_code_is_refused_not_guessed() -> None:
    with pytest.raises(UnknownRelationshipCodeError, match="supersedes"):
        derive_adjustment_type("supersedes")


# ── normalize_payment — §8's table ───────────────────────────────────────────


def test_original_payment_keeps_its_sign() -> None:
    assert normalize_payment(ClaimAdjustmentType.ORIGINAL, Decimal(500)) == Decimal(500)


def test_adjustment_payment_keeps_its_sign() -> None:
    assert normalize_payment(ClaimAdjustmentType.ADJUSTMENT, Decimal(450)) == Decimal(450)


def test_cancellation_payment_is_negated() -> None:
    assert normalize_payment(ClaimAdjustmentType.CANCELLATION, Decimal(500)) == Decimal(-500)


# ── flatten_eob — §5's three resources, §11's flat-field mapping ────────────


def test_flattening_the_original_claim() -> None:
    event = flatten_eob(_ORIGINAL)
    assert event == ClaimEvent(
        claim_id="A100",
        parent_claim_id=None,
        relationship_code=None,
        status="active",
        raw_payment=Decimal(500),
    )


def test_flattening_the_cancellation_claim() -> None:
    event = flatten_eob(_CANCELLATION)
    assert event == ClaimEvent(
        claim_id="A100-CANCEL",
        parent_claim_id="A100",
        relationship_code="replacedby",
        status="cancelled",
        raw_payment=Decimal(500),
    )


def test_flattening_the_adjustment_claim() -> None:
    event = flatten_eob(_ADJUSTMENT)
    assert event == ClaimEvent(
        claim_id="A100-ADJ",
        parent_claim_id="A100",
        relationship_code="prior",
        status="active",
        raw_payment=Decimal(450),
    )


def test_a_resource_with_no_id_flattens_to_nothing() -> None:
    with pytest.raises(ClaimLineageError, match="no id"):
        flatten_eob({"resourceType": "ExplanationOfBenefit", "status": "active"})


def test_a_resource_with_no_payment_is_zero_not_a_crash() -> None:
    event = flatten_eob({"resourceType": "ExplanationOfBenefit", "id": "A200", "status": "active"})
    assert event.raw_payment == Decimal(0)


# ── derive_lineage — the whole worked example, end to end ───────────────────


def test_the_documented_worked_example_matches_exactly() -> None:
    events = (flatten_eob(_ORIGINAL), flatten_eob(_CANCELLATION), flatten_eob(_ADJUSTMENT))

    records = derive_lineage(events)

    by_id = {record.claim_id: record for record in records}
    assert by_id["A100"].adjustment_type is ClaimAdjustmentType.ORIGINAL
    assert by_id["A100"].normalized_payment == Decimal(500)
    assert by_id["A100-CANCEL"].adjustment_type is ClaimAdjustmentType.CANCELLATION
    assert by_id["A100-CANCEL"].normalized_payment == Decimal(-500)
    assert by_id["A100-ADJ"].adjustment_type is ClaimAdjustmentType.ADJUSTMENT
    assert by_id["A100-ADJ"].normalized_payment == Decimal(450)

    assert net_position(records) == Decimal(450)


def test_the_latest_version_is_the_latest_active_non_cancelled_claim() -> None:
    """§14 "Golden View Logic": Latest Claim Snapshot = latest active
    non-cancelled claim version — here, the adjustment, not the cancelled
    intermediate and not the superseded original."""
    events = (flatten_eob(_ORIGINAL), flatten_eob(_CANCELLATION), flatten_eob(_ADJUSTMENT))

    records = derive_lineage(events)

    by_id = {record.claim_id: record for record in records}
    assert by_id["A100-ADJ"].is_latest_version
    assert not by_id["A100"].is_latest_version
    assert not by_id["A100-CANCEL"].is_latest_version


def test_a_family_with_no_active_survivor_has_no_latest_version() -> None:
    """A lone cancellation event — status `cancelled` and type CANCELLATION
    both — has no active claim to call latest, and `derive_lineage` says so
    by marking nothing latest rather than picking an arbitrary one."""
    records = derive_lineage((flatten_eob(_CANCELLATION),))

    assert not any(record.is_latest_version for record in records)


def test_lineage_carries_the_parent_claim_id_through() -> None:
    events = (flatten_eob(_ORIGINAL), flatten_eob(_CANCELLATION))
    records = derive_lineage(events)
    by_id = {record.claim_id: record for record in records}
    assert by_id["A100"].parent_claim_id is None
    assert by_id["A100-CANCEL"].parent_claim_id == "A100"


def test_an_unreviewed_relationship_code_fails_the_whole_family_not_silently() -> None:
    poisoned = ClaimEvent(
        claim_id="A100-WEIRD",
        parent_claim_id="A100",
        relationship_code="supersedes",
        status="active",
        raw_payment=Decimal(10),
    )
    with pytest.raises(UnknownRelationshipCodeError):
        derive_lineage((flatten_eob(_ORIGINAL), poisoned))


# ── immutability — §15 "DO NOT update prior claims in-place" ────────────────


def test_a_claim_event_cannot_be_mutated_in_place() -> None:
    event = flatten_eob(_ORIGINAL)
    with pytest.raises(FrozenInstanceError):
        event.raw_payment = Decimal(999)  # type: ignore[misc]


def test_a_lineage_record_cannot_be_mutated_in_place() -> None:
    (record,) = derive_lineage((flatten_eob(_ORIGINAL),))
    with pytest.raises(FrozenInstanceError):
        record.is_latest_version = False  # type: ignore[misc]
