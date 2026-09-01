"""CF-V3-E8-05 — Silver Raw to Silver ODS: the load decisions, pure.

"Apply history rules exactly as configured per entity (current-only
 updates vs full history with effective dates). Run business
 deduplication with the documented precedence rules, logging what was
 merged and why. Preserve source identifiers on every row alongside
 surrogate keys."
"Exception — Given two sources assert different current addresses for
 one member on the same day, when business dedup runs, then the
 configured precedence applies, the losing value is retained in history
 (not discarded), and the conflict is logged for the data-quality trend."
"Don't — Load a record whose identity is unresolved."
— CF-V3-E8-05
"""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.ods_load import (
    DedupConflict,
    LoadAction,
    NoPrecedenceRuleError,
    PrecedenceRule,
    SourceValue,
    UnresolvedIdentityLoadedError,
    assign_surrogate_key,
    cast_mapped_value,
    compute_record_hash,
    enrich_with_crosswalk,
    plan_current_only,
    plan_effective_dated,
    resolve_precedence,
    stringify_for_mapping,
)
from cinqflow.core.registry.contract import CastFailureError
from cinqflow.core.schema_spec import Column, TypeName

pytestmark = pytest.mark.unit


def _entry(**overrides: object) -> CrosswalkEntry:
    defaults: dict[str, object] = {
        "source_system": "fidelis",
        "source_member_id": "F-1",
        "internal_member_id": "",
        "verato_person_id": None,
        "batch_id": "batch-1",
        "outcome": MatchOutcome.RESOLVED,
    }
    defaults.update(overrides)
    return CrosswalkEntry(**defaults)  # type: ignore[arg-type]


# ── enrich_with_crosswalk ────────────────────────────────────────────────


def test_enrich_attaches_the_crosswalks_two_identifiers() -> None:
    entry = _entry(internal_member_id="482910", verato_person_id="LNK-9")
    enriched = enrich_with_crosswalk({"first_name": "Ana"}, entry)
    assert enriched["first_name"] == "Ana"
    assert enriched["_internal_member_id"] == "482910"
    assert enriched["_verato_person_id"] == "LNK-9"


def test_enrich_refuses_an_unresolved_entry() -> None:
    """ "A record whose identity is unresolved never loads" — enforced at
    this one seam, not trusted to every future caller's own filtering."""
    entry = _entry(outcome=MatchOutcome.UNRESOLVED)
    with pytest.raises(UnresolvedIdentityLoadedError, match="fidelis:F-1"):
        enrich_with_crosswalk({"first_name": "Ana"}, entry)


def test_enrich_refuses_a_failed_entry() -> None:
    entry = _entry(outcome=MatchOutcome.FAILED)
    with pytest.raises(UnresolvedIdentityLoadedError):
        enrich_with_crosswalk({}, entry)


def test_enrich_does_not_mutate_the_original_row() -> None:
    row = {"first_name": "Ana"}
    enrich_with_crosswalk(row, _entry(internal_member_id="1"))
    assert row == {"first_name": "Ana"}


# ── compute_record_hash ──────────────────────────────────────────────────


def test_hash_is_stable_for_the_same_values() -> None:
    values = {"FirstName": "Ana", "LastName": "Diaz", "BatchId": "b1"}
    assert compute_record_hash(values, ("FirstName", "LastName")) == compute_record_hash(
        values, ("FirstName", "LastName")
    )


def test_hash_changes_when_a_named_column_changes() -> None:
    a = compute_record_hash({"FirstName": "Ana"}, ("FirstName",))
    b = compute_record_hash({"FirstName": "Anita"}, ("FirstName",))
    assert a != b


def test_hash_ignores_columns_not_named() -> None:
    """Adding an unrelated audit column must never invalidate an existing
    hash — the hash is over declared BUSINESS columns only."""
    a = compute_record_hash({"FirstName": "Ana", "BatchId": "b1"}, ("FirstName",))
    b = compute_record_hash({"FirstName": "Ana", "BatchId": "b2"}, ("FirstName",))
    assert a == b


# ── assign_surrogate_key ─────────────────────────────────────────────────


def test_assign_surrogate_key_reuses_a_legacy_id() -> None:
    minted = []
    key = assign_surrogate_key("482910", mint=lambda: minted.append(1) or 999)
    assert key == 482910
    assert minted == []


def test_assign_surrogate_key_mints_for_a_genuinely_new_member() -> None:
    """ "internal_member_id stays empty for a genuinely new member, never
    invented to fill it" — minting a fresh one is what THIS function does
    with that empty string; nobody upstream fills it."""
    key = assign_surrogate_key("", mint=lambda: 555)
    assert key == 555


# ── plan_current_only / plan_effective_dated ─────────────────────────────


def test_current_only_inserts_when_nothing_exists() -> None:
    assert plan_current_only(None, "h1") is LoadAction.INSERTED


def test_current_only_updates_when_the_hash_changed() -> None:
    assert plan_current_only("h1", "h2") is LoadAction.UPDATED


def test_current_only_skips_when_unchanged() -> None:
    """Idempotency guardrail: the same input arriving twice is safely
    skipped — data is never duplicated, and never rewritten for nothing."""
    assert plan_current_only("h1", "h1") is LoadAction.SKIPPED_UNCHANGED


def test_effective_dated_opens_when_nothing_is_open() -> None:
    assert plan_effective_dated(None, "h1") is LoadAction.OPENED


def test_effective_dated_closes_and_opens_when_changed() -> None:
    """ "An address change closes the old row and opens a new one" — the
    model's own comment on `Members_Addresses`, verbatim."""
    assert plan_effective_dated("h1", "h2") is LoadAction.CLOSED_AND_OPENED


