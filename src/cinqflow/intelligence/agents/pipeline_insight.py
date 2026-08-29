"""CF-V0-E16-10 — the Pipeline Insight Agent, wired.

    "every factual claim carries a citation I can click through to the row it
     came from"
    "Refuse to exceed grounding. Empty or thin tool results degrade to 'needs
     your input' naming what is missing. Thin results are never padded."
    — CF-V0-E16-10

The graph is data in `core/agents/pipeline_insight/graph.py`; these are the
node implementations, which touch pins. Three properties are enforced here and
tested independently:

  1. `execute` calls NO model. Tool execution is the platform's governed
     surface, and a model that could execute tools could choose an uncertified
     one. A test walks this module's AST and asserts the node's body never
     reaches the gateway.

  2. Every claim is checked against the citations the tools ACTUALLY returned.
     A claim citing something no tool produced is dropped and reported in
     `unanswered` — the model does not get to decide what counts as evidence.

  3. Thin grounding degrades. If the tools returned nothing, the answer says
     what is missing and offers no hypothesis. That is the documented exception
     for "the reconciliation row does not exist yet".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinqflow.core.agents.pipeline_insight.graph import (
    AGENT,
    ANSWER_SCHEMA,
    DECLINED_CAPABILITIES,
    INTENT_TOOLS,
    NODE_ANSWER,
    NODE_EXECUTE,
    NODE_PLAN_TOOLS,
    NODE_ROUTE,
    PLAN_SCHEMA,
    RISK_CLASS,
    ROUTE_SCHEMA,
    Intent,
)
from cinqflow.core.citations import CitationId, UnresolvableCitationError, parse
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.tools import spec_for
from cinqflow.intelligence.action_gateway import ActionGateway
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.intelligence.tools import ToolContext, ToolResult, invoke
from cinqflow.ports.agent_runtime import AgentRuntimePort, Edge, GraphSpec

NEEDS_YOUR_INPUT = "needs your input"


@dataclass(frozen=True)
class Claim:
    text: str
    citations: tuple[CitationId, ...]


@dataclass(frozen=True)
class Answer:
    """The agent's structured output, after the platform has checked it."""

    claims: tuple[Claim, ...]
    confidence: str
    unanswered: tuple[str, ...]
    intent: Intent
    tools_called: tuple[str, ...]
    trace: tuple[tuple[str, int], ...] = ()
    cost_usd: str = "0"
    refused: bool = False
    refusal: str = ""

    @property
    def is_grounded(self) -> bool:
        """Every claim carries at least one citation. Zero uncited claims."""
        return all(claim.citations for claim in self.claims)

    def as_text(self) -> str:
        if self.refused:
            return self.refusal
        if not self.claims:
            return (
                f"{NEEDS_YOUR_INPUT}: "
                + ("; ".join(self.unanswered) or "there is nothing recorded to answer from")
            )
        return "\n".join(
            f"{claim.text} [{', '.join(str(c) for c in claim.citations)}]"
            for claim in self.claims
        )


