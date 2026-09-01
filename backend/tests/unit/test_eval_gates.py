"""The gates that grade the agent, graded themselves.

    "Plan-step coverage >= 98% · invented steps EXACTLY 0 · citation
     resolvability 100% · numeric fidelity 100%"
    — CF-V0-E16-10

These run in Lane 1 because the GATES are machinery. What they measure is a
Lane-3 quality claim; that a fabricated step fails the gate is not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cinqflow.core.compiler.plan import LogicalPlan, PlanStep, StepKind
from cinqflow.core.model.vocabulary import Layer
from cinqflow.intelligence.evals import (
    RunBudget,
    citation_fidelity,
    numeric_fidelity,
    plan_fidelity,
)

pytestmark = [pytest.mark.unit, pytest.mark.lane1]

FULL = (
    "It reads the file, validates 47 columns against the contract, lands bronze, "
    "casts four dates, maps the source names, evaluates the DQ rules, loads "
    "silver_raw and reconciles."
)


def _plan() -> LogicalPlan:
    return LogicalPlan(
        feed_id="fidelis-downstate-roster",
        feed_version=1,
        terminal_layer=Layer.SILVER_RAW,
        steps=tuple(
            PlanStep(kind=kind, description=kind.value)
            for kind in (
                StepKind.READ,
                StepKind.VALIDATE,
                StepKind.LAND_BRONZE,
                StepKind.CAST,
                StepKind.MAP,
                StepKind.EVALUATE_RULES,
                StepKind.LOAD,
                StepKind.RECONCILE,
            )
        ),
    )


def test_a_complete_explanation_passes_the_coverage_gate() -> None:
    fidelity = plan_fidelity(_plan(), FULL)
    assert fidelity.coverage == 1.0
    assert fidelity.invented == ()
    assert fidelity.passes


def test_a_missing_step_fails_the_coverage_gate_and_names_it() -> None:
    fidelity = plan_fidelity(_plan(), "It reads the file and reconciles.")
    assert not fidelity.passes
    assert "validate" in fidelity.missed
    assert "coverage 25%" in fidelity.explain()


@pytest.mark.parametrize(
    "fabrication",
    [
        "deduplicates the roster",
        "enriches each member",
        "normalises the plan codes",
        "anonymizes the file",
        "backfills last month",
    ],
)
def test_a_fabricated_step_is_a_hard_failure(fabrication: str) -> None:
    """Exactly zero — a percentage would make one invention in fifty a pass."""
    fidelity = plan_fidelity(_plan(), f"{FULL} Then it {fabrication}.")
    assert fidelity.coverage == 1.0
    assert fidelity.invented, "an invention that conjugates is still an invention"
    assert not fidelity.passes


def test_claiming_a_step_the_wave_0_plan_does_not_run_is_an_invention() -> None:
    """Wave 0 stops at Silver Raw. `resolve_identity` is Wave 3 (gate G4)."""
    fidelity = plan_fidelity(_plan(), f"{FULL} It then resolves identity into Silver ODS.")
    assert "resolve_identity" in fidelity.invented
    assert not fidelity.passes


# ── the accusation this gate used to make ────────────────────────────────────


#: WORD FOR WORD what the Pipeline Insight Agent answered on the real endpoint
#: when asked what the roster plan does. Every claim is true, every step it
#: names is in the plan, and it names no step that is not — and the gate failed
#: it for an invented `resolve_identity`, because `match` was an alias for that
#: step and the roster's file names match a pattern.
#:
#: Kept verbatim rather than paraphrased: a paraphrase would drift away from
#: the sentence that actually broke this, which is the only sentence that
#: proves the fix.
A_REAL_ANSWER = (
    "The plan reads files whose names match the pattern "
    "^_CINQDOWNSTATE_Member_Roster_\\d{8}\\.xlsx$. "
    "It validates that each file has exactly 3 columns. "
    "It lands the data to a bronze layer in append-only mode. "
    "It casts the date_of_birth column to the expected type. "
    "It applies a mapping using mapping_version 1. "
    "It evaluates data quality rule DQ-002. "
    "It loads the processed data into the silver_raw.members table. "
    "It performs reconciliation at the silver_raw terminal layer."
)


def test_a_true_answer_is_not_accused_of_inventing_a_step() -> None:
    """The false positive that made this gate unusable.

    A gate that fails correct answers is not a strict gate; it is a broken one,
    and the failure mode is that somebody mutes it and stops reading the two
    real hallucinations it would have caught next week.
    """
    fidelity = plan_fidelity(_plan(), A_REAL_ANSWER)
    assert fidelity.invented == (), fidelity.explain()
    assert fidelity.coverage == 1.0
    assert fidelity.passes


@pytest.mark.parametrize(
    "sentence",
    [
        "It reads files whose names match the pattern.",
        "It writes the rows the contract expects.",
        "The counts balance at the end of the run.",
        "It checks the types of every column.",
        "It renames the source columns.",
    ],
)
def test_an_ordinary_english_verb_is_not_a_claimed_step(sentence: str) -> None:
    """`match`, `writes`, `balance`, `types` and `renames` all earn COVERAGE
    credit for the steps they describe, and none of them may accuse an answer
    of claiming a step that will not run. Two questions, two tables."""
    fidelity = plan_fidelity(_plan(), f"{FULL} {sentence}")
    assert fidelity.invented == (), fidelity.explain()


def test_naming_an_absent_step_outright_is_still_caught() -> None:
    """The fix narrows the accusation; it does not withdraw it."""
    for named in ("resolve_identity", "it performs identity resolution"):
        fidelity = plan_fidelity(_plan(), f"{FULL} Then {named}.")
        assert "resolve_identity" in fidelity.invented, named


def test_every_step_can_be_named_unambiguously() -> None:
    """A step with no unambiguous word could never be reported as invented, so
    a new `StepKind` that nobody adds a word for would silently leave the gate."""
    from cinqflow.core.compiler.plan import StepKind as Kinds
    from cinqflow.intelligence.evals import _UNAMBIGUOUS

    assert set(_UNAMBIGUOUS) == {kind.value for kind in Kinds}
    assert all(_UNAMBIGUOUS[kind.value] for kind in Kinds)


def test_numeric_fidelity_accepts_numbers_the_grounding_supports() -> None:
    result = numeric_fidelity(
        "22,000 rows arrived; 21,820 loaded and 175 were quarantined.",
        "records_in=22000 records_out=21820 quarantined=175 attributed_drops=5",
    )
    assert result.passes
    assert len(result.quoted) == 3


def test_a_transposed_digit_fails_numeric_fidelity() -> None:
    result = numeric_fidelity("21,819 rows loaded.", "records_out=21820")
    assert not result.passes
    assert result.unsupported == ("21,819",)


def test_citation_fidelity_is_all_or_nothing() -> None:
    good = citation_fidelity(("recon:8842#DQ-002", "feed:fidelis-downstate-roster@v1"))
    assert good.passes and good.resolvable == 2

    bad = citation_fidelity(("recon:8842", "member:12345"))
    assert not bad.passes
    assert bad.unresolvable == ("member:12345",), (
        "there is no citation kind addressing a member, and there never will be"
    )


def test_the_run_budget_gates_p95_latency_and_cost_per_run() -> None:
    budget = RunBudget()
    assert budget.check(latencies_ms=(1200, 2400, 3100), costs=(Decimal("0.01"),)) == ()

    slow = budget.check(latencies_ms=(1000, 9000, 9500), costs=())
    assert slow and "p95" in slow[0]

    dear = budget.check(latencies_ms=(), costs=(Decimal("0.09"),))
    assert dear and "over the $0.05 cap" in dear[0]
