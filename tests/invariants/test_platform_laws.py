"""The platform's laws, as property tests.

    "LAYER 4, the platform laws as property tests:
       rows_in == rows_out + quarantined + attributed_drops
       same file twice  -> skipped, audited, never duplicated
       restart-from-stage -> no duplicates, no skips
     These are universally-quantified claims. Example-based tests cannot
     express them; Hypothesis can."
    — proposals/wave-0-stack, requirements/dev.txt

The distinction is not pedantry. An example test says "these 22,000 rows
balanced". A property test says "no arrangement of counts can balance without
being attributed", and it goes looking for the counterexample.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cinqflow.core.landing import LandingOutcome, RegisteredFeed, classify
from cinqflow.core.model.vocabulary import ErrorCategory, Layer
from cinqflow.core.recon import (
    DropReason,
    StageReconciliation,
    UnattributedDropError,
    error_id_hash,
    reconcile,
)
from cinqflow.ports.compute_job import JobRun, StageResult
from cinqflow.ports.storage import FileRef

pytestmark = pytest.mark.invariant

counts = st.integers(min_value=0, max_value=10_000_000)


# ── law 1 · the balance equation ─────────────────────────────────────────────
@given(out=counts, quarantined=counts, drops=counts)
@settings(max_examples=200)
def test_a_stage_balances_exactly_when_the_equation_holds(
    out: int, quarantined: int, drops: int
) -> None:
    """ "rows_in == rows_out + quarantined + attributed_drops, every stage,
    every batch"

    Stated as a property, this says: there is no arrangement of counts for
    which `balances` disagrees with the arithmetic.
    """
    stage = StageReconciliation(
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        records_in=out + quarantined + drops,
        records_out=out,
        quarantined=quarantined,
        drops=(
            (DropReason(rule_id="DQ-002", reason="null first name", record_count=drops),)
            if drops
            else ()
        ),
    )
    assert stage.balances is True
    assert stage.unexplained == 0
    assert reconcile(stage) is stage


@given(records_in=counts, out=counts, quarantined=counts)
@settings(max_examples=200)
def test_any_unattributed_difference_is_refused(
    records_in: int, out: int, quarantined: int
) -> None:
    """The law that makes silent row loss structurally impossible.

    For EVERY arrangement where the numbers do not add up, reconciliation
    raises. There is no gap size small enough to pass, which is the point:
    incident #2's roster understatement was a small, plausible-looking gap.
    """
    stage = StageReconciliation(
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        records_in=records_in,
        records_out=out,
        quarantined=quarantined,
    )
    if records_in == out + quarantined:
        assert reconcile(stage) is stage
    else:
        with pytest.raises(UnattributedDropError):
            reconcile(stage)


@given(
    drops=st.lists(
        st.tuples(st.sampled_from(["DQ-002", "DQ-014", "STRUCTURE"]), counts),
        min_size=1,
        max_size=6,
    )
)
def test_the_ledger_total_always_equals_the_sum_of_its_reasons(
    drops: list[tuple[str, int]],
) -> None:
    """No drop can be counted in the total without appearing as a reason —
    which is what "every excluded record is attributed to the specific rule
    that excluded it" means arithmetically."""
    reasons = tuple(
        DropReason(rule_id=rule, reason=f"excluded by {rule}", record_count=count)
        for rule, count in drops
    )
    total = sum(count for _, count in drops)
    stage = StageReconciliation(
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        records_in=total,
        records_out=0,
        drops=reasons,
    )
    assert stage.attributed_drops == total
    assert stage.balances is True


# ── law 2 · the same file twice is skipped, audited, never duplicated ────────
@given(
    filename=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Nd"), whitelist_characters="_."),
        min_size=1,
        max_size=40,
    ),
    size=st.integers(min_value=0, max_value=50_000_000),
)
@settings(max_examples=200)
def test_a_seen_fingerprint_is_always_skipped_and_always_audited(filename: str, size: int) -> None:
    """ "the same file presented twice is skipped, with an audit entry"

    For ANY filename and ANY size — including names that would otherwise be
    rejected. Order is a guarantee: re-judging a file the platform has already
    loaded means a pattern change could reject something Bronze already holds,
    and then the drop ledger and Bronze disagree permanently.
    """
    from datetime import UTC, datetime

    decision = classify(
        FileRef(
            key=f"enrollments/fidelis_downstate/roster/incoming/2026-08-01/{filename}",
            size_bytes=size,
            modified_ts=datetime(2026, 8, 1, tzinfo=UTC),
            fingerprint="sha256-seen-before",
        ),
        feeds=(_FIDELIS,),
        fingerprint_seen=True,
    )
    assert decision.outcome is LandingOutcome.SKIPPED
    assert decision.audit_required is True


