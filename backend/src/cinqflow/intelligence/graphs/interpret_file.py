"""interpret_file graph: ground (no model) -> infer (model) -> assemble (no model).

Only the middle node calls a model. Grounding and assembly are deterministic, so
what reaches the store is always a validated artifact, never raw model output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cinqflow.intelligence import prompts
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.llm import LlmClient
from cinqflow.intelligence.schemas import InterpretationResponse, LlmSignal
from cinqflow.workflow.models import (
    Claim,
    InterpretationContent,
    ProfileFacts,
    RecommendedAction,
    Signal,
)


class State(TypedDict, total=False):
    # inputs
    facts: dict[str, Any]
    source_system: str
    feed: str
    # ground
    payload: dict[str, Any]
    citations: list[str]
    prompt_citation: str
    system: str
    # infer
    raw: dict[str, Any]
    # assemble
    content: dict[str, Any]


class InterpretFileGraph:
    """Built once, run per job. Dependencies are injected, not imported."""

    name = "interpret_file"

    def __init__(self, *, context_builder: ContextBuilder, llm: LlmClient) -> None:
        self.context_builder = context_builder
        self.llm = llm
        self._compiled = self._build().compile()

    # ------------------------------------------------------------------ nodes
    def _ground(self, state: State) -> State:
        """No model. Selects knowledge and freezes the payload the model sees."""
        facts = ProfileFacts.model_validate(state["facts"])
        job = self.context_builder.for_interpretation(
            facts=facts, source_system=state["source_system"], feed=state["feed"]
        )
        system, prompt_citation = prompts.load(self.name)
        return {
            "payload": {"observations": job.observations, "context": job.context},
            "citations": job.citations,
            "prompt_citation": prompt_citation,
            "system": system,
        }

    def _infer(self, state: State) -> State:
        """The only model call in the graph."""
        raw = self.llm.complete_json(
            system=state["system"],
            user=json.dumps(state["payload"], sort_keys=True, separators=(",", ":")),
            response_model=InterpretationResponse,
        )
        return {"raw": raw}

    @staticmethod
    def _headline(signals: list[Signal]) -> tuple[str, RecommendedAction]:
        """Composed from the final signal list, never asked of the model - same
        rule as the S2 verdict sentence (`ProfileFacts`, not the interpretation).
        `info`-severity signals (bookkeeping about discarded model output) never
        drive this; they're visible in evidence, not in the synthesis."""
        blockers = [s for s in signals if s.severity == "blocker"]
        risks = [s for s in signals if s.severity == "warn"]
        if blockers:
            n = len(blockers)
            return (
                f"{n} unknown{'s' if n != 1 else ''} unresolved — review before approving.",
                "review_first",
            )
        if risks:
            n = len(risks)
            return (
                f"{n} risk{'s' if n != 1 else ''} to check — likely still approvable.",
                "approve",
            )
        return ("Clean interpretation — no risks or unknowns found.", "approve")

    @staticmethod
    def _dropped(reason: str) -> Signal:
        """One discarded model candidate (a claim or a signal), recorded as an
        `info` signal rather than silently lost - same audit-trail intent the
        old `unknowns` bucket served for dropped claims."""
        return Signal(
            kind="unknown",
            claim=reason,
            basis="Assembled deterministically from the model's raw output.",
            check="Open Forensic mode to see the discarded candidate verbatim.",
            consequence="Nothing was written for this candidate; it is simply absent.",
            severity="info",
        )

    def _assemble(self, state: State) -> State:
        """No model. Validates, drops unusable claims and signals, keeps the
        record structured, and composes the headline/recommended_action."""
        raw = state.get("raw") or {}
        claims: list[Claim] = []
        signals: list[Signal] = []

        for candidate in raw.get("claims", []) or []:
            try:
                claim = Claim.model_validate(candidate)
            except Exception:
                signals.append(self._dropped(f"malformed claim discarded: {str(candidate)[:120]}"))
                continue
            if not claim.evidence:
                signals.append(self._dropped(f"claim without evidence discarded: {claim.field}"))
                continue
            claims.append(claim)

        for candidate in raw.get("signals", []) or []:
            try:
                parsed = LlmSignal.model_validate(candidate)
            except Exception:
                signals.append(self._dropped(f"malformed signal discarded: {str(candidate)[:120]}"))
                continue
            if not parsed.basis.strip():
                # Same rule as claims: a statement without a basis is not one.
                signals.append(
                    self._dropped(f"signal without basis discarded: {parsed.claim[:80]}")
                )
                continue
            signals.append(
                Signal(
                    kind=parsed.kind,
                    claim=parsed.claim,
                    basis=parsed.basis,
                    check=parsed.check,
                    consequence=parsed.consequence,
                    severity="blocker" if parsed.kind == "unknown" else "warn",
                )
            )

        headline, recommended_action = self._headline(signals)
        content = InterpretationContent(
            claims=claims,
            signals=signals,
            headline=headline,
            recommended_action=recommended_action,
        )
        return {"content": content.model_dump()}

    def _build(self) -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("ground", self._ground)
        graph.add_node("infer", self._infer)
        graph.add_node("assemble", self._assemble)
        graph.add_edge(START, "ground")
        graph.add_edge("ground", "infer")
        graph.add_edge("infer", "assemble")
        graph.add_edge("assemble", END)
        return graph

    # -------------------------------------------------------------------- run
    def run(
        self,
        *,
        facts: ProfileFacts,
        source_system: str,
        feed: str,
        on_step: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Runs the graph node by node instead of invoking it whole, so a caller
        can be told the moment each node finishes - `on_step` is the only reason
        this streams rather than invokes; the result is identical either way."""
        state = {"facts": facts.model_dump(), "source_system": source_system, "feed": feed}
        final: dict[str, Any] = dict(state)
        for update in self._compiled.stream(state, stream_mode="updates"):
            for node, partial in update.items():
                final.update(partial)
                if on_step is not None:
                    on_step(node)
        return {
            "content": InterpretationContent.model_validate(final["content"]),
            "prompt": final["prompt_citation"],
            "knowledge": final.get("citations", []),
            "model": self.llm.model_id,
        }
