"""inproc — the Wave-0 agent runtime. Deterministic, no persistence, NO CREDENTIALS.

    adapters: {inproc: wave0_deterministic_no_credentials,
               langgraph: wave2_checkpointed_streaming}
    — docs/architecture/plates/11-agent-runtime-and-the-risk-router.md

This is both the mock AND the dev adapter, which is unusual and deliberate: the
Wave-0 graph is four linear nodes, and a runtime that executes it needs about
this much code. Fitting LangGraph here would buy checkpointed resume and
branching that nothing yet uses, at the cost of a dependency chain that pulls a
telemetry client into a platform whose invariant is "only endpoints declared in
the connection profile may be called".

So LangGraph is a Wave-2 SEAT (ADR-0018), and this is the Lane-1 runner.

The runtime holds no credentials. That is what makes CF-V0-E16-11's guardrail
true by construction: a node attempting a model call outside the gateway fails
because there is nothing here to authenticate with.
"""

from __future__ import annotations

import time
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.agent_runtime import AgentRuntimeError, GraphRun, GraphSpec, NodeTrace


@port("agent_runtime", "mock")
class InProcAgentRuntime:
    """Linear and conditional execution. No persistence. Fully deterministic."""

    def __init__(self, *, max_steps: int = 32) -> None:
        # A cycle in a graph spec would otherwise hang a request. Bounded, and
        # the refusal names the graph rather than timing out anonymously.
        self._max_steps = max_steps

    def execute(self, graph: GraphSpec, initial_state: dict[str, Any]) -> GraphRun:
        state = dict(initial_state)
        traces: list[NodeTrace] = []
        current: str | None = graph.entrypoint
        steps = 0

        while current is not None:
            steps += 1
            if steps > self._max_steps:
                raise AgentRuntimeError(
                    f"{graph.name}: exceeded {self._max_steps} steps — the edge "
                    "specification has a cycle"
                )

            before = set(state)
            started = time.perf_counter()
            produced = graph.nodes[current](state)
            duration_ms = int((time.perf_counter() - started) * 1000)

            if not isinstance(produced, dict):  # pragma: no cover - defensive
                raise AgentRuntimeError(f"{graph.name}.{current} returned {type(produced)!r}")
            state.update(produced)

            traces.append(
                NodeTrace(
                    node=current,
                    duration_ms=duration_ms,
                    state_keys_added=tuple(sorted(set(state) - before)),
                )
            )
            current = None if current == graph.terminal else self._next(graph, current, state)

        return GraphRun(graph=graph.name, state=state, traces=tuple(traces), completed=True)

    @staticmethod
    def _next(graph: GraphSpec, current: str, state: dict[str, Any]) -> str | None:
        """First matching edge wins. Guarded edges are tried before the default,
        so a fallback edge cannot shadow a condition by declaration order."""
        outgoing = [e for e in graph.edges if e.source == current]
        for edge in sorted(outgoing, key=lambda e: e.when is None):
            if edge.when is None or state.get(edge.when):
                return edge.target
        return None
