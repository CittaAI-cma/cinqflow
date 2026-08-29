"""CF-V0-E8-02 — Landing Zone Controls: register, validate, never process twice.

    "I want every arriving file to be automatically registered, checked (name,
     structure, size), fingerprinted against past files, and then archived or
     rejected into the right folder, so that only valid, expected, first-time
     files ever enter the pipeline — the 'exactly once' guarantee that makes
     every later feature trustworthy."

Landing is the Control Entry Point: STRUCTURAL validation only, no semantic
validation. The classifier below is pure — it takes a file, the registered
feeds and whether the fingerprint has been seen, and returns a decision. All
I/O happens in the worker that wires it to the storage and control-table pins.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.landing import (
    LandingOutcome,
    RegisteredFeed,
    classify,
)
from cinqflow.core.model.vocabulary import LandingFolder
from cinqflow.ports.storage import FileRef

pytestmark = pytest.mark.unit

FIDELIS = RegisteredFeed(
    feed_id="fidelis-downstate-roster",
    feed_version=1,
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.xlsx",
    file_format="xlsx",
    min_size_bytes=5_000_000,
    max_size_bytes=30_000_000,
)


def _arriving(
    filename: str = "_CINQDOWNSTATE_Member_Roster_202608.xlsx",
    *,
    size: int = 12_000_000,
    path: str = "enrollments/fidelis_downstate/roster",
) -> FileRef:
    return FileRef(
        key=f"{path}/incoming/2026-08-01/{filename}",
        size_bytes=size,
        modified_ts=datetime(2026, 8, 1, 3, 14, tzinfo=UTC),
        fingerprint="sha256-abc123",
    )


# ── happy path ───────────────────────────────────────────────────────────────
def test_an_expected_file_is_accepted_and_routed_to_processed() -> None:
    """ "Given the expected Centene roster arrives on schedule with a valid name
    and size, when landing controls run, then the file is registered as
    Accepted, handed to the engine, and archived after successful
    processing." — CF-V0-E8-02, happy path"""
    decision = classify(_arriving(), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.ACCEPTED
    assert decision.feed_id == "fidelis-downstate-roster"
    assert decision.move_to is LandingFolder.PROCESSED
    assert decision.reason is None


def test_an_accepted_file_names_the_feed_version_it_matched() -> None:
    """ "the engine always states which feed version a run used". A decision
    that named only the feed would leave that unanswerable after a version
    bump."""
    decision = classify(_arriving(), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.feed_version == 1


# ── exception: the unexpected file ───────────────────────────────────────────
def test_a_file_matching_no_feed_is_parked_and_surfaced_never_ignored() -> None:
    """ "Given a file arrives that matches no registered feed, when landing
    controls run, then it is registered as Unexpected, parked unprocessed, and
    Operations sees it on their attention list — NOTHING DISAPPEARS
    SILENTLY." — CF-V0-E8-02, exception"""
    decision = classify(
        _arriving("SOME_OTHER_PAYER_FILE.csv"), feeds=(FIDELIS,), fingerprint_seen=False
    )
    assert decision.outcome is LandingOutcome.UNEXPECTED
    assert decision.move_to is LandingFolder.PARKED
    assert decision.feed_id is None
    assert decision.registered is True, "an unexpected file is still REGISTERED"


def test_an_unexpected_file_says_what_it_did_not_match() -> None:
    """An operator seeing "unexpected" with no detail has to go read the
    registry. Naming the closest thing turns a mystery into a typo."""
    decision = classify(
        _arriving("_CINQDOWNSTATE_Member_Roster_2026O8.xlsx"),  # letter O, not zero
        feeds=(FIDELIS,),
        fingerprint_seen=False,
    )
    assert decision.outcome is LandingOutcome.UNEXPECTED
    assert decision.reason is not None
    assert "fidelis-downstate-roster" in decision.reason


# ── guardrail: exactly once ──────────────────────────────────────────────────
def test_the_same_fingerprint_twice_is_skipped_not_reprocessed() -> None:
    """ "Given the same input arrives twice, when the process runs again, then
    it is safely skipped — data is never duplicated." — CF-V0-E8-02, guardrail

    Incident #4: a duplicate Feb-2025 Fidelis roster was found during seeding.
    """
    decision = classify(_arriving(), feeds=(FIDELIS,), fingerprint_seen=True)
    assert decision.outcome is LandingOutcome.SKIPPED
    assert decision.audit_required is True, "a skip without an audit entry is a silent skip"
    assert decision.move_to is LandingFolder.ARCHIVE


def test_a_skip_is_decided_on_content_not_on_filename() -> None:
    """A re-sent file rarely arrives under its original name, so a name-based
    dedup would miss the duplicate that actually matters."""
    renamed = _arriving("_CINQDOWNSTATE_Member_Roster_202609.xlsx")
    decision = classify(renamed, feeds=(FIDELIS,), fingerprint_seen=True)
    assert decision.outcome is LandingOutcome.SKIPPED


# ── the seeded pre-flight checks ─────────────────────────────────────────────
def test_an_underscore_filename_is_rejected_with_a_stated_reason() -> None:
    """INCIDENT #1, now a permanent pre-flight check.

    "Include the known past failure modes as permanent pre-flight checks (for
    example, file names that start with an underscore, which once broke the
    Excel reader)." — CF-V0-E8-02

    Note the tension this test resolves: the REAL Fidelis pattern legitimately
    starts with an underscore, so the check cannot be "reject all underscores".
    It is "the reader must have been told to expect one" — the feed declares
    it, and an undeclared underscore is refused.
    """
    undeclared = RegisteredFeed(
        feed_id="centene-ga-roster",
        feed_version=1,
        landing_path="enrollments/centene_ga/roster",
        file_pattern=r"_?CENTENE_GA_Roster_\d{6}\.csv",
        file_format="csv",
        allows_leading_underscore=False,
    )
    arriving = _arriving("_CENTENE_GA_Roster_202608.csv", path="enrollments/centene_ga/roster")
    decision = classify(arriving, feeds=(undeclared,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.REJECTED
    assert decision.move_to is LandingFolder.REJECTED
    assert "underscore" in (decision.reason or "").lower()
    assert decision.check_name == "leading_underscore"


def test_the_declared_underscore_pattern_is_accepted() -> None:
    """The Fidelis roster genuinely is named `_CINQDOWNSTATE_...`. A check that
    rejected it would have "fixed" the incident by breaking production."""
    decision = classify(_arriving(), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.ACCEPTED


def test_a_truncated_file_is_rejected_with_its_size_named() -> None:
    """ "Given the simulator injects a truncated file, when landing controls
    run, then the file REJECTS WITH THE STATED REASON." — CF-V0-E8-08"""
    decision = classify(_arriving(size=1_200), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.REJECTED
    assert decision.check_name == "size_bounds"
    assert "1,200" in (decision.reason or "") or "1200" in (decision.reason or "")


def test_an_oversized_file_is_rejected_too() -> None:
    """A roster ten times its usual size is a delivery fault, not a good day."""
    decision = classify(_arriving(size=400_000_000), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.REJECTED
    assert decision.check_name == "size_bounds"


def test_an_empty_file_is_rejected() -> None:
    decision = classify(_arriving(size=0), feeds=(FIDELIS,), fingerprint_seen=False)
    assert decision.outcome is LandingOutcome.REJECTED


def test_a_file_in_the_wrong_landing_path_does_not_match_the_feed() -> None:
    """The pattern is scoped to a path. Otherwise one payer's file name could
    claim another payer's feed."""
    decision = classify(
        _arriving(path="enrollments/molina_ny/roster"), feeds=(FIDELIS,), fingerprint_seen=False
    )
    assert decision.outcome is LandingOutcome.UNEXPECTED


# ── ordering, which is itself a guarantee ────────────────────────────────────
def test_a_duplicate_is_skipped_before_any_other_check_runs() -> None:
    """Order matters: a file already processed must be SKIPPED, not re-judged.

    Re-judging a known file means a pattern change could reject something the
    platform has already loaded — and then the drop ledger and Bronze disagree.
    """
    decision = classify(_arriving(size=0), feeds=(FIDELIS,), fingerprint_seen=True)
    assert decision.outcome is LandingOutcome.SKIPPED


def test_every_rejection_names_the_check_that_rejected_it() -> None:
    """A rejection with no named check is an unattributed drop wearing a
    different hat."""
    for arriving in (_arriving(size=0), _arriving(size=999_999_999)):
        decision = classify(arriving, feeds=(FIDELIS,), fingerprint_seen=False)
        assert decision.check_name, decision
        assert decision.reason


def test_every_decision_is_registered_including_the_rejections() -> None:
    """ "Register every file the moment it arrives." 100% of arriving files have
    a registry entry — that is the measurable bar."""
    cases = [
        _arriving(),
        _arriving(size=0),
        _arriving("UNKNOWN.csv"),
    ]
    for arriving in cases:
        assert classify(arriving, feeds=(FIDELIS,), fingerprint_seen=False).registered is True
    assert classify(_arriving(), feeds=(FIDELIS,), fingerprint_seen=True).registered is True


def test_landing_does_no_semantic_validation() -> None:
    """ "Landing is the Control Entry Point: STRUCTURAL validation only, no
    semantic validation."

    Columns, types and DQ rules are G2's job, on the Bronze -> Silver Raw hop.
    A landing check that read the file's contents would be doing G2's work at
    G1, where there is no contract to check against yet — and it would make the
    trust boundary depend on a parser, which is the component most likely to
    fail on a malformed file.

    Asserted over the AST rather than the source text, so the module is free to
    EXPLAIN the boundary in prose without tripping its own test.
    """
    import ast
    import inspect

    from cinqflow.core import landing

    tree = ast.parse(inspect.getsource(landing))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    for semantic in ("read_bytes", "read", "open", "parse", "load", "decode"):
        assert semantic not in called, f"landing performs semantic work: {semantic}()"

    # And it never receives content in the first place.
    signature = inspect.signature(classify)
    assert set(signature.parameters) == {"file", "feeds", "fingerprint_seen"}