def test_effective_dated_skips_when_unchanged() -> None:
    assert plan_effective_dated("h1", "h1") is LoadAction.SKIPPED_UNCHANGED


# ── resolve_precedence ────────────────────────────────────────────────────


def test_a_single_source_needs_no_precedence_rule() -> None:
    winner, conflicts = resolve_precedence(
        "Members_Addresses", "Address1", (SourceValue("fidelis", "1 Main St"),), ()
    )
    assert winner.value == "1 Main St"
    assert conflicts == ()


def test_agreeing_sources_need_no_precedence_rule_either() -> None:
    """Two sources naming the SAME value is not a conflict — precedence only
    matters when sources actually disagree."""
    winner, conflicts = resolve_precedence(
        "Members_Addresses",
        "Address1",
        (SourceValue("fidelis", "1 Main St"), SourceValue("molina", "1 Main St")),
        (),
    )
    assert winner.value == "1 Main St"
    assert conflicts == ()


def test_disagreeing_sources_with_no_rule_are_refused() -> None:
    """ "The configured precedence applies" presupposes one is configured —
    silently picking a source would be a coin-flip wearing the story's
    words."""
    with pytest.raises(NoPrecedenceRuleError, match=r"Members_Addresses\.Address1"):
        resolve_precedence(
            "Members_Addresses",
            "Address1",
            (SourceValue("fidelis", "1 Main St"), SourceValue("molina", "2 Oak Ave")),
            (),
        )


def test_the_configured_precedence_picks_the_winner_and_logs_the_loser() -> None:
    rule = PrecedenceRule(
        entity="Members_Addresses", column="Address1", source_priority=("fidelis", "molina")
    )
    winner, conflicts = resolve_precedence(
        "Members_Addresses",
        "Address1",
        (SourceValue("molina", "2 Oak Ave"), SourceValue("fidelis", "1 Main St")),
        (rule,),
    )
    assert winner.source_system == "fidelis"
    assert winner.value == "1 Main St"
    (conflict,) = conflicts
    assert conflict == DedupConflict(
        entity="Members_Addresses",
        column="Address1",
        winner_source="fidelis",
        winner_value="1 Main St",
        loser_source="molina",
        loser_value="2 Oak Ave",
    )
    assert "retained in history, not discarded" in conflict.explain()


def test_a_source_missing_from_the_priority_list_still_loses() -> None:
    """The priority list need not name every source that will ever appear —
    an unranked source simply loses to any ranked one, rather than blocking
    the whole load on an incomplete rule."""
    rule = PrecedenceRule(
        entity="Members_Addresses", column="Address1", source_priority=("fidelis",)
    )
    winner, conflicts = resolve_precedence(
        "Members_Addresses",
        "Address1",
        (SourceValue("centene", "9 Elm St"), SourceValue("fidelis", "1 Main St")),
        (rule,),
    )
    assert winner.source_system == "fidelis"
    assert len(conflicts) == 1


def test_three_way_disagreement_logs_every_loser() -> None:
    rule = PrecedenceRule(
        entity="Members_Addresses",
        column="Address1",
        source_priority=("fidelis", "molina", "centene"),
    )
    winner, conflicts = resolve_precedence(
        "Members_Addresses",
        "Address1",
        (
            SourceValue("centene", "9 Elm St"),
            SourceValue("fidelis", "1 Main St"),
            SourceValue("molina", "2 Oak Ave"),
        ),
        (rule,),
    )
    assert winner.value == "1 Main St"
    assert {c.loser_source for c in conflicts} == {"molina", "centene"}


# ── stringify_for_mapping / cast_mapped_value ────────────────────────────


def test_stringify_converts_a_date_to_iso_format() -> None:
    row = stringify_for_mapping({"DateOfBirth": date(1990, 1, 1)})
    assert row["DateOfBirth"] == "1990-01-01"


def test_stringify_turns_none_into_an_empty_string() -> None:
    row = stringify_for_mapping({"MiddleName": None})
    assert row["MiddleName"] == ""


def test_stringify_leaves_a_string_untouched() -> None:
    row = stringify_for_mapping({"FirstName": "Ana"})
    assert row["FirstName"] == "Ana"


def test_cast_mapped_value_round_trips_a_date_through_isoformat() -> None:
    """The same seam `core.compiler.execute._cast_and_map` crosses in the
    other direction — Silver Raw's typed `date` becomes a string for the
    mapping engine, and this casts it straight back."""
    column = Column("DateOfBirth", TypeName.DATE)
    stringified = stringify_for_mapping({"DateOfBirth": date(1990, 1, 1)})
    assert cast_mapped_value(stringified["DateOfBirth"], column) == date(1990, 1, 1)


def test_cast_mapped_value_reuses_the_platforms_one_date_parser() -> None:
    """19900101 and 1990-01-01 are the same date, identically on both planes
    — proven here by reusing `core.registry.contract.cast_value`, not a
    second definition of what a date string means."""
    column = Column("DateOfBirth", TypeName.DATE)
    assert cast_mapped_value("19900101", column) == date(1990, 1, 1)


def test_cast_mapped_value_refuses_an_unparseable_date() -> None:
    column = Column("DateOfBirth", TypeName.DATE)
    with pytest.raises(CastFailureError):
        cast_mapped_value("not-a-date", column)


def test_cast_mapped_value_returns_none_for_an_empty_nullable_column() -> None:
    column = Column("MiddleName", TypeName.STRING, nullable=True)
    assert cast_mapped_value("", column) is None


def test_cast_mapped_value_refuses_empty_for_a_non_nullable_column() -> None:
    column = Column("OurId", TypeName.INT64, nullable=False)
    with pytest.raises(CastFailureError):
        cast_mapped_value("", column)
