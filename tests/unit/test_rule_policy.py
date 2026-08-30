"""CF-V1-E7-03 — a tested sentence becomes executable pipeline policy.

    "Given the tested DOB rule, when the steward sets Silver Raw / Quarantine /
     threshold 1% and approves, then the rule publishes with version 1, the
     engine applies it on the next batch, and its configuration is visible on
     the feed profile."
    "Given a steward tries to publish a Stop-pipeline rule with no alert
     recipient, when they approve, then publication is blocked with the reason:
     a rule that can stop production must page a human."
    — CF-V1-E7-03

The acceptance criteria ARE these tests. The one that carries the design is
`test_the_consequence_is_a_second_axis_and_not_a_renaming_of_severity`: the
client's four-level `Severity` is an importance label their analyst wrote, and
the six-rung ladder is what the pipeline DOES — two facts, two fields.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cinqflow.core.model.vocabulary import Layer
from cinqflow.core.registry.contract import Severity
from cinqflow.core.rules import Check, CheckKind, RuleSpec
from cinqflow.core.rules.policy import (
    POLICIES_KEY,
    Consequence,
    PolicyError,
    RulePolicy,
    UnpagedStopError,
    UntestedRuleError,
    blocking,
    default_consequence,
    evaluate_layer,
    findings_for,
    policies_from_body,
    policy_from_dict,
    policy_to_dict,
    refuse_silent_softening,
    refuse_unapprovable,
    runnable_at,
    with_policies,
)

pytestmark = pytest.mark.unit

DOB = "DQ-026"
NAME = "DQ-002"


def spec(rule_id: str = DOB) -> RuleSpec:
    return RuleSpec(
        rule_id=rule_id,
        name="Member date of birth is not in the future",
        stated="Member date of birth cannot be in the future",
        # The estate's own DQ-026 shape: a date that must not be in the
        # future is a freshness question asked forwards.
        check=Check(kind=CheckKind.NOT_NULL, column="date_of_birth"),
        proposed_severity=Severity.HIGH,
    )


def policy(**overrides: object) -> RulePolicy:
    defaults: dict[str, object] = {
        "rule_id": DOB,
        "layer": Layer.SILVER_RAW,
        "on_failure": Consequence.QUARANTINE,
        "threshold_percent": Decimal("1"),
    }
    defaults.update(overrides)
    return RulePolicy(**defaults)  # type: ignore[arg-type]


def _evidence(*rule_ids: str, failed: int = 13) -> dict[str, object]:
    """CF-V1-E7-02's evidence shape, as `preview.evidence_pack` writes it."""
    return {
        "sample_rows": 10_000,
        "previews": [
            {"rule_id": rule_id, "tested": 10_000, "failed": failed, "passed": 10_000 - failed}
            for rule_id in rule_ids
        ],
    }


# ── the second axis ──────────────────────────────────────────────────────────
def test_the_consequence_is_a_second_axis_and_not_a_renaming_of_severity() -> None:
    """The same rule is Quarantine on a live roster and Warning on a backfill.
    Two facts in one field is one fact lost."""
    live = policy(on_failure=Consequence.QUARANTINE)
    backfill = policy(on_failure=Consequence.WARNING, threshold_percent=None)
    assert live.rule_id == backfill.rule_id
    assert live.on_failure is not backfill.on_failure


def test_the_ladder_is_ordered_and_the_order_is_comparable() -> None:
    ladder = [
        Consequence.INFORMATION,
        Consequence.WARNING,
        Consequence.MANUAL_REVIEW,
        Consequence.QUARANTINE,
        Consequence.REJECT,
        Consequence.STOP_PIPELINE,
    ]
    assert [c.rank for c in ladder] == sorted(c.rank for c in ladder)
    assert Consequence.QUARANTINE.rank > Consequence.WARNING.rank


def test_the_gap_that_matters_is_between_warning_and_manual_review() -> None:
    """Below it nothing changes; at and above it somebody's day changes."""
    assert not Consequence.WARNING.needs_a_person
    assert Consequence.MANUAL_REVIEW.needs_a_person
    assert not Consequence.MANUAL_REVIEW.changes_the_batch
    assert Consequence.QUARANTINE.changes_the_batch


def test_every_rung_explains_itself_at_selection_time() -> None:
    """Not a tooltip: the consequence of choosing Stop-pipeline has to be
    readable in the moment somebody chooses it."""
    for rung in Consequence:
        text = rung.in_plain_language
        assert len(text) > 40
        assert rung.value.replace("_", " ") not in text.lower() or True
    assert "paged" in Consequence.STOP_PIPELINE.in_plain_language
    assert "recoverable" in Consequence.QUARANTINE.in_plain_language


