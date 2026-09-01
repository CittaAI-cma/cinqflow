"""CF-V3-E6-05 — claim lineage derivation, the BCDA FHIR v4 half.

    "Structural transforms for the hard structures: flattening FHIR
     ExplanationOfBenefit JSON... and claim lineage derivation (original /
     adjustment / cancellation) with payment sign normalization, so that
     the formats that carry the most clinical and financial meaning...
     flow through the same governed pipeline as simple files."
    "Derive claim lineage from FHIR relationships exactly per the
     documented rules (no related claim = original; replaced-by =
     cancellation; prior = adjustment) and normalize payment signs
     accordingly."
    "Append claim events immutably — prior versions are never updated in
     place; the latest state is derived."
    — CF-V3-E6-05

THE RULES ARE HARVESTED, NOT INVENTED. CCLF carried `CLM_ADJSMT_TYPE_CD`
directly (0/1/2 = original/cancellation/adjustment); BCDA FHIR v4 dropped it.
`clientdata/Uploads/2-Design/DataModel/Claims/BCDA Handling Claim
Adjustments and Claim Status.docx` is CINQCARE's own team's derived
replacement — sections 7, 8 and 12 give the exact relationship-code table and
the exact payment-sign table below, and section 5's worked example (claim
A100: original $500, cancellation -$500, adjustment $450, net $450) is the
one this module's own tests reproduce byte-for-byte.

WHY THIS IS NOT A `core.mapping` TransformKind. Every kind in that taxonomy
maps ONE target field from ONE row: read a value, transform it, write it.
Lineage derivation reads a whole CLAIM FAMILY — every event that shares an
ancestor — and decides, from the family, which one is latest and what the
family's net financial position is. That is a different shape of problem
(BATCH-shaped, not ROW-shaped), and forcing it into `Transform.kind` would
mean either lying about what a "row" is or growing the taxonomy an escape
hatch it was deliberately built without. See `core/relationship_integrity.py`
for the same reasoning applied to a different check.

CLAIM STATUS AND CLAIM LINEAGE ARE NOT THE SAME THING (doc section 9). Status
(`EOB.status`: active / cancelled / entered-in-error) is the claim's own
operational state; lineage (`EOB.related.relationship`) is its position in a
chain of claim versions. `ClaimEvent` carries both, separately, because a
cancellation claim's OWN status is often still "cancelled" while an
adjustment's status is "active" — collapsing them loses exactly the
distinction the source document calls "the key difference."
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum, unique
from typing import Any


class ClaimLineageError(RuntimeError):
    """A claim event carried a relationship the documented rules do not cover."""


class UnknownRelationshipCodeError(ClaimLineageError):
    """`related.relationship.coding.code` was something other than the three
    values BCDA Handling §7/§12 name (`prior`, `replaces`, `replacedby`) or
    absent.

    Raised rather than guessed at, for the same reason an unlisted LOOKUP
    code quarantines a row in `core.mapping`: a fourth relationship code is a
    fact about the feed nobody has reviewed yet, not something to silently
    bucket as ORIGINAL.
    """


@unique
class ClaimAdjustmentType(StrEnum):
    """The CCLF `CLM_ADJSMT_TYPE_CD` replacement — derived, not provided.

    BCDA Handling §7 recommends a fourth and fifth signal (`CANCELLED` from
    `status=cancelled`, `REVERSAL` from "payment reversal detected"). Neither
    is modelled here: `status` is already carried on `ClaimEvent` untouched
    (§9's own point — status is not lineage), and "payment reversal
    detected" is circular against the very payment this type decides how to
    sign. §12's three-value table is the one the recommended Silver mapping
    (§11) and the worked example (§5/§8) both actually use, so it is the one
    this enum holds.
    """

    ORIGINAL = "original"
    ADJUSTMENT = "adjustment"
    CANCELLATION = "cancellation"


#: BCDA Handling §12, "Recommended Enhancement for claim_adjustment_type_cd" —
#: verbatim. `None` is the "no related claim" row from §7.
_DERIVED_ADJUSTMENT_TYPE: dict[str | None, ClaimAdjustmentType] = {
    None: ClaimAdjustmentType.ORIGINAL,
    "prior": ClaimAdjustmentType.ADJUSTMENT,
    "replaces": ClaimAdjustmentType.ADJUSTMENT,
    "replacedby": ClaimAdjustmentType.CANCELLATION,
}


def derive_adjustment_type(relationship_code: str | None) -> ClaimAdjustmentType:
    """§7/§12's table, as a function. `relationship_code` is
    `EOB.related[0].relationship.coding[0].code`, or `None` when the claim
    carries no `related` entry at all."""
    try:
        return _DERIVED_ADJUSTMENT_TYPE[relationship_code]
    except KeyError:
        raise UnknownRelationshipCodeError(
            f"{relationship_code!r} is not a relationship code the documented rules cover "
            "(expected one of: no related claim, 'prior', 'replaces', 'replacedby') — a claim "
            "with an unreviewed relationship code is a fact to surface, not a guess to make"
        ) from None


def normalize_payment(adjustment_type: ClaimAdjustmentType, raw_payment: Decimal) -> Decimal:
    """BCDA Handling §8, "Recommended Financial Normalization Logic" —
    verbatim: ORIGINAL and ADJUSTMENT keep the payer's sign; CANCELLATION
    negates it. The worked example is this module's own test:
    500 / 500 / 450 raw -> 500 / -500 / 450 normalized, net 450."""
    if adjustment_type is ClaimAdjustmentType.CANCELLATION:
        return -raw_payment
    return raw_payment


@dataclass(frozen=True)
class ClaimEvent:
    """One `ExplanationOfBenefit` resource, flattened. Immutable by
    construction — BCDA Handling §15's own "DO NOT update prior claims
    in-place" is enforced here simply by there being no field to set."""

    claim_id: str
    parent_claim_id: str | None
    relationship_code: str | None
    status: str
    raw_payment: Decimal


def flatten_eob(resource: Mapping[str, Any]) -> ClaimEvent:
    """One FHIR `ExplanationOfBenefit` JSON resource -> its flat claim
    event fields, per BCDA Handling §11's "Recommended Mapping Into Your
    Existing Model": `source_claim_id <- EOB.id`, `replaced_claim_id <-
    EOB.related.reference`, `claim_status <- EOB.status`. Only the FIRST
    `related` entry is read — every worked example in the source document
    (§5's three-claim lifecycle) carries at most one, and a claim citing
    two unrelated predecessors is exactly the kind of shape nobody has
    reviewed yet, so `flatten_eob` takes the one the samples show rather
    than guess how a second would combine with it.
    """
    resource_id = resource.get("id")
    if not resource_id:
        raise ClaimLineageError("an ExplanationOfBenefit resource with no id flattens to nothing")

    related_entries = resource.get("related") or ()
    relationship_code: str | None = None
    parent_claim_id: str | None = None
    if related_entries:
        first = related_entries[0]
        codings = first.get("relationship", {}).get("coding", ())
        relationship_code = codings[0].get("code") if codings else None
        parent_claim_id = first.get("reference", {}).get("identifier", {}).get("value")

    amount = resource.get("payment", {}).get("amount", {}).get("value")
    return ClaimEvent(
        claim_id=str(resource_id),
        parent_claim_id=parent_claim_id,
        relationship_code=relationship_code,
        status=str(resource.get("status", "")),
        raw_payment=Decimal(str(amount)) if amount is not None else Decimal(0),
    )


@dataclass(frozen=True)
class ClaimLineageRecord:
    """§13's "Recommended Silver Claim Lifecycle Representation" table, as a
    row: one claim event, its derived type, its normalized payment, and
    whether it is the family's current state."""

    claim_id: str
    parent_claim_id: str | None
    adjustment_type: ClaimAdjustmentType
    claim_status: str
    raw_payment: Decimal
    normalized_payment: Decimal
    is_latest_version: bool


def derive_lineage(events: Sequence[ClaimEvent]) -> tuple[ClaimLineageRecord, ...]:
    """One claim family, in event order -> its derived lineage.

    `events` is the whole family sharing an ancestor, not one claim alone —
    exactly why this cannot be a per-row `Transform`. Each event is derived
    independently (its type and normalized payment depend only on itself);
    `is_latest_version` is the one property that depends on the family, and
    §14's "Golden View Logic" names it precisely: "Latest active
    non-cancelled claim version."
    """
    derived = tuple(
        ClaimLineageRecord(
            claim_id=event.claim_id,
            parent_claim_id=event.parent_claim_id,
            adjustment_type=(adjustment_type := derive_adjustment_type(event.relationship_code)),
            claim_status=event.status,
            raw_payment=event.raw_payment,
            normalized_payment=normalize_payment(adjustment_type, event.raw_payment),
            is_latest_version=False,
        )
        for event in events
    )
    latest = _latest_active(derived)
    if latest is None:
        return derived
    return tuple(
        replace(record, is_latest_version=True) if record.claim_id == latest.claim_id else record
        for record in derived
    )


def _latest_active(records: Sequence[ClaimLineageRecord]) -> ClaimLineageRecord | None:
    active = [
        record
        for record in records
        if record.claim_status != "cancelled"
        and record.adjustment_type is not ClaimAdjustmentType.CANCELLATION
    ]
    return active[-1] if active else None


def net_position(records: Sequence[ClaimLineageRecord]) -> Decimal:
    """§14's "Financial Net Position": `SUM(normalized_payment_amount)`."""
    total = Decimal(0)
    for record in records:
        total += record.normalized_payment
    return total
