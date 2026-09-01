"""CF-V3-E10-03 — one batch's Silver ODS publication decision, governed.

"Downstream never sees an uncertified batch."
— CF-V3-E10-03
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from cinqflow.core.certification import Certification, Check, CheckKind, Verdict
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_batch_certification import (
    BatchCertificationRecord,
    CheckSummary,
    UncertifiedDraftError,
    as_governed,
    from_certification,
    from_governed,
)

pytestmark = pytest.mark.unit

AUTHOR = Actor(subject="cinqflow.ods_certification", actor_type=ActorType.SYSTEM, display_name="")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _passing_certification(verdict: Verdict = Verdict.CERTIFIED) -> Certification:
    return Certification(
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        verdict=verdict,
        checks=(
            Check(kind=CheckKind.BALANCE, passed=True, evidence="ok"),
            Check(
                kind=CheckKind.RELATIONSHIP_INTEGRITY,
                passed=True,
                evidence="1000 row(s) checked, none orphaned",
            ),
        ),
        derived_ts=NOW,
        as_of=NOW.date(),
    )


def test_a_certified_batch_produces_a_draftable_record() -> None:
    record = from_certification(_passing_certification(), model_version="1")
    assert record.batch_id == "batch-8842"
    assert record.verdict == "Certified"
    assert len(record.checks) == 2


def test_a_certified_with_waiver_batch_is_also_draftable() -> None:
    record = from_certification(
        _passing_certification(Verdict.CERTIFIED_WITH_WAIVER), model_version="1"
    )
    assert record.verdict == "Certified-with-Waiver"


def test_an_uncertified_batch_refuses_to_become_a_record() -> None:
    """ "Downstream never sees an uncertified batch" — enforced at
    construction, not trusted to whichever caller drafts one."""
    with pytest.raises(UncertifiedDraftError, match="batch-8842"):
        BatchCertificationRecord(
            batch_id="batch-8842",
            feed_id="fidelis-downstate-roster",
            model_version="1",
            verdict=Verdict.NOT_CERTIFIED.value,
            checks=(),
        )


def test_a_pending_batch_also_refuses() -> None:
    with pytest.raises(UncertifiedDraftError):
        BatchCertificationRecord(
            batch_id="batch-8842",
            feed_id="fidelis-downstate-roster",
            model_version="1",
            verdict=Verdict.PENDING.value,
            checks=(),
        )


def test_the_governed_round_trip_preserves_every_check() -> None:
    record = from_certification(_passing_certification(), model_version="1")
    governed = as_governed(record, author=AUTHOR, created_ts=NOW)
    assert governed.object_type is ObjectType.ODS_BATCH_CERTIFICATION
    assert governed.object_id == "batch-8842"
    assert governed.lifecycle_state is LifecycleState.DRAFT
    assert governed.version == 1

    round_tripped = from_governed(governed)
    assert round_tripped == record


def test_from_governed_refuses_the_wrong_object_type() -> None:
    record = from_certification(_passing_certification(), model_version="1")
    governed = as_governed(record, author=AUTHOR, created_ts=NOW)
    wrong = replace(governed, object_type=ObjectType.MAPPING)
    with pytest.raises(Exception, match="not a batch certification"):
        from_governed(wrong)


def test_check_summary_is_frozen_and_comparable() -> None:
    a = CheckSummary(kind="balance", passed=True, evidence="ok")
    b = CheckSummary(kind="balance", passed=True, evidence="ok")
    assert a == b
