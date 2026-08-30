"""The ONE contract suite for the `agent_runtime` pin — its first.

    "every port has real | dev_standin | mock, all passing ONE contract suite"
    — docs/architecture/INVARIANTS.md, chip discipline

`inproc` has run unaudited since Wave 0 (CF-V0-E16-11). `langgraph` is fitted
in Wave 2 because ADR-0018's reverse falsifier tripped — `fingerprint_match`'s
graph genuinely branches on `when="has_grounding"` / `when="novel"`. This file
is where the pin catches up with every other one: ONE suite, run against every
adapter `fitted("agent_runtime")` reports, so a second seat is a CERTIFICATION
rather than a migration nobody can score.

Every graph below leans on the ONE thing `inproc` and `langgraph` must agree
on: `when=None` is the unconditional default edge, `when="key"` fires when
`state[key]` is truthy, and a guarded edge is tried before the default —
first-matching-guarded-edge-wins, regardless of declaration order.

`langgraph` is a Wave-2 dependency (`requirements/agents.txt`), deliberately
absent from the base install. Every case that needs it ACTUALLY installed is
SKIPPED, not failed, when the package is absent — scoped to just that
adapter's parametrized case. The `inproc`/`mock` half of this suite runs and
passes unconditionally either way.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

import pytest

import cinqflow.adapters.langgraph  # noqa: F401 — fits `langgraph` to this pin
from cinqflow.adapters.langgraph import LangGraphAgentRuntime
from cinqflow.ports import fitted
from cinqflow.ports.agent_runtime import AgentRuntimeError, AgentRuntimePort, Edge, GraphSpec

from .conftest import adapters_for

pytestmark = pytest.mark.contract

_LANGGRAPH_INSTALLED = find_spec("langgraph") is not None

# ── fixture 1 · a pure linear graph ──────────────────────────────────────────


def _double(state: dict[str, Any]) -> dict[str, Any]:
    return {"value": state["value"] * 2}


def _increment(state: dict[str, Any]) -> dict[str, Any]:
    return {"value": state["value"] + 1}


def _stamp_finished(state: dict[str, Any]) -> dict[str, Any]:
    return {"finished": True}


LINEAR = GraphSpec(
    name="contract-linear",
    nodes={"double": _double, "increment": _increment, "finish": _stamp_finished},
    edges=(Edge("double", "increment"), Edge("increment", "finish")),
    entrypoint="double",
    terminal="finish",
)

# ── fixture 2 · a branching graph, the `fingerprint_match` shape ────────────


def _assess(state: dict[str, Any]) -> dict[str, Any]:
    grounded = state.get("citations_count", 0) > 0
    return {"has_grounding": grounded, "novel": not grounded}


def _grounded_answer(state: dict[str, Any]) -> dict[str, Any]:
    return {"route": "grounded_answer"}


def _novel_escalation(state: dict[str, Any]) -> dict[str, Any]:
    return {"route": "novel_escalation"}


BRANCHING = GraphSpec(
    name="contract-branching",
    nodes={
        "assess": _assess,
        "grounded_answer": _grounded_answer,
        "novel_escalation": _novel_escalation,
    },
    edges=(
        Edge("assess", "grounded_answer", when="has_grounding"),
        Edge("assess", "novel_escalation", when="novel"),
    ),
    entrypoint="assess",
    terminal="grounded_answer",
)

# ── fixture 3 · a cycle, to prove neither adapter hangs ──────────────────────


def _spin(state: dict[str, Any]) -> dict[str, Any]:
    return {"visits": state.get("visits", 0) + 1}


CYCLIC = GraphSpec(
    name="contract-cyclic",
    nodes={"spin": _spin, "unreached": lambda state: {}},
    edges=(Edge("spin", "spin"),),
    entrypoint="spin",
    terminal="unreached",
)


# ── the fixture every test below parametrizes over ───────────────────────────


@pytest.fixture(params=adapters_for("agent_runtime"))
def runtime(request: pytest.FixtureRequest, make: Callable[..., Any]) -> AgentRuntimePort:
    """Every adapter fitted to `agent_runtime`, discovered via `fitted()` —
    not hardcoded, so a third seat needs no edit here to be certified too.

    The `langgraph` case needs `requirements/agents.txt` installed; it is
    SKIPPED rather than failed when the package is absent, scoped to just this
    one parametrized case — every other adapter still runs unconditionally.
    """
    if request.param is LangGraphAgentRuntime:
        pytest.importorskip("langgraph")
    return make(request.param)


# ── the certification ─────────────────────────────────────────────────────────


def test_a_linear_graph_reaches_the_same_final_state(runtime: AgentRuntimePort) -> None:
    run = runtime.execute(LINEAR, {"value": 5})
    assert run.state == {"value": 11, "finished": True}
    assert run.completed is True
    assert [t.node for t in run.traces] == ["double", "increment", "finish"]
    assert run.traces[-1].state_keys_added == ("finished",)


@pytest.mark.parametrize(
    ("citations_count", "expected_route", "expected_path"),
    [
        pytest.param(3, "grounded_answer", ["assess", "grounded_answer"], id="grounded"),
        pytest.param(0, "novel_escalation", ["assess", "novel_escalation"], id="novel"),
    ],
)
def test_a_guarded_edge_picks_the_same_branch_on_every_adapter(
    runtime: AgentRuntimePort,
    citations_count: int,
    expected_route: str,
    expected_path: list[str],
) -> None:
    run = runtime.execute(BRANCHING, {"citations_count": citations_count})
    assert run.state["route"] == expected_route
    assert [t.node for t in run.traces] == expected_path


def test_a_cycle_is_bounded_not_hung(runtime: AgentRuntimePort) -> None:
    """Both adapters document a bounded loop, never a hang, against a spec
    with a cycle — the guardrail against a request that never returns.
    `max_steps` is set small so a real hang would show up as a slow test, not
    a stuck one."""
    bounded = type(runtime)(max_steps=5)
    with pytest.raises(AgentRuntimeError):
        bounded.execute(CYCLIC, {})


# ── the cross-adapter comparison, made explicit ──────────────────────────────


def test_every_fitted_adapter_agrees_on_the_linear_graph(make: Callable[..., Any]) -> None:
    """The certification, made literal — not merely implied by every adapter
    happening to satisfy the same per-test assertion above. Built directly off
    `fitted()`, so this stays true when a third adapter is fitted with no edit
    here."""
    candidates = dict(fitted("agent_runtime"))
    if not _LANGGRAPH_INSTALLED:
        candidates.pop("langgraph", None)

    results = {
        name: make(factory).execute(LINEAR, {"value": 5}) for name, factory in candidates.items()
    }
    assert len(results) >= 2, "fit a second adapter before trusting this certification"
    if _LANGGRAPH_INSTALLED:
        assert "langgraph" in results

    states = {name: dict(run.state) for name, run in results.items()}
    reference_name, reference_state = next(iter(states.items()))
    for name, state in states.items():
        assert state == reference_state, f"{name} diverges from {reference_name}: {state}"


def test_every_fitted_adapter_agrees_on_the_branch_taken(make: Callable[..., Any]) -> None:
    candidates = dict(fitted("agent_runtime"))
    if not _LANGGRAPH_INSTALLED:
        candidates.pop("langgraph", None)

    for citations_count, expected in ((3, "grounded_answer"), (0, "novel_escalation")):
        routes = {
            name: make(factory)
            .execute(BRANCHING, {"citations_count": citations_count})
            .state["route"]
            for name, factory in candidates.items()
        }
        assert set(routes.values()) == {expected}, routes
