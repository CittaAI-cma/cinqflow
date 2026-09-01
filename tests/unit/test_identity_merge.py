"""CF-V3-E9-03 — merge/split: R4, human-always, verified.

    "for each potential merge (two records, one person) or split (one record,
     two people), an AI-prepared evidence card ... and a preview of exactly
     which records would repoint or separate, with the decision itself always
     mine"
    "Preview consequences concretely: which addresses repoint, which
     duplicates collapse, which identifier is marked merged — per the
     documented scenarios."
    "Execute only on explicit steward approval, then verify the post-change
     state matches the preview and report any difference."
    "Detect flip-flop patterns (identities oscillating between states) and
     surface the offending source."
    "Given Verato reports L200 merged into L100, when the steward opens the
     card, then they see both profiles, the preview ('2 addresses repoint, 1
     duplicate collapses, C2 marked merged-to-C1'), approve, and the
     post-change verification confirms the outcome matches."
    "Execute any merge or split automatically, at any confidence, ever — this
     is a human decision by policy, not by threshold." (a documented don't)
    "Hide any affected record from the preview." (a documented don't)
    — CF-V3-E9-03

    "1. Normal update ... 2. Merge (L200 -> L100). C2 is marked MERGED_TO_C1;
     C1 stays ACTIVE. The satellite rows repoint and dedup — two identical
     addresses collapse to one."
    — memory/05-ground-truth/01-canonical-model.md, Verato Scenarios.docx

THE DESIGN DECISION UNDER TEST: the preview and the post-change verification
are the SAME FUNCTION run twice — `plan_merge()` before, `plan_merge()` again
after, expecting nothing left to repoint or collapse. "Post-change
verification matches the preview in 100% of executed decisions" is therefore
a STRUCTURAL property of re-running one pure function, never two
implementations that could drift apart.

DEMOGRAPHIC COMPARISON NEVER CARRIES A RAW VALUE OUT OF THIS MODULE. Its
output is match/differs/similar per field — the categorical fact an AI
narrator is grounded in — never the name or the date of birth itself. "Send
unmasked PHI to any model" is a platform-wide don't; this is where that
becomes structurally true for the merge evidence card specifically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.identity.merge import (
    FieldComparison,
    IdentityEvent,
    IdentityEventKind,
    MergeError,
    PreviewMismatchError,
    SatelliteRow,
    UnapprovedMergeExecutionError,
    compare_demographics,
    detect_flip_flop,
    execute_merge,
    plan_merge,
    verify_post_change,
)

pytestmark = pytest.mark.unit


def _row(entity: str, record_id: str, owner: str, content_key: str) -> SatelliteRow:
    return SatelliteRow(
        entity=entity, record_id=record_id, owner_member_id=owner, content_key=content_key
    )


# ── plan_merge — the worked example, verbatim ────────────────────────────────


def test_the_worked_example_previews_two_repoints_and_one_collapse() -> None:
    """ "2 addresses repoint, 1 duplicate collapses, C2 marked merged-to-C1" —
    the ground-truth scenario, proven rather than paraphrased."""
    merged_away_rows = (
        _row("Members_Addresses", "A1", "C2", "123 Main St|Albany|NY"),
        _row("Members_Addresses", "A2", "C2", "456 Oak Ave|Albany|NY"),
        _row("Members_Addresses", "A3", "C2", "789 Elm St|Albany|NY"),
    )
    survivor_rows = (_row("Members_Addresses", "A9", "C1", "789 Elm St|Albany|NY"),)

    plan = plan_merge(
        merged_away_member_id="C2",
        survivor_member_id="C1",
        merged_away_rows=merged_away_rows,
        survivor_rows=survivor_rows,
    )

    assert len(plan.repoints) == 2
    assert len(plan.collapses) == 1
    assert plan.collapses[0].collapsed_record_id == "A3"
    assert plan.collapses[0].kept_record_id == "A9"
    assert plan.marked_merged == "C2"
    assert plan.survivor_member_id == "C1"


def test_the_preview_names_every_affected_record_none_hidden() -> None:
    """ "Hide any affected record from the preview" is the documented don't —
    every merged-away row appears in EITHER repoints OR collapses, never
    neither."""
    merged_away_rows = (
        _row("Members_Addresses", "A1", "C2", "x"),
        _row("Members_Emails", "E1", "C2", "y"),
    )
    plan = plan_merge(
        merged_away_member_id="C2",
        survivor_member_id="C1",
        merged_away_rows=merged_away_rows,
        survivor_rows=(),
    )
    assert plan.affected_record_ids() == {"A1", "E1"}


def test_a_merge_with_no_satellite_rows_is_a_legitimate_empty_plan() -> None:
    plan = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=(), survivor_rows=()
    )
    assert plan.repoints == ()
    assert plan.collapses == ()
    assert plan.affected_record_ids() == set()


def test_merging_a_member_into_itself_is_refused() -> None:
    with pytest.raises(MergeError, match="itself"):
        plan_merge(
            merged_away_member_id="C1",
            survivor_member_id="C1",
            merged_away_rows=(),
            survivor_rows=(),
        )


def test_the_plan_fingerprints_its_own_shape() -> None:
    rows = (_row("Members_Addresses", "A1", "C2", "x"),)
    a = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=rows, survivor_rows=()
    )
    b = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=rows, survivor_rows=()
    )
    assert a.fingerprint == b.fingerprint


# ── execute — R4, human-always ───────────────────────────────────────────────


def test_execution_without_a_steward_approval_id_is_refused() -> None:
    """ "Execute any merge or split automatically, at any confidence, ever —
    this is a human decision by policy, not by threshold." No confidence
    parameter exists on this function for exactly that reason."""
    plan = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=(), survivor_rows=()
    )
    with pytest.raises(UnapprovedMergeExecutionError):
        execute_merge(plan, steward_approval_id=None)


def test_execution_with_an_empty_approval_id_is_also_refused() -> None:
    """An empty string is not an approval — the same discipline
    `UnnamedApproverError` holds a publish to."""
    plan = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=(), survivor_rows=()
    )
    with pytest.raises(UnapprovedMergeExecutionError):
        execute_merge(plan, steward_approval_id="   ")


def test_an_approved_execution_returns_the_authorized_plan() -> None:
    plan = plan_merge(
        merged_away_member_id="C2", survivor_member_id="C1", merged_away_rows=(), survivor_rows=()
    )
    authorized = execute_merge(plan, steward_approval_id="APPROVAL-4471")
    assert authorized.plan is plan
    assert authorized.steward_approval_id == "APPROVAL-4471"


# ── verify_post_change — re-derive, never trust ──────────────────────────────


def test_post_change_verification_matches_when_the_merge_fully_executed() -> None:
    """Re-planning from the state AFTER a successful merge must show nothing
    left owned by the merged-away id — that IS "matches the preview"."""
    merged_away_rows = (_row("Members_Addresses", "A1", "C2", "x"),)
    plan = plan_merge(
        merged_away_member_id="C2",
        survivor_member_id="C1",
        merged_away_rows=merged_away_rows,
        survivor_rows=(),
    )
    authorized = execute_merge(plan, steward_approval_id="APPROVAL-1")

    post_change_rows = (_row("Members_Addresses", "A1", "C1", "x"),)  # A1 now owned by C1
    verification = verify_post_change(authorized, post_change_rows=post_change_rows)
    assert verification.matches_preview
    assert verification.unexpected_remainder == ()


def test_post_change_verification_flags_a_row_that_never_repointed() -> None:
    merged_away_rows = (_row("Members_Addresses", "A1", "C2", "x"),)
    plan = plan_merge(
        merged_away_member_id="C2",
        survivor_member_id="C1",
        merged_away_rows=merged_away_rows,
        survivor_rows=(),
    )
    authorized = execute_merge(plan, steward_approval_id="APPROVAL-1")

    post_change_rows = (
        _row("Members_Addresses", "A1", "C2", "x"),
    )  # still C2 — the write never happened
    verification = verify_post_change(authorized, post_change_rows=post_change_rows)
    assert not verification.matches_preview
    assert verification.unexpected_remainder == ("A1",)


def test_verify_raises_when_asked_to_enforce_a_mismatch() -> None:
    merged_away_rows = (_row("Members_Addresses", "A1", "C2", "x"),)
    plan = plan_merge(
        merged_away_member_id="C2",
        survivor_member_id="C1",
        merged_away_rows=merged_away_rows,
        survivor_rows=(),
    )
    authorized = execute_merge(plan, steward_approval_id="APPROVAL-1")
    with pytest.raises(PreviewMismatchError, match="A1"):
        verify_post_change(authorized, post_change_rows=merged_away_rows, enforce=True)


# ── demographic comparison — categorical only, never a raw value ────────────


def test_comparison_reports_match_and_differs_never_the_values() -> None:
    left = {"first_name": "Jane", "last_name": "Doe", "date_of_birth": "1980-01-01"}
    right = {"first_name": "Janie", "last_name": "Doe", "date_of_birth": "1980-01-01"}
    comparison = compare_demographics(
        left, right, fields=("first_name", "last_name", "date_of_birth")
    )
    assert comparison.fields["last_name"] is FieldComparison.MATCH
    assert comparison.fields["date_of_birth"] is FieldComparison.MATCH
    assert comparison.fields["first_name"] is FieldComparison.DIFFERS
    rendered = repr(comparison)
    assert "Jane" not in rendered and "Doe" not in rendered and "1980" not in rendered


def test_a_field_missing_from_one_side_differs() -> None:
    comparison = compare_demographics({"middle_name": "Ann"}, {}, fields=("middle_name",))
    assert comparison.fields["middle_name"] is FieldComparison.DIFFERS


# ── flip-flop detection ───────────────────────────────────────────────────────

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _event(kind: IdentityEventKind, *, at: datetime, source: str = "FIDELIS") -> IdentityEvent:
    return IdentityEvent(
        kind=kind, member_a="C1", member_b="C2", source_system=source, occurred_ts=at
    )


def test_an_identity_that_merges_and_splits_repeatedly_is_a_flip_flop() -> None:
    events = (
        _event(IdentityEventKind.MERGE, at=T0),
        _event(IdentityEventKind.SPLIT, at=T0 + timedelta(days=1)),
        _event(IdentityEventKind.MERGE, at=T0 + timedelta(days=2)),
        _event(IdentityEventKind.SPLIT, at=T0 + timedelta(days=3)),
    )
    findings = detect_flip_flop(events, within=timedelta(days=30), min_reversals=2)
    assert len(findings) == 1
    assert findings[0].offending_source == "FIDELIS"
    assert findings[0].reversal_count == 3


def test_a_single_merge_with_no_reversal_is_not_a_flip_flop() -> None:
    findings = detect_flip_flop(
        (_event(IdentityEventKind.MERGE, at=T0),), within=timedelta(days=30)
    )
    assert findings == ()


def test_reversals_outside_the_window_do_not_count() -> None:
    events = (
        _event(IdentityEventKind.MERGE, at=T0),
        _event(IdentityEventKind.SPLIT, at=T0 + timedelta(days=90)),
    )
    findings = detect_flip_flop(events, within=timedelta(days=30), min_reversals=2)
    assert findings == ()


def test_flip_flop_names_the_offending_source_when_two_sources_are_involved() -> None:
    """The source that supplied the MOST reversing signals is the one named —
    "surfaces the offending source" is a diagnostic, not a shared blame."""
    events = (
        _event(IdentityEventKind.MERGE, at=T0, source="FIDELIS"),
        _event(IdentityEventKind.SPLIT, at=T0 + timedelta(days=1), source="FIDELIS"),
        _event(IdentityEventKind.MERGE, at=T0 + timedelta(days=2), source="FIDELIS"),
        _event(IdentityEventKind.SPLIT, at=T0 + timedelta(days=3), source="OPTUM"),
    )
    (finding,) = detect_flip_flop(events, within=timedelta(days=30), min_reversals=2)
    assert finding.offending_source == "FIDELIS"
