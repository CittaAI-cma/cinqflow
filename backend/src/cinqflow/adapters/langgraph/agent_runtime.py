"""langgraph — the Wave-2 runtime seat, fitted. ADR-0018.

    "It runs as a PURE ORCHESTRATION SUBSTRATE: nodes are our functions calling
     our services. No ChatOpenAI, no LangChain prompt template, no LangChain
     tool node, no LangChain retriever."
    "The runtime NEVER owns the model call, the prompt text, the tool
     whitelist, the risk class, or any state the lifecycle engine and proposal
     store already own."
    — ADR-0018

    never_owns: [model_call, prompt_text, tool_whitelist, risk_class,
                 lifecycle_state]
    — plate 11

WHY IT ARRIVES NOW AND NOT IN WAVE 0. The Wave-0 agent is four linear nodes and
`inproc` runs it in about 150 lines we control. Wave 2 brings an agent that
genuinely branches — `fingerprint_match` forks on `has_grounding` / `novel` and
hands off. Checkpointed resume and streaming become real work rather than
speculative ergonomics — which is the condition ADR-0018 set for building this
adapter at all (the reverse falsifier).

THE EGRESS PROBLEM, AND THE CONFORMANCE CHECK IT OWES. langgraph pulls
langchain-core, which pulls `langsmith`. That telemetry client is inert unless
`LANGSMITH_TRACING` is set — but this platform's invariant is *"only endpoints
declared in the connection profile may be called"*, and "inert unless" is not a
guarantee, it is a default. So:

  • `_silence_telemetry()` sets the kill variables at import time, before any
    langchain module is imported;
  • `conformance/checks/egress.py` asserts they are set, proving the switch
    actually fired rather than merely trusting this docstring;
  • the profile gains `agent_runtime: {adapter: inproc, tracing: off}` even
    while `inproc` stays the shipped default — the flag travels with the pin,
    not with whichever adapter happens to be chosen.

A dependency that phones home is not a bug we fix later. It is a pin we certify.

WHAT THE ADAPTER ACTUALLY DOES. It translates our `GraphSpec` into a LangGraph
`StateGraph` and runs it. The translation is total and mechanical — every node
becomes a callable that wraps OUR function, every edge becomes an edge or a
conditional edge — and the ONE contract suite in
`tests/contract/test_agent_runtime_contract.py` runs against both adapters, so
fitting this is a CERTIFICATION rather than a migration.

A NODE'S RETURN VALUE IS A PARTIAL UPDATE, NOT THE WHOLE STATE — same
convention `inproc` uses (`state.update(produced)`). The wrapper below merges
`produced` onto the incoming state before handing it back to LangGraph, rather
than letting `produced` replace it outright: our node functions (see
`intelligence/agents/pipeline_insight.py`) return only the keys they add or
change, and a runtime that dropped everything else would silently diverge from
`inproc` on every node after the first — exactly the drift the ONE contract
suite exists to catch.
"""

from __future__ import annotations

import os
import time
from collections.abc import Hashable
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.agent_runtime import (
    AgentRuntimeError,
    Edge,
    GraphRun,
    GraphSpec,
    NodeTrace,
)


def _silence_telemetry() -> None:
    """Pin the telemetry client off BEFORE langchain is imported.

    Set rather than defaulted, and set here rather than in a profile note,
    because the profile is read at runtime and these are read at import. A
    variable the process never sets is a variable an operator can accidentally
    export.
    """
    for name, value in (
        ("LANGSMITH_TRACING", "false"),
        ("LANGCHAIN_TRACING_V2", "false"),
        ("LANGCHAIN_ENDPOINT", ""),
        ("LANGSMITH_ENDPOINT", ""),
        ("LANGCHAIN_API_KEY", ""),
    ):
        os.environ[name] = value


_silence_telemetry()

#: The state key the terminal node writes to signal completion. Ours, not
#: LangGraph's — the runtime does not get to define our vocabulary. Popped
#: back out of the final state before it is handed to the caller, same as
#: `__traces__` — a caller must never see either adapter's own bookkeeping.
_DONE = "__cinqflow_terminal__"
_TRACE_KEY = "__traces__"