@dataclass
class PipelineInsightAgent:
    """Four nodes, one gateway, one tool context. No credentials of its own."""

    llm: LlmGateway
    tools: ToolContext
    runtime: AgentRuntimePort
    action_gateway: ActionGateway = field(default_factory=ActionGateway)

    # ── the graph ────────────────────────────────────────────────────────────

    def graph(self) -> GraphSpec:
        return GraphSpec(
            name=AGENT,
            nodes={
                NODE_ROUTE: self._route,
                NODE_PLAN_TOOLS: self._plan_tools,
                NODE_EXECUTE: self._execute,
                NODE_ANSWER: self._answer,
            },
            edges=(
                Edge(NODE_ROUTE, NODE_ANSWER, when="declined"),
                Edge(NODE_ROUTE, NODE_PLAN_TOOLS),
                Edge(NODE_PLAN_TOOLS, NODE_EXECUTE),
                Edge(NODE_EXECUTE, NODE_ANSWER),
            ),
            entrypoint=NODE_ROUTE,
            terminal=NODE_ANSWER,
        )

    def ask(self, question: str, *, run_id: str) -> Answer:
        self.tools.run_id = run_id
        try:
            run = self.runtime.execute(self.graph(), {"question": question, "run_id": run_id})
        except ManualPathRequiredError as escalated:
            return Answer(
                claims=(),
                confidence="low",
                unanswered=(str(escalated),),
                intent=Intent.DECLINED,
                tools_called=(),
                refused=True,
                refusal=f"{NEEDS_YOUR_INPUT}: {escalated}",
            )
        answer: Answer = run.state["answer"]
        return Answer(
            **{
                **answer.__dict__,
                "trace": tuple((t.node, t.duration_ms) for t in run.traces),
            }
        )

    # ── node 1 · route (small) ───────────────────────────────────────────────

    def _route(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.llm.complete(
            agent=AGENT,
            run_id=state["run_id"],
            prompt_id="pipeline-insight.route",
            caller=self.tools.actor,
            input_text=state["question"],
        )
        routed = _as_dict(result.value, ROUTE_SCHEMA)
        intent = Intent(routed.get("intent", Intent.DECLINED.value))

        if intent is Intent.DECLINED:
            capability = routed.get("declined_capability", "")
            reason = DECLINED_CAPABILITIES.get(
                capability, "That is not something this agent can do in Wave 0."
            )
            self._record(state, f"declined:{capability or 'unrouted'}",
                         ActionOutcome.REFUSED_NOT_WHITELISTED, reason)
            return {
                "intent": intent,
                "declined": True,
                "answer": Answer(
                    claims=(),
                    confidence="high",
                    unanswered=(),
                    intent=intent,
                    tools_called=(),
                    refused=True,
                    refusal=reason,
                ),
            }
        return {"intent": intent, "declined": False, "routed": routed}

    # ── node 2 · plan_tools (small) ──────────────────────────────────────────

    def _plan_tools(self, state: dict[str, Any]) -> dict[str, Any]:
        intent: Intent = state["intent"]
        available = INTENT_TOOLS[intent]
        result = self.llm.complete(
            agent=AGENT,
            run_id=state["run_id"],
            prompt_id="pipeline-insight.plan",
            caller=self.tools.actor,
            context=(
                f"intent: {intent.value}\n"
                f"available tools: {', '.join(available)}\n"
                f"known ids: {state.get('routed', {})}"
            ),
            input_text=state["question"],
        )
        planned = _as_dict(result.value, PLAN_SCHEMA).get("calls", [])
        routed = state.get("routed", {})

        calls: list[dict[str, Any]] = []
        for call in planned:
            tool = str(call.get("tool", ""))
            # Narrowed to the INTENT's tools, not merely to the whitelist. A
            # question about a definition has no business reading the error log.
            if tool not in available:
                self._record(state, f"tool:{tool}", ActionOutcome.REFUSED_NOT_WHITELISTED,
                             f"{tool} is not available for intent {intent.value}")
                continue
            arguments = {
                key: value
                for key, value in call.items()
                if key != "tool" and key in {p.name for p in spec_for(tool).parameters}
            }
            # Identifiers come from ROUTING, not from the planning model: an id
            # a model invented is an id that reads someone else's feed.
            for key in ("feed_id", "batch_id"):
                if key in routed and key in {p.name for p in spec_for(tool).parameters}:
                    arguments[key] = routed[key]
            if "query" in {p.name for p in spec_for(tool).parameters}:
                arguments.setdefault("query", routed.get("term", state["question"]))
            calls.append({"tool": tool, "arguments": arguments})

        return {"calls": calls}

    # ── node 3 · execute (NO MODEL) ──────────────────────────────────────────

    def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic. This node reaches no model, and a test proves it."""
        results: list[ToolResult] = []
        for call in state.get("calls", []):
            permission = self.action_gateway.permit(call["tool"])
            if not permission:
                self._record(state, f"tool:{call['tool']}", permission.outcome, permission.reason)
                continue
            results.append(invoke(self.tools, call["tool"], call["arguments"]))
        return {"results": tuple(results)}

    # ── node 4 · answer (large) ──────────────────────────────────────────────

    def _answer(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("declined"):
            return {}

        results: tuple[ToolResult, ...] = state.get("results", ())
        available = _citations_of(results)
        called = tuple(result.tool for result in results)

        if not available:
            # Thin grounding degrades and NAMES what is missing. No hypothesis.
            missing = _what_is_missing(results, state["intent"])
            return {
                "answer": Answer(
                    claims=(),
                    confidence="low",
                    unanswered=missing,
                    intent=state["intent"],
                    tools_called=called,
                )
            }

        grounding = "\n\n".join(result.as_grounding() for result in results)
        result = self.llm.complete(
            agent=AGENT,
            run_id=state["run_id"],
            prompt_id="pipeline-insight.answer",
            caller=self.tools.actor,
            context=grounding,
            input_text=state["question"],
        )
        drafted = _as_dict(result.value, ANSWER_SCHEMA)

        claims, dropped = _keep_only_grounded(drafted.get("claims", []), available)
        unanswered = tuple(drafted.get("unanswered", [])) + dropped
        return {
            "answer": Answer(
                claims=claims,
                confidence="low" if dropped else str(drafted.get("confidence", "medium")),
                unanswered=unanswered,
                intent=state["intent"],
                tools_called=called,
                cost_usd=str(result.cost_usd),
            )
        }

    # ── audit ────────────────────────────────────────────────────────────────

    def _record(
        self, state: dict[str, Any], action: str, outcome: ActionOutcome, detail: str
    ) -> None:
        self.tools.metadata.append_agent_action(
            AgentAction(
                run_id=state["run_id"],
                agent=AGENT,
                action=action,
                outcome=outcome,
                actor=self.tools.actor,
                occurred_ts=self.tools.now,
                risk_class=RISK_CLASS,
                detail=detail,
            )
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _as_dict(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """The gateway already validated against the schema; this narrows the type."""
    _ = schema
    return value if isinstance(value, dict) else {}


def _citations_of(results: tuple[ToolResult, ...]) -> frozenset[str]:
    return frozenset(str(c) for result in results for c in result.citations)


def _keep_only_grounded(
    drafted: list[dict[str, Any]], available: frozenset[str]
) -> tuple[tuple[Claim, ...], tuple[str, ...]]:
    """Drop any claim whose citations no tool actually produced.

    The model does not get to decide what counts as evidence. A confident
    sentence citing `batch:9999` when no tool returned that batch is exactly
    the failure this platform exists to make impossible, so it is removed and
    reported rather than shown with a warning nobody reads.
    """
    kept: list[Claim] = []
    dropped: list[str] = []
    for claim in drafted:
        text = str(claim.get("text", "")).strip()
        if not text:
            continue
        citations: list[CitationId] = []
        for raw in claim.get("citation_ids", []):
            if str(raw) not in available:
                dropped.append(
                    f"a claim citing {raw!r} was removed — no tool returned that citation"
                )
                citations = []
                break
            try:
                citations.append(parse(str(raw)))
            except UnresolvableCitationError:
                dropped.append(f"a claim citing {raw!r} was removed — it does not resolve")
                citations = []
                break
        if citations:
            kept.append(Claim(text=text, citations=tuple(citations)))
        elif text:
            dropped.append(f"uncited: {text}")
    return tuple(kept), tuple(dropped)


def _what_is_missing(results: tuple[ToolResult, ...], intent: Intent) -> tuple[str, ...]:
    if not results:
        return (f"no certified tool could be called for a {intent.value} question",)
    missing: list[str] = []
    for result in results:
        if result.out_of_scope:
            missing.append(f"{result.tool}: not available to you, or not recorded")
        elif result.is_empty:
            missing.append(f"{result.tool}: nothing recorded yet")
    return tuple(missing) or ("the tools returned no citable rows",)