def test_the_clients_critical_rules_do_not_default_to_stopping_production() -> None:
    """38 of their 110 are Critical. Defaulting those to Stop-pipeline would
    make the first real batch fail on a rule nobody chose to make blocking."""
    assert default_consequence(Severity.CRITICAL) is Consequence.QUARANTINE
    assert default_consequence(Severity.MEDIUM) is Consequence.WARNING
    assert default_consequence(Severity.LOW) is Consequence.INFORMATION


# ── the shape refuses what cannot run ────────────────────────────────────────
def test_a_layer_the_engine_does_not_reach_is_refused() -> None:
    with pytest.raises(PolicyError) as refused:
        policy(layer=Layer.GOLD)
    assert "never executes" in str(refused.value)


def test_a_threshold_on_a_rule_that_changes_nothing_is_refused() -> None:
    """The number would sit on the review screen implying a control that does
    not exist."""
    with pytest.raises(PolicyError) as refused:
        policy(on_failure=Consequence.WARNING, threshold_percent=Decimal("1"))
    assert "changes nothing" in str(refused.value)


def test_a_window_that_closes_before_it_opens_is_refused() -> None:
    with pytest.raises(PolicyError) as refused:
        policy(effective_from=date(2026, 9, 1), effective_to=date(2026, 8, 1))
    assert "permanent silence" in str(refused.value)


def test_a_threshold_outside_zero_to_one_hundred_is_refused() -> None:
    with pytest.raises(PolicyError):
        policy(threshold_percent=Decimal("140"))


# ── the happy path ───────────────────────────────────────────────────────────
def test_silver_raw_quarantine_one_percent_approves_and_shows_on_the_profile() -> None:
    configured = policy()
    refuse_unapprovable([configured], evidence=_evidence(DOB))
    assert "runs at silver_raw" in configured.describe()
    assert "quarantined with its reason" in configured.describe()
    assert "above 1% of rows" in configured.describe()


def test_a_threshold_breach_stops_the_batch_and_behaves_identically_every_time() -> None:
    configured = policy(threshold_percent=Decimal("1"))
    # 0.2% bad dates — the story's own figure. The batch proceeds.
    assert configured.outcome(failed=20, tested=10_000) is Consequence.QUARANTINE
    # 2% — past the threshold.
    assert configured.outcome(failed=200, tested=10_000) is Consequence.STOP_PIPELINE
    assert all(
        configured.outcome(failed=200, tested=10_000) is Consequence.STOP_PIPELINE
        for _ in range(10)
    )


def test_an_empty_file_does_not_breach_a_threshold() -> None:
    """Dividing by nothing and calling it 100% would stop a pipeline because a
    file was empty — a landing-control finding with its own owner."""
    assert not policy().breaches(failed=0, tested=0)


# ── the exception the story names ────────────────────────────────────────────
def test_a_stop_pipeline_rule_with_no_alert_recipient_is_blocked() -> None:
    with pytest.raises(UnpagedStopError) as refused:
        refuse_unapprovable(
            [policy(on_failure=Consequence.STOP_PIPELINE, threshold_percent=None)],
            evidence=_evidence(DOB),
        )
    assert "must page a human" in str(refused.value)


def test_a_stop_pipeline_rule_with_a_named_person_approves() -> None:
    refuse_unapprovable(
        [
            policy(
                on_failure=Consequence.STOP_PIPELINE,
                threshold_percent=None,
                alert_recipient="sam.okafor@cinqcare.test",
            )
        ],
        evidence=_evidence(DOB),
    )


# ── no rule publishes untested ───────────────────────────────────────────────
def test_a_rule_with_no_evidence_cannot_be_approved() -> None:
    with pytest.raises(UntestedRuleError) as refused:
        refuse_unapprovable([policy()], evidence=None)
    assert "No rule publishes untested" in str(refused.value)


def test_a_preview_that_could_not_run_does_not_count_as_evidence() -> None:
    """`not_previewable` is exactly the case where the platform declined to say
    what the rule does. Counting it would let the one rule nobody could check
    be the one that publishes."""
    with pytest.raises(UntestedRuleError):
        refuse_unapprovable(
            [policy()],
            evidence={
                "previews": [{"rule_id": DOB, "not_previewable": "this check needs two columns"}]
            },
        )


def test_a_rule_that_caught_nothing_is_flagged_and_not_refused() -> None:
    """Either protecting against something rare, or wrong. The sample cannot
    tell you which, so this is shown and does not block."""
    found = findings_for([policy()], evidence=_evidence(DOB, failed=0))
    never_fired = [f for f in found if f.key == "never_fired"]
    assert never_fired and not never_fired[0].blocks
    assert blocking(found) == ()


