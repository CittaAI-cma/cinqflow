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
from cinqflow.intelligence.schemas import InterpretationResponse
from cinqflow.workflow.models import Claim, InterpretationContent, ProfileFacts


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

    def _assemble(self, state: State) -> State:
        """No model. Validates, drops unusable claims, keeps the record structured."""
        raw = state.get("raw") or {}
        claims: list[Claim] = []
        dropped: list[str] = []

        for candidate in raw.get("claims", []) or []:
            try:
                claim = Claim.model_validate(candidate)
            except Exception:
                dropped.append(f"malformed claim discarded: {str(candidate)[:120]}")
                continue
            if not claim.evidence:
                dropped.append(f"claim without evidence discarded: {claim.field}")
                continue
            claims.append(claim)

        content = InterpretationContent(
            claims=claims,
            risks=[str(r) for r in (raw.get("risks") or [])],
            unknowns=[str(u) for u in (raw.get("unknowns") or [])] + dropped,
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
