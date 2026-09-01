"""The compiled plan — the IR the engine runs and the agent explains.

    "Approved metadata compiles to a logical plan, and the plan is rendered by
     whichever compute adapter is fitted."
    plan_steps: [read, validate, land_bronze, cast, map, evaluate_rules,
                 resolve_identity, load, reconcile]
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

This artifact does three jobs, and the tests below hold it to all three:

  1. the engine RUNS it            — so it must be complete and ordered
  2. the agent EXPLAINS it         — so every step must name its objects with
                                     resolvable citations
  3. the eval suite GRADES against it — so "which steps exist" must be
                                     computable, giving CF-V0-E16-10 a golden
                                     set with zero annotation cost
"""

from __future__ import annotations

import pytest

from cinqflow.core.citations import CitationKind
from cinqflow.core.compiler.plan import (
    LogicalPlan,
    PlanStep,
    StepKind,
)
from cinqflow.core.model.vocabulary import Layer


@pytest.mark.unit
def test_the_nine_plan_steps_are_the_plate_s_steps_in_order() -> None:
    assert [s.value for s in StepKind] == [
        "read",
        "validate",
        "land_bronze",
        "cast",
        "map",
        "evaluate_rules",
        "resolve_identity",
        "load",
        "reconcile",
    ]


@pytest.mark.unit
def test_a_plan_is_deterministic_from_its_inputs() -> None:
    """Same metadata in, byte-identical plan out — or nothing downstream is
    reproducible, including the agent's explanation of it."""
    a = _roster_plan()
    b = _roster_plan()
    assert a == b
    assert a.fingerprint == b.fingerprint


@pytest.mark.unit
def test_a_plan_changes_its_fingerprint_when_any_input_version_changes() -> None:
    plan = _roster_plan()
    other = _roster_plan(contract_version=2)
    assert plan.fingerprint != other.fingerprint, (
        "a plan that does not notice a contract change would run stale configuration"
    )


@pytest.mark.unit
def test_every_step_carries_a_resolvable_citation() -> None:
    """This is what makes CF-V0-E16-10's citation gate computable with NO model."""
    for step in _roster_plan().steps:
        assert step.citations, f"{step.kind} names no object; it cannot be explained"
        for cid in step.citations:
            assert cid.kind in set(CitationKind)


@pytest.mark.unit
def test_wave_0_stops_at_silver_raw_and_says_so() -> None:
    """Silver ODS sits behind G4 identity resolution — Wave 3, not Wave 0.

    A plan that quietly included resolve_identity would make Wave 0 look
    finished while G4 had never been built.
    """
    plan = _roster_plan()
    assert plan.terminal_layer is Layer.SILVER_RAW
    assert StepKind.RESOLVE_IDENTITY not in plan.step_kinds
    assert StepKind.RECONCILE in plan.step_kinds, "reconciliation is never optional"


@pytest.mark.unit
def test_a_plan_must_end_by_reconciling() -> None:
    """ "Fail the batch loudly if the equation does not balance." A plan with no
    reconcile step cannot fail loudly, so it is refused at compile time."""
    with pytest.raises(ValueError, match="reconcil"):
        LogicalPlan(
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            steps=(_step(StepKind.READ), _step(StepKind.LAND_BRONZE)),
        )


@pytest.mark.unit
def test_steps_must_be_in_the_plate_s_order() -> None:
    """The order is the spine. A plan that maps before it lands would write
    transformed data into an append-only raw layer."""
    with pytest.raises(ValueError, match="order"):
        LogicalPlan(
            feed_id="f",
            feed_version=1,
            steps=(_step(StepKind.MAP), _step(StepKind.READ), _step(StepKind.RECONCILE)),
        )


@pytest.mark.unit
def test_a_plan_carries_no_engine_specific_sql() -> None:
    """ "engine-specific SQL exists only inside compute adapters, never in the core"

    The IR is what the core produces; the dialect is the renderer's business.
    """
    import re

    rendered = repr(_roster_plan())
    # SQL STATEMENTS, not English words. "an untouched copy of the source" is
    # prose about Bronze; "COPY ... FROM STDIN" would be a Postgres dialect
    # leaking out of the renderer and into the core.
    sql_statement = re.compile(
        r"\b(SELECT\s+\w|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
        r"COPY\s+\w+\s+FROM|MERGE\s+INTO|CREATE\s+TABLE|::\w+)",
        re.IGNORECASE,
    )
    assert not sql_statement.search(rendered), (
        "engine-specific SQL exists only inside compute adapters, never in the core"
    )


@pytest.mark.unit
def test_a_plan_explains_itself_in_plain_english() -> None:
    """The DA's actual question is "what will this do to my data?".

    Every sentence must carry the citations for the objects it names, so the
    agent's answer is a rendering of the plan rather than a paraphrase of it.
    """
    narration = _roster_plan().narrate()
    assert len(narration) == len(_roster_plan().steps)
    for line in narration:
        assert line.text
        assert line.citations
        assert not line.text.endswith(" "), "narration is prose, not a fragment"


@pytest.mark.unit
def test_a_feed_specific_branch_cannot_be_expressed() -> None:
    """ "Contain any feed-specific code — everything feed-specific must come
    from metadata." The IR has parameters, not conditionals."""
    assert not hasattr(PlanStep, "condition")
    assert not hasattr(PlanStep, "if_feed")


# ── fixtures ─────────────────────────────────────────────────────────────────
def _step(kind: StepKind) -> PlanStep:
    from cinqflow.core.citations import CitationId

    return PlanStep(
        kind=kind,
        description=f"{kind.value} step",
        citations=(CitationId(kind=CitationKind.FEED, subject="f", version=1),),
    )


def _roster_plan(contract_version: int = 3) -> LogicalPlan:
    from cinqflow.core.compiler.plan import compile_steps

    return LogicalPlan(
        feed_id="fidelis-downstate-roster",
        feed_version=1,
        steps=compile_steps(
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            contract_version=contract_version,
            mapping_version=1,
            file_pattern="_CINQDOWNSTATE_Member_Roster_*.xlsx",
            column_count=47,
            cast_columns=("date_of_birth", "effective_date", "end_date", "term_date"),
            rule_ids=("DQ-002", "DQ-014"),
            target_table="silver_raw.members",
            terminal_layer=Layer.SILVER_RAW,
        ),
        terminal_layer=Layer.SILVER_RAW,
    )
