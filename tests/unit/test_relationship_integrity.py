"""CF-V3-E10-03 — relationship validation, as data.

"Validate relationships as data (orphaned claims, dangling provider
 references) with counts and examples."
"Exception — Given 0.3% of claims reference members absent from the
 member table, when the gate runs, then publication holds, the
 orphaned claims are listed with their source batches."
— CF-V3-E10-03
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.certification import Certification, Check, CheckKind, Verdict, certify
from cinqflow.core.relationship_integrity import check_relationship

pytestmark = pytest.mark.unit

_PASSING_MANDATORY_CHECKS = tuple(
    Check(kind=kind, passed=True, evidence="ok")
    for kind in (CheckKind.BALANCE, CheckKind.DROP_LEDGER, CheckKind.RECONCILIATION)
)


def test_a_relationship_with_no_orphans_passes_with_a_real_count() -> None:
    result = check_relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=1000,
        orphan_rows=(),
    )
    assert result.passed
    assert result.orphan_count == 0
    assert "1000 row(s) checked, none orphaned" in result.evidence()


def test_orphaned_rows_are_named_with_their_source_batch() -> None:
    result = check_relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=1000,
        orphan_rows=(
            {"OurId": 5551212, "BatchId": "batch-8842"},
            {"OurId": 5551213, "BatchId": "batch-8843"},
        ),
    )
    assert not result.passed
    assert result.orphan_count == 2
    evidence = result.evidence()
    assert "2 of 1000" in evidence
    assert "5551212" in evidence and "batch-8842" in evidence
    assert "5551213" in evidence and "batch-8843" in evidence


def test_a_sampled_orphan_count_reports_the_true_total_not_the_sample_size() -> None:
    """A caller that capped its query at a handful of examples must never
    have the evidence understate a 0.3%-of-a-million problem as '3 rows'."""
    sample = ({"OurId": 1, "BatchId": "b1"}, {"OurId": 2, "BatchId": "b1"})
    result = check_relationship(
        child_entity="Claims",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=1_000_000,
        orphan_rows=sample,
        orphan_count=3000,
    )
    assert result.orphan_count == 3000
    assert len(result.orphans) == 2
    assert "3000 of 1000000" in result.evidence()


def test_a_row_with_no_batch_id_is_named_unknown_not_hidden() -> None:
    result = check_relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=10,
        orphan_rows=({"OurId": 1},),
    )
    assert "unknown batch" in result.evidence()


def test_as_check_produces_a_relationship_integrity_check() -> None:
    result = check_relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=10,
        orphan_rows=(),
    )
    check = result.as_check()
    assert check.kind is CheckKind.RELATIONSHIP_INTEGRITY
    assert check.passed


def test_a_failed_relationship_check_blocks_certification_though_never_mandatory() -> None:
    """CF-V3-E10-03's own gate: `certify()`'s existing policy already fails
    ANY completed-and-failed check, mandatory or not — proving the new
    check kind needs no change to that policy to hold publication."""
    result = check_relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
        checked=10,
        orphan_rows=({"OurId": 1, "BatchId": "b1"},),
    )
    verdict = certify(
        batch_id="b1",
        feed_id="fidelis-downstate-roster",
        checks=(*_PASSING_MANDATORY_CHECKS, result.as_check()),
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert isinstance(verdict, Certification)
    assert verdict.verdict is Verdict.NOT_CERTIFIED
    assert result.as_check() in verdict.failed
