"""CF-V3-E9-01 — the identity stage: minimize, submit, disposition, balance.

    "Store full request and response payloads with hashes ... Retry transient
     failures with backoff; categorize outcomes rather than collapsing them
     into success/failure. Never let an unresolved identity silently
     disappear — every record has a disposition."
    — CF-V3-E9-01

    "Given a roster batch reaches the identity stage, when resolution runs,
     then 9,940 of 10,000 resolve with LinkIds in the crosswalk, 42 fail
     transiently and succeed on retry, 18 remain unresolved and route to the
     exception queue — and 9,940 + 42 + 18 = 10,000, proven."
    — CF-V3-E9-01, happy path

    "Send no member attribute not required by the request specification."
    — CF-V3-E9-01, don'ts

The design decision under test: G4's balance equation
(submitted == resolved + unresolved + failed) is computed the same way
core.recon computes G2/G3's — a property of the accounting, checked and
raised on, never assumed. `prepare()` is where attribute minimization is
enforced structurally rather than trusted to whatever calls the identity
port next.
"""

from __future__ import annotations

import pytest

from cinqflow.core.identity import (
    REQUIRED_ATTRIBUTES,
    CrosswalkEntry,
    IdentityDisposition,
    IdentityStageError,
    MatchOutcome,
    UnbalancedIdentityError,
    dispose,
    prepare,
)

pytestmark = pytest.mark.unit

_FULL_SILVER_RAW_ROW = {
    "member_row_id": "row-1",
    "feed_id": "fidelis-downstate-roster",
    "source_system": "FIDELIS",
    "source_member_id": "M-1001",
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1980-01-01",
    "gender": "F",
    "line_of_business": "Medicaid",
    "record_hash": "deadbeef",
    "batch_id": "b-1",
}


# ── prepare — attribute minimization ─────────────────────────────────────────


def test_prepare_keeps_only_the_required_attributes() -> None:
    (record,) = prepare([_FULL_SILVER_RAW_ROW])
    assert set(record) <= REQUIRED_ATTRIBUTES


def test_prepare_never_sends_a_column_the_specification_does_not_name() -> None:
    """The negative test the story's don't names explicitly: nothing beyond
    REQUIRED_ATTRIBUTES ever leaves this function, no matter how much a
    Silver Raw row happens to carry."""
    (record,) = prepare([_FULL_SILVER_RAW_ROW])
    assert "record_hash" not in record
    assert "member_row_id" not in record
    assert "line_of_business" not in record


def test_prepare_never_invents_an_attribute_a_record_does_not_have() -> None:
    """Minimization SUBTRACTS; it never pads a sparse record with keys it
    never had, which would let a caller mistake 'absent' for 'empty string'."""
    sparse = {"source_system": "FIDELIS", "source_member_id": "M-1002"}
    (record,) = prepare([sparse])
    assert record == sparse


def test_prepare_preserves_record_order_and_count() -> None:
    records = [_FULL_SILVER_RAW_ROW, {"source_system": "FIDELIS", "source_member_id": "M-1002"}]
    prepared = prepare(records)
    assert len(prepared) == 2
    assert prepared[0]["source_member_id"] == "M-1001"
    assert prepared[1]["source_member_id"] == "M-1002"


# ── IdentityDisposition — G4's accounting ────────────────────────────────────


def _entry(source_member_id: str, outcome: MatchOutcome) -> CrosswalkEntry:
    return CrosswalkEntry(
        source_system="FIDELIS",
        source_member_id=source_member_id,
        internal_member_id=f"cinq-{source_member_id}",
        verato_person_id=f"verato-{source_member_id}" if outcome is MatchOutcome.RESOLVED else None,
        batch_id="b-1",
        outcome=outcome,
    )


def test_the_worked_example_balances_exactly() -> None:
    """9,940 + 42 + 18 = 10,000 — the story's own numbers, proven rather than
    asserted."""
    entries = (
        tuple(_entry(f"r{i}", MatchOutcome.RESOLVED) for i in range(9940))
        + tuple(_entry(f"f{i}", MatchOutcome.FAILED) for i in range(42))
        + tuple(_entry(f"u{i}", MatchOutcome.UNRESOLVED) for i in range(18))
    )
    disposition = IdentityDisposition(batch_id="b-1", submitted=10_000, entries=entries)
    assert disposition.resolved == 9940
    assert disposition.failed == 42
    assert disposition.unresolved == 18
    assert disposition.balances
    assert dispose(disposition) is disposition


def test_a_short_count_of_entries_does_not_balance() -> None:
    """Submitted must equal len(entries) too — a disposition missing a
    record's outcome entirely is exactly the silent disappearance the story
    forbids, and a resolved+unresolved+failed sum alone would miss it."""
    entries = (_entry("r1", MatchOutcome.RESOLVED),)
    disposition = IdentityDisposition(batch_id="b-1", submitted=2, entries=entries)
    assert not disposition.balances
    with pytest.raises(UnbalancedIdentityError, match="b-1"):
        dispose(disposition)


def test_loadable_records_are_exactly_the_resolved_ones() -> None:
    """ "A record whose identity is unresolved NEVER loads." — the property
    the ODS loader depends on, computed here rather than re-derived by every
    caller."""
    entries = (
        _entry("r1", MatchOutcome.RESOLVED),
        _entry("u1", MatchOutcome.UNRESOLVED),
        _entry("f1", MatchOutcome.FAILED),
    )
    disposition = IdentityDisposition(batch_id="b-1", submitted=3, entries=entries)
    assert [e.source_member_id for e in disposition.loadable] == ["r1"]
    assert {e.source_member_id for e in disposition.blocked} == {"u1", "f1"}


def test_explain_names_every_category_and_the_batch() -> None:
    disposition = IdentityDisposition(
        batch_id="b-1",
        submitted=3,
        entries=(
            _entry("r1", MatchOutcome.RESOLVED),
            _entry("u1", MatchOutcome.UNRESOLVED),
            _entry("f1", MatchOutcome.FAILED),
        ),
    )
    explanation = disposition.explain()
    assert "b-1" in explanation
    assert "1 resolved" in explanation
    assert "1 unresolved" in explanation
    assert "1 failed" in explanation


def test_a_disposition_with_no_entries_and_zero_submitted_balances() -> None:
    """An empty batch is a legitimate, balanced disposition — zero equals
    zero — never a special case that skips the equation."""
    disposition = IdentityDisposition(batch_id="b-empty", submitted=0, entries=())
    assert disposition.balances
    assert dispose(disposition) is disposition


def test_dispose_raises_the_stage_error_family() -> None:
    disposition = IdentityDisposition(batch_id="b-1", submitted=5, entries=())
    with pytest.raises(IdentityStageError):
        dispose(disposition)
