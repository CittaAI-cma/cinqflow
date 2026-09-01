"""CF-V1-E4-02 — the generated pack, and the staleness that is mechanical.

"Given a complete draft for the Centene Medicare clone, when the BA runs
 the end-to-end test, then in minutes she has a pack showing 10,000 rows in
 / 9,992 loaded / 8 quarantined with reasons, twenty before/after examples,
 and every rule's hit rate — ready to attach to the approval."
"Given the test fails midway (a mapping type error), when the run completes,
 then the pack is still produced up to the failure, the failing step is
 explained in plain language, and the wizard links straight to the mapping
 line at fault."
— CF-V1-E4-02
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.compiler.execute import ExecutionResult, QuarantinedRow
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, Layer
from cinqflow.core.onboarding.evidence import (
    EXAMPLE_LIMIT,
    MASKED,
    Failure,
    Gap,
    build_pack,
    configuration_fingerprint,
)
from cinqflow.core.recon import StageReconciliation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "centene-medicare-roster"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)


def governed(object_type: ObjectType, body: dict, *, version: int = 1) -> GovernedObject:
    return GovernedObject(
        object_type=object_type,
        object_id=FEED,
        version=version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=NOW,
        body=body,
    )


def run(*, rows_in: int, loaded: int, quarantined: list[QuarantinedRow]) -> ExecutionResult:
    return ExecutionResult(
        loaded=tuple({"member_row_id": f"m{i}", "first_name": "ADA"} for i in range(loaded)),
        quarantined=tuple(quarantined),
        reconciliation=StageReconciliation(
            batch_id="B-1",
            stage=Layer.SILVER_RAW,
            records_in=rows_in,
            records_out=loaded,
            quarantined=len(quarantined),
        ),
    )


def dropped(count: int, rule_id: str = "DQ-002") -> list[QuarantinedRow]:
    return [
        QuarantinedRow(
            row_number=index,
            rule_id=rule_id,
            reason="Member First Name Not Null",
            columns=("First_Name",),
            row={"First_Name": ""},
        )
        for index in range(count)
    ]


# ── the fingerprint ──────────────────────────────────────────────────────────
def test_the_fingerprint_is_over_bodies_not_version_numbers() -> None:
    """A resubmitted identical draft is the same configuration under a new
    number. Invalidating evidence for a version bump that changed nothing
    teaches people the staleness gate is noise."""
    body = {"columns": [{"name": "First_Name"}]}
    first = configuration_fingerprint([governed(ObjectType.CONTRACT, body, version=1)])
    second = configuration_fingerprint([governed(ObjectType.CONTRACT, body, version=2)])
    assert first == second


def test_key_order_does_not_change_the_fingerprint() -> None:
    """Two dicts with the same contents and different insertion order must
    fingerprint identically, or the gate fires on a JSONB round trip."""
    one = configuration_fingerprint([governed(ObjectType.CONTRACT, {"a": 1, "b": 2})])
    two = configuration_fingerprint([governed(ObjectType.CONTRACT, {"b": 2, "a": 1})])
    assert one == two


def test_editing_a_mapping_changes_the_fingerprint() -> None:
    """The wave's exit criterion depends on exactly this."""
    before = configuration_fingerprint([governed(ObjectType.MAPPING, {"lines": ["a"]})])
    after = configuration_fingerprint([governed(ObjectType.MAPPING, {"lines": ["a", "b"]})])
    assert before != after


def test_the_feed_record_is_not_part_of_the_fingerprint() -> None:
    """A change of owner or alert address does not invalidate a demonstration
    that the data loads."""
    contract = governed(ObjectType.CONTRACT, {"columns": []})
    with_feed = configuration_fingerprint(
        [contract, governed(ObjectType.FEED, {"owner": "someone@else"})]
    )
    assert with_feed == configuration_fingerprint([contract])


def test_a_different_sample_is_a_different_configuration() -> None:
    contract = governed(ObjectType.CONTRACT, {"columns": []})
    assert configuration_fingerprint(
        [contract], sample_fingerprint="sha256-a"
    ) != configuration_fingerprint([contract], sample_fingerprint="sha256-b")


# ── the happy path ───────────────────────────────────────────────────────────
def test_the_pack_carries_the_counts_the_story_names() -> None:
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=10_000, loaded=9_992, quarantined=dropped(8)),
        objects=[governed(ObjectType.CONTRACT, {"columns": []})],
        rule_names={"DQ-002": "Member First Name Not Null"},
        quarantining_rules=frozenset({"DQ-002"}),
        now=NOW,
    )
    assert (pack.rows_in, pack.rows_loaded, pack.rows_quarantined) == (10_000, 9_992, 8)
    assert pack.accounts_for_every_row
    assert "10,000 rows in / 9,992 loaded / 8 quarantined" in pack.summary()


def test_every_drop_names_a_reason() -> None:
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=100, loaded=92, quarantined=dropped(8)),
        objects=[],
        now=NOW,
    )
    assert len(pack.drops) == 1
    assert pack.drops[0].record_count == 8
    assert pack.drops[0].reason == "Member First Name Not Null"