@given(
    filename=st.text(min_size=1, max_size=40).filter(lambda s: "/" not in s),
    size=st.integers(min_value=0, max_value=50_000_000),
)
@settings(max_examples=200)
def test_every_arriving_file_is_registered_whatever_happens_to_it(filename: str, size: int) -> None:
    """ "100% of arriving files have a registry entry" — the measurable bar.

    Accepted, rejected, unexpected or skipped: all four register. There is no
    fifth outcome, and no outcome that registers nothing. "Nothing disappears
    silently" is a property, not an intention.
    """
    from datetime import UTC, datetime

    for seen in (True, False):
        decision = classify(
            FileRef(
                key=f"enrollments/fidelis_downstate/roster/incoming/2026-08-01/{filename}",
                size_bytes=size,
                modified_ts=datetime(2026, 8, 1, tzinfo=UTC),
                fingerprint="sha256-x",
            ),
            feeds=(_FIDELIS,),
            fingerprint_seen=seen,
        )
        assert decision.registered is True
        assert decision.outcome in set(LandingOutcome)


# ── law 3 · restart resumes from the last completed stage ────────────────────
@given(completed=st.lists(st.sampled_from(list(Layer)), max_size=6, unique=True))
def test_restart_resumes_after_the_last_completed_stage_never_before_it(
    completed: list[Layer],
) -> None:
    """ "restart resumes from the last completed stage: no duplicates, no skips"

    NO DUPLICATES: the resume point is strictly after the last completed stage,
    so a completed stage is never re-run — which matters because Bronze is
    append-only and a re-run would either duplicate or be refused.
    NO SKIPS: the resume point is the IMMEDIATE next stage, never further on.
    """
    ordered = sorted(completed, key=lambda layer: list(Layer).index(layer))
    run = JobRun(
        run_id="r",
        batch_id="8842",
        completed_stages=tuple(ordered),
        results=tuple(
            StageResult(
                stage=stage,
                records_in=0,
                records_out=0,
                quarantined=0,
                attributed_drops=0,
                duration_ms=0,
            )
            for stage in ordered
        ),
    )
    resume = run.resume_from

    if not ordered:
        assert resume is Layer.LANDING, "with nothing completed, restart starts at the beginning"
        return

    last = ordered[-1]
    if last is list(Layer)[-1]:
        assert resume is None, "nothing follows the final stage"
        return

    assert resume is Layer.after(last)
    assert resume not in ordered, "a completed stage is never re-run"
    assert list(Layer).index(resume) == list(Layer).index(last) + 1, "no stage is skipped"


# ── law 4 · the deterministic error hash ─────────────────────────────────────
@given(
    batch_id=st.text(min_size=1, max_size=12),
    record_key=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    rule_id=st.one_of(st.none(), st.text(min_size=1, max_size=12)),
    stage=st.sampled_from(list(Layer)),
    category=st.sampled_from(list(ErrorCategory)),
)
@settings(max_examples=300)
def test_the_error_hash_is_a_pure_function_of_its_five_components(
    batch_id: str,
    record_key: str | None,
    rule_id: str | None,
    stage: Layer,
    category: ErrorCategory,
) -> None:
    """Idempotent at the error level: reprocessing a corrected batch cannot
    manufacture duplicate incidents, for ANY error."""
    arguments = {
        "batch_id": batch_id,
        "stage": stage,
        "record_key": record_key,
        "error_type": category,
        "rule_id": rule_id,
    }
    first = error_id_hash(**arguments)  # type: ignore[arg-type]
    assert first == error_id_hash(**arguments)  # type: ignore[arg-type]
    assert len(first) == 32


@given(
    left=st.text(min_size=1, max_size=8),
    right=st.text(min_size=1, max_size=8),
)
@settings(max_examples=300)
def test_two_different_errors_never_share_a_hash(left: str, right: str) -> None:
    """A collision would merge two incidents into one and under-report the
    drop ledger. The separator matters: without it, ("ab","c") and ("a","bc")
    would hash identically."""
    from hypothesis import assume

    assume(left != right)
    make = lambda key: error_id_hash(  # noqa: E731
        batch_id="8842",
        stage=Layer.SILVER_RAW,
        record_key=key,
        error_type=ErrorCategory.VALIDATION,
        rule_id="DQ-002",
    )
    assert make(left) != make(right)


_FIDELIS = RegisteredFeed(
    feed_id="fidelis-downstate-roster",
    feed_version=1,
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.xlsx",
    file_format="xlsx",
    min_size_bytes=5_000_000,
    max_size_bytes=30_000_000,
)
