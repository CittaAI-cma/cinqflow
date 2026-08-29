"""The `agent_runtime` pin — execute an agent graph.

    verb: execute_agent_graph   mock: inproc   dev: inproc
    target: langgraph_from_wave2
    — docs/architecture/plates/04-pin-out-map.md

    never_owns: [model_call, prompt_text, tool_whitelist, risk_class,
                 lifecycle_state]
    — docs/architecture/plates/11-agent-runtime-and-the-risk-router.md

CF-V0-E16-11 exists so Law 1 survives contact with agent orchestration. Graphs
are declared in core/agents/<agent>/graph.py as DATA — nodes are pure
functions, edges are a spec — and a runtime merely executes them. The
`graphs-are-data` import contract enforces the absence of a runtime import
under core/.

What this buys: a checkpointed graph engine (LangGraph, Wave 2) is fitted as an
ADAPTER SWAP rather than a rewrite, and the one contract suite makes that
second runtime a CERTIFICATION rather than a migration.

What a runtime may never own is the list above. Four of the five things a graph
framework typically provides already exist here as governed product — the
lifecycle engine is the state machine, the proposal lifecycle is the
human-in-the-loop, the action gateway is the tool layer, and prompt hash plus
audit.agent_action is the trace. What is genuinely missing is graph ergonomics,
checkpointed resume and streaming, and that is all a runtime is allowed to be.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# A node is a pure function over state. It calls OUR services — the gateway,
# the action gateway — and holds no credentials of its own.
NodeState = Mapping[str, Any]
NodeFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Edge:
    """One transition. `when` names a state key that must be truthy.

    A predicate FUNCTION would make the graph code rather than data, and the
    graph has to remain inspectable — CF-V0-E16-11 requires a run to be
    reproducible from the prompt hashes alone.
    """

    source: str
    target: str
    when: str | None = None


@dataclass(frozen=True)
class GraphSpec:
    """An agent's workflow, as data.

    The Wave-0 Pipeline Insight Agent is four nodes and linear:
        route(small) -> plan_tools(small) -> execute(NO MODEL) -> answer(large)
    """

    name: str
    nodes: Mapping[str, NodeFn]
    edges: tuple[Edge, ...]
    entrypoint: str
    terminal: str

    def __post_init__(self) -> None:
        for edge in self.edges:
            for end in (edge.source, edge.target):
                if end not in self.nodes:
                    raise ValueError(f"{self.name}: edge names unknown node {end!r}")
        for name in (self.entrypoint, self.terminal):
            if name not in self.nodes:
                raise ValueError(f"{self.name}: {name!r} is not a node")


@dataclass(frozen=True)
class NodeTrace:
    """Entry and exit of one node. Traced so a run is reproducible."""

    node: str
    duration_ms: int
    state_keys_added: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GraphRun:
    graph: str
    state: Mapping[str, Any]
    traces: tuple[NodeTrace, ...]
    completed: bool = True


class AgentRuntimeError(RuntimeError):
    """The graph could not be executed."""


@runtime_checkable
class AgentRuntimePort(Protocol):
    def execute(self, graph: GraphSpec, initial_state: dict[str, Any]) -> GraphRun:
        """Run the graph. Trace every node's entry and exit.

        The runtime does not decide what a node may do — the action gateway
        does, and it is non-bypassable.
        """
        ...