def test_a_rule_that_caught_nothing_is_still_reported() -> None:
    """Silence is data. A rule that never fires on a representative sample is
    either protecting against something rare or is wrong, and only a human can
    tell which."""
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=100, loaded=100, quarantined=[]),
        objects=[],
        rule_names={"DQ-002": "First name", "DQ-030": "ZIP format"},
        now=NOW,
    )
    assert {r.rule_id for r in pack.rules} == {"DQ-002", "DQ-030"}
    assert all(rule.flagged == 0 for rule in pack.rules)


def test_examples_are_capped_at_twenty_and_masked() -> None:
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=50, loaded=50, quarantined=[]),
        objects=[],
        sample_rows=[{"MEMBER_ID": f"m{i}", "FIRST_NAME": "Ada"} for i in range(50)],
        phi_columns=frozenset({"FIRST_NAME", "first_name"}),
        now=NOW,
    )
    assert len(pack.examples) == EXAMPLE_LIMIT
    assert pack.examples[0].before["FIRST_NAME"] == MASKED
    # And on the OTHER side of the mapping — the canonical field the value
    # lands in is the same protected value under a different name.
    assert pack.examples[0].after["first_name"] == MASKED
    assert pack.examples[0].before["MEMBER_ID"] == "m0"


def test_masking_happens_in_the_pack_not_at_the_screen() -> None:
    """The pack leaves the platform. A masking policy applied only by a
    renderer stops applying the moment somebody exports."""
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=1, loaded=1, quarantined=[]),
        objects=[],
        sample_rows=[{"SSN": "123-45-6789"}],
        phi_columns=frozenset({"SSN"}),
        now=NOW,
    )
    assert "123-45-6789" not in pack.render_markdown()
    assert MASKED in pack.render_markdown()


# ── the honest gaps ──────────────────────────────────────────────────────────
def test_the_pack_lists_gaps_and_says_so_when_there_are_none() -> None:
    """ "The pack is evidence, not marketing." A pack with no gaps section
    reads as though nothing was left out."""
    empty = build_pack(
        feed_id=FEED, result=run(rows_in=1, loaded=1, quarantined=[]), objects=[], now=NOW
    )
    assert "## Known gaps" in empty.render_markdown()
    assert "None recorded." in empty.render_markdown()

    with_gaps = build_pack(
        feed_id=FEED,
        result=run(rows_in=1, loaded=1, quarantined=[]),
        objects=[],
        gaps=[
            Gap(
                key="unmapped_optional",
                what="members.middle_name is not mapped",
                why_it_is_acceptable="This payer does not send it, and the field is optional.",
            )
        ],
        now=NOW,
    )
    assert "members.middle_name is not mapped" in with_gaps.render_markdown()


# ── the exception: a run that failed midway ──────────────────────────────────
def test_a_failed_run_still_produces_a_pack_up_to_the_failure() -> None:
    """A pack that only exists on success is a pack nobody sees on the day they
    most need it."""
    failure = Failure(
        step="Map the fields",
        explanation=(
            "This mapping compares a date to a text field, so the row could not be built."
        ),
        citation=CitationId(
            kind=CitationKind.MAPPING, subject=FEED, version=1, fragment="date_of_birth"
        ),
    )
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=100, loaded=40, quarantined=dropped(3)),
        objects=[],
        failure=failure,
        now=NOW,
    )
    assert pack.partial
    document = pack.render_markdown()
    assert "Where it stopped" in document
    assert "compares a date to a text field" in document
    assert pack.failure is not None
    assert pack.failure.route.startswith("/data/intake/mapping/")
    assert "INCOMPLETE" in pack.summary()


def test_a_pack_whose_arithmetic_does_not_add_up_says_so_on_its_face() -> None:
    """A reviewer signs the pack. A pack that cannot account for every row must
    say so, rather than be caught later by a control table nobody in the
    approval reads."""
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=100, loaded=40, quarantined=dropped(3)),
        objects=[],
        now=NOW,
    )
    assert not pack.accounts_for_every_row
    assert "counts do not balance" in pack.render_markdown()


# ── the document ─────────────────────────────────────────────────────────────
def test_the_document_is_complete_without_platform_access() -> None:
    """A reviewer who needs an account to read the evidence will ask somebody
    to summarise it, and the summary is what gets approved."""
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=10, loaded=9, quarantined=dropped(1)),
        objects=[governed(ObjectType.CONTRACT, {"columns": []})],
        rule_names={"DQ-002": "Member First Name Not Null"},
        quarantining_rules=frozenset({"DQ-002"}),
        sample_rows=[{"MEMBER_ID": "m1"}],
        sample_filename="CENTENE_ROSTER_20260801.csv",
        now=NOW,
    )
    document = pack.render_markdown()
    for expected in (
        f"# Onboarding evidence — {FEED}",
        pack.fingerprint,
        "CENTENE_ROSTER_20260801.csv",
        "Why rows did not load",
        "Every rule, including the quiet ones",
        "Known gaps",
    ):
        assert expected in document


def test_staleness_is_asked_of_the_pack_itself() -> None:
    pack = build_pack(
        feed_id=FEED,
        result=run(rows_in=1, loaded=1, quarantined=[]),
        objects=[governed(ObjectType.MAPPING, {"lines": ["a"]})],
        now=NOW,
    )
    same = configuration_fingerprint([governed(ObjectType.MAPPING, {"lines": ["a"]})])
    edited = configuration_fingerprint([governed(ObjectType.MAPPING, {"lines": ["a", "b"]})])
    assert not pack.is_stale_for(same)
    assert pack.is_stale_for(edited)