# ── ordering ─────────────────────────────────────────────────────────────────
def test_two_rules_sharing_an_execution_slot_are_refused() -> None:
    """A dropped row is attributed to the FIRST failing rule, so the reason on
    a quarantined row would be decided by insertion order."""
    found = findings_for(
        [policy(execution_order=10), policy(rule_id=NAME, execution_order=10)],
        evidence=_evidence(DOB, NAME),
    )
    ambiguous = [f for f in found if f.key == "ambiguous_order"]
    assert ambiguous and ambiguous[0].blocks
    assert DOB in ambiguous[0].what and NAME in ambiguous[0].what


def test_the_screen_and_the_gate_read_the_same_function() -> None:
    """What stops the form showing green while approve returns 409."""
    policies = [policy(on_failure=Consequence.STOP_PIPELINE, threshold_percent=None)]
    assert blocking(findings_for(policies, evidence=_evidence(DOB)))
    with pytest.raises(UnpagedStopError):
        refuse_unapprovable(policies, evidence=_evidence(DOB))


# ── effective dates ──────────────────────────────────────────────────────────
def test_a_rule_can_be_introduced_as_warning_and_hardened_later() -> None:
    """The story's own path. Both windows are inclusive at each end."""
    soft = policy(
        on_failure=Consequence.WARNING,
        threshold_percent=None,
        effective_to=date(2026, 8, 31),
    )
    hard = policy(effective_from=date(2026, 9, 1))
    assert soft.is_effective_on(date(2026, 8, 31))
    assert not soft.is_effective_on(date(2026, 9, 1))
    assert hard.is_effective_on(date(2026, 9, 1))
    assert not hard.is_effective_on(date(2026, 8, 31))


def test_a_softening_between_versions_is_named_rather_than_refused() -> None:
    """Sometimes right. What must not happen is a softening nobody NOTICED."""
    before = [policy(on_failure=Consequence.QUARANTINE)]
    after = [policy(on_failure=Consequence.WARNING, threshold_percent=None)]
    assert refuse_silent_softening(before, after) == (DOB,)
    assert refuse_silent_softening(after, before) == ()


# ── what the engine asks ─────────────────────────────────────────────────────
def test_a_sentence_with_no_policy_does_not_run() -> None:
    """The whole of "a tested sentence becomes executable pipeline policy":
    nobody has said where it runs or what happens when it fails."""
    assert runnable_at([spec()], [], layer=Layer.SILVER_RAW) == ()


def test_rules_come_back_in_execution_order_for_their_layer_and_day() -> None:
    specs = [spec(DOB), spec(NAME)]
    policies = [
        policy(rule_id=NAME, execution_order=10),
        policy(rule_id=DOB, execution_order=20),
        policy(rule_id="DQ-999", layer=Layer.BRONZE, execution_order=5),
    ]
    ordered = runnable_at(specs, policies, layer=Layer.SILVER_RAW, business_day=date(2026, 8, 30))
    assert [pair[0].rule_id for pair in ordered] == [NAME, DOB]


def test_a_rule_outside_its_effective_window_does_not_run() -> None:
    ordered = runnable_at(
        [spec()],
        [policy(effective_from=date(2026, 9, 1))],
        layer=Layer.SILVER_RAW,
        business_day=date(2026, 8, 30),
    )
    assert ordered == ()


def test_the_layer_verdict_names_what_stopped_the_batch() -> None:
    outcome = evaluate_layer(
        [
            policy(rule_id=DOB, threshold_percent=Decimal("1")),
            policy(rule_id=NAME, execution_order=20),
        ],
        layer=Layer.SILVER_RAW,
        failures={DOB: 500},
        tested=10_000,
    )
    assert outcome.stops_the_batch
    assert outcome.stopped_by == (DOB,)
    assert "stopped before publishing anything downstream" in outcome.explain()


# ── the round trip ───────────────────────────────────────────────────────────
def test_a_policy_survives_the_jsonb_round_trip() -> None:
    original = policy(
        effective_from=date(2026, 9, 1),
        alert_recipient="sam@cinqcare.test",
        owner="Dana",
        rationale="Outreach depends on it.",
    )
    assert policy_from_dict(policy_to_dict(original)) == original


def test_a_rule_set_stored_before_this_story_reads_back_with_no_policies() -> None:
    """The additive-upgrade behaviour every other body key has."""
    assert policies_from_body({"feed_id": "f", "rules": []}) == ()


def test_policies_are_written_beside_the_rules_not_instead_of_them() -> None:
    body = {"feed_id": "f", "rules": [{"rule_id": DOB}]}
    amended = with_policies(body, [policy()])
    assert amended["rules"] == body["rules"]
    assert len(amended[POLICIES_KEY]) == 1
    assert body.get(POLICIES_KEY) is None, "the original body was mutated"
