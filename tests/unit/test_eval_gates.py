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