@port("agent_runtime", "langgraph")
class LangGraphAgentRuntime:
    """Checkpointed, branching, streaming execution of OUR graphs.

    Holds no credentials — same as `inproc`, and for the same reason: a node
    attempting a model call outside the gateway must fail because there is
    nothing here to authenticate with.

    `checkpointer` is injected. At rung 0.5 it is LangGraph's Postgres
    checkpointer pointed at the database we already run (its driver is the
    `psycopg` already in `base.txt`); in Lane 1 it is None, so the contract
    suite runs credential-free and deterministic exactly as it does today.
    """

    def __init__(self, *, checkpointer: Any | None = None, max_steps: int = 64) -> None:
        self._checkpointer = checkpointer
        self._max_steps = max_steps
        self._compiled: dict[str, Any] = {}

    # ── translation ──────────────────────────────────────────────────────────

    def _build(self, graph: GraphSpec) -> Any:
        """GraphSpec → StateGraph. Total, mechanical, cached per graph name.

        Imported INSIDE the method, not at module scope, so that a deployment
        without `requirements/agents.txt` installed fails with a clear message
        at fit time rather than an ImportError at import time — and so `core/`
        remains importable in an environment where langgraph is absent, which
        is every environment before Wave 2.
        """
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as missing:  # pragma: no cover - environment shape
            raise AgentRuntimeError(
                "the langgraph adapter is fitted but langgraph is not installed. "
                "It ships in requirements/agents.txt, which is deliberately absent "
                "from the Wave-0/1 image (ADR-0018)."
            ) from missing

        # `dict` is not itself `StateLike` — langgraph's stub wants a TypedDict,
        # a dataclass or a pydantic model, so it can build one reducer per
        # declared field. Our graphs are per-agent and their keys are not known
        # here; empirically (langgraph 1.2.11), a bare `dict` schema is treated
        # as ONE channel with a last-value reducer — precisely the untyped,
        # whole-state-replaced-per-node shape `_wrap` already produces. The
        # mismatch is between the stub's declared bound and a genuinely
        # supported runtime path, not a bug in this call.
        # `unused-ignore` is NOT redundancy. langgraph ships in
        # requirements/agents.txt, which ADR-0018 deliberately keeps out of the
        # Wave-0/1 image — so in CI `StateGraph` is untyped `Any` and the
        # `type-var` ignore has nothing to suppress, which `strict = true`
        # reports as an unused ignore and fails the lint gate on. The second
        # code makes the line correct in BOTH images rather than in whichever
        # one the author happened to have installed.
        builder = StateGraph(dict)  # type: ignore[type-var, unused-ignore]

        for name, fn in graph.nodes.items():
            builder.add_node(name, _wrap(name, fn, graph.terminal))

        builder.add_edge(START, graph.entrypoint)

        for source in graph.nodes:
            outgoing = [e for e in graph.edges if e.source == source]
            if source == graph.terminal or not outgoing:
                builder.add_edge(source, END)
                continue
            if len(outgoing) == 1 and outgoing[0].when is None:
                builder.add_edge(source, outgoing[0].target)
                continue
            builder.add_conditional_edges(source, _router(outgoing), _targets(outgoing))

        return builder.compile(checkpointer=self._checkpointer)

    # ── execution ────────────────────────────────────────────────────────────

    def execute(self, graph: GraphSpec, initial_state: dict[str, Any]) -> GraphRun:
        """The port's one verb. Same signature, same guarantees, as `inproc`.

        `traces` is rebuilt from the state the wrapper accumulates rather than
        from LangGraph's own event stream: the contract suite asserts a trace
        per visited node with a duration, and deriving that from our own
        wrapper keeps the assertion true whatever the framework's stream shape
        does across minor versions.
        """
        compiled = self._compiled.get(graph.name)
        if compiled is None:
            compiled = self._compiled[graph.name] = self._build(graph)

        state = dict(initial_state)
        state.setdefault(_TRACE_KEY, [])

        config: dict[str, Any] = {"recursion_limit": self._max_steps}
        if self._checkpointer is not None:
            thread = state.get("run_id") or graph.name
            config["configurable"] = {"thread_id": str(thread)}

        try:
            final = compiled.invoke(state, config=config)
        except Exception as failure:  # the port declares exactly one error type
            raise AgentRuntimeError(f"{graph.name}: {failure}") from failure

        raw = final.pop(_TRACE_KEY, [])
        # Popped, not left behind: a caller comparing this adapter's state
        # against `inproc`'s must see the SAME keys, or "one contract suite,
        # identical final state" is a claim nobody checked.
        completed = bool(final.pop(_DONE, True))
        traces = tuple(
            NodeTrace(
                node=str(t["node"]),
                duration_ms=int(t["duration_ms"]),
                state_keys_added=tuple(t["added"]),
            )
            for t in raw
        )
        return GraphRun(
            graph=graph.name,
            state=final,
            traces=traces,
            completed=completed,
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _wrap(name: str, fn: Any, terminal: str) -> Any:
    """Wrap one of OUR node functions so the framework can call it.

    The wrapper does three things and nothing else: time the call, MERGE the
    keys it added onto the incoming state (never replace the state outright —
    `inproc` merges with `state.update(produced)` and a node here returns only
    the keys it changed, same as every node in
    `intelligence/agents/pipeline_insight.py`), and flag the terminal. It does
    not interpret the return value further, catch its exceptions, or inject
    anything else — a wrapper that starts making decisions is a runtime that
    has started owning graph semantics.
    """

    def node(state: dict[str, Any]) -> dict[str, Any]:
        before = set(state)
        started = time.perf_counter()
        produced = fn(state)
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not isinstance(produced, dict):
            raise AgentRuntimeError(f"{name} returned {type(produced)!r}, not a dict")

        traces = list(state.get(_TRACE_KEY, []))
        traces.append(
            {
                "node": name,
                "duration_ms": duration_ms,
                "added": tuple(sorted(set(produced) - before)),
            }
        )
        out = dict(state)
        out.update(produced)
        out[_TRACE_KEY] = traces
        if name == terminal:
            out[_DONE] = True
        return out

    node.__name__ = f"cinqflow_{name}"
    return node


def _router(outgoing: list[Edge]) -> Any:
    """Guarded edges before the default — identical semantics to `inproc`.

    Sharing the rule matters more than sharing the code: the ONE contract suite
    asserts that both runtimes pick the same branch for the same state, and
    that assertion is only meaningful because this ordering is deliberate here
    rather than inherited by luck.
    """

    def choose(state: dict[str, Any]) -> str:
        for edge in sorted(outgoing, key=lambda e: e.when is None):
            if edge.when is None or state.get(edge.when):
                return edge.target
        return "__end__"

    return choose


def _targets(outgoing: list[Edge]) -> dict[Hashable, str]:
    """`dict[Hashable, str]`, not `dict[str, str]`: `add_conditional_edges`
    wants the former, and dict key types are invariant under mypy — a
    `dict[str, str]` is not accepted where `dict[Hashable, str]` is, even
    though every key here is in fact a `str`."""
    from langgraph.graph import END

    mapping: dict[Hashable, str] = {e.target: e.target for e in outgoing}
    mapping["__end__"] = END
    return mapping
