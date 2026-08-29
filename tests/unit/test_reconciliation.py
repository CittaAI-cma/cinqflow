"""CF-V0-E13-01 — the balance equation and the named-reason drop ledger."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cinqflow.core.model.vocabulary import ErrorCategory, Layer
from cinqflow.core.recon import (
    DropReason,
    ReconVerdict,
    StageReconciliation,
    UnattributedDropError,
    error_id_hash,
    reconcile,
)

pytestmark = pytest.mark.unit

INVARIANT = "rows_in == rows_out + quarantined + attributed_drops, every stage, every batch"


def _roster(**overrides: object) -> StageReconciliation:
    defaults: dict[str, object] = {
        "batch_id": "8842",
        "stage": Layer.SILVER_RAW,
        "records_in": 22_000,
        "records_out": 21_820,
        "quarantined": 175,
        "drops": (
            DropReason(rule_id="STRUCTURE", reason="rejected by structure check", record_count=5),
        ),
    }
    defaults.update(overrides)
    return StageReconciliation(**defaults)  # type: ignore[arg-type]


def test_the_story_s_worked_example_balances() -> None:
    """ "Given a roster batch processes 22,000 rows and rules exclude 180, when
    the stage completes, then reconciliation shows 22,000 in = 21,820 out + 175
    quarantined by DQ-002 + 5 rejected by structure check, and the batch is
    marked balanced." — CF-V0-E13-01, happy path"""
    stage = _roster()
    assert stage.balances is True, INVARIANT
    assert stage.unexplained == 0
    assert stage.verdict is ReconVerdict.BALANCED
    assert reconcile(stage) is stage


def test_an_unexplained_difference_fails_the_batch_loudly() -> None:
    """ "Given counts do not balance and the difference has no attributed
    reason, when reconciliation runs, then the batch is marked
    Failed-Reconciliation, downstream publication is blocked, and Operations is
    alerted with the exact stage where the imbalance appeared."

    It RAISES rather than returning a flag, because a flag is something a
    caller can forget to check — and forgetting is the whole failure mode.
    """
    stage = _roster(quarantined=0, drops=())
    assert stage.verdict is ReconVerdict.FAILED_RECONCILIATION
    with pytest.raises(UnattributedDropError) as caught:
        reconcile(stage)
    assert "silver_raw" in str(caught.value)
    assert "180" in str(caught.value)


def test_the_explanation_is_the_sentence_an_operator_needs() -> None:
    """Not a table of numbers — the sentence that ends the investigation."""
    assert _roster().explain() == (
        "22,000 in = 21,820 out + 175 quarantined + 5 rejected by structure check "
        "(STRUCTURE). Balanced."
    )


def test_an_unbalanced_explanation_names_the_stage_and_the_gap() -> None:
    text = _roster(quarantined=0, drops=()).explain()
    assert "UNBALANCED by 180" in text and "silver_raw" in text
    assert "publication is blocked" in text


@pytest.mark.parametrize("forbidden", ["other", "Other", "UNKNOWN", "n/a", "misc", "  ", ""])
def test_a_drop_category_that_is_not_a_reason_is_refused(forbidden: str) -> None:
    """ "Allow any category called 'other' or 'unknown' in the drop ledger" is a
    documented don't.

    Incident #2: member_provider silently lost rows where pcp_npi was null, and
    understated the roster. 'Other' is how silent row loss gets a LABEL instead
    of a FIX.
    """
    with pytest.raises(UnattributedDropError):
        DropReason(rule_id=forbidden, reason="something happened", record_count=10)
    with pytest.raises(UnattributedDropError):
        DropReason(rule_id="DQ-002", reason=forbidden, record_count=10)


def test_a_named_rule_is_a_reason_and_carries_its_citation() -> None:
    """Every drop opens the rule that caused it — one click, no search."""
    drop = DropReason(
        rule_id="DQ-002",
        reason="Member First Name Not Null",
        record_count=175,
        columns=("first_name",),
    )
    assert str(drop.citation) == "rule:DQ-002"


def test_a_drop_can_carry_its_financial_impact() -> None:
    """Claims reconciliation is not only about counts: a $48,000 variance is
    the shape of the Wave-2 story, and the ledger has to be able to hold it."""
    drop = DropReason(
        rule_id="DQ-041",
        reason="negative pharmacy amount",
        record_count=3,
        financial_impact=Decimal("-48000.00"),
    )
    assert drop.financial_impact == Decimal("-48000.00")


def test_a_negative_drop_count_is_refused() -> None:
    with pytest.raises(ValueError, match="not a count"):
        DropReason(rule_id="DQ-002", reason="null name", record_count=-1)


# ── the deterministic error hash ─────────────────────────────────────────────
def test_the_same_error_hashes_the_same_across_processes() -> None:
    """Python's hash() is salted per process. An identifier that changed
    between runs would defeat replay-safety entirely."""
    arguments = {
        "batch_id": "8842",
        "stage": Layer.SILVER_RAW,
        "record_key": "MBR000042",
        "error_type": ErrorCategory.VALIDATION,
        "rule_id": "DQ-002",
    }
    assert error_id_hash(**arguments) == error_id_hash(**arguments)  # type: ignore[arg-type]
    assert error_id_hash(**arguments) == (  # type: ignore[arg-type]
        error_id_hash(
            batch_id="8842",
            stage=Layer.SILVER_RAW,
            record_key="MBR000042",
            error_type=ErrorCategory.VALIDATION,
            rule_id="DQ-002",
        )
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"batch_id": "8843"},
        {"stage": Layer.BRONZE},
        {"record_key": "MBR000043"},
        {"error_type": ErrorCategory.SCHEMA},
        {"rule_id": "DQ-003"},
    ],
)
def test_every_component_of_the_hash_changes_it(changed: dict[str, object]) -> None:
    """A component that did not affect the hash would collapse distinct errors
    into one, and the drop ledger would under-report."""
    base: dict[str, object] = {
        "batch_id": "8842",
        "stage": Layer.SILVER_RAW,
        "record_key": "MBR000042",
        "error_type": ErrorCategory.VALIDATION,
        "rule_id": "DQ-002",
    }
    assert error_id_hash(**base) != error_id_hash(**{**base, **changed})  # type: ignore[arg-type]


def test_a_record_level_error_and_a_stage_error_hash_differently() -> None:
    """A stage failure has no record key. It must not collide with the first
    record that happened to fail there."""
    stage_level = error_id_hash(
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        record_key=None,
        error_type=ErrorCategory.SYSTEM,
        rule_id=None,
    )
    record_level = error_id_hash(
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        record_key="MBR000042",
        error_type=ErrorCategory.SYSTEM,
        rule_id=None,
    )
    assert stage_level != record_level
