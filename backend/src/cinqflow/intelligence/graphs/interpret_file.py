"""interpret_file graph: ground (no model) -> infer (model) -> assemble (no model).

Only the middle node calls a model. Grounding and assembly are deterministic, so
what reaches the store is always a validated artifact, never raw model output.

v3 (PR-6) adds column roles. The profiler's deterministic `hint` per column
(engine/profiler.py) goes to the model as an observation; the model classifies
*against* it and must say why when it disagrees. `_assemble` then applies the
same discipline claims and signals already get: only observed columns survive,
an invalid role becomes `unclassified`, a column the model skipped falls back to
its hint, a `technical` column is never above `low` importance, a role that
contradicts the hint without a reason is put back to the hint - each correction
recorded as an `info` signal. Anomalies the v2 facts already state (a column
that is entirely null, constant, sentinel-heavy, a duplicate rate, a null rate)
become `risk` signals here, deterministically, so they exist whatever provider
answered - the model explains, it does not detect.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cinqflow.intelligence import prompts
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.llm import LlmClient
from cinqflow.intelligence.schemas import (
    COLUMN_ROLES,
    IMPORTANCE_LEVELS,
    InterpretationResponse,
    LlmColumnRole,
    LlmSignal,
)
from cinqflow.workflow.models import (
    Claim,
    ColumnRoleOut,
    InterpretationContent,
    ProfileFacts,
    RecommendedAction,
    Signal,
)

#: A date column whose placeholder values exceed this share of its non-null
#: values is "sentinel-heavy" (RR-23).
SENTINEL_HEAVY_RATIO = 0.05
#: A column null in at least this share of rows is worth a signal.
NULL_RATE_SIGNAL_RATIO = 0.01


class State(TypedDict, total=False):
    # inputs
    facts: dict[str, Any]
    source_system: str
    feed: str
    domain: str | None
    # ground
    payload: dict[str, Any]
    citations: list[str]
    prompt_citation: str
    system: str
    hints: dict[str, str]
    mapped_columns: list[str]
    # infer
    raw: dict[str, Any]
    # assemble
    content: dict[str, Any]


def glossary_mapped_columns(columns: list[str], glossary: dict[str, Any] | None) -> list[str]:
    """Observed columns whose glossary term carries `maps_toward` - the
    knowledge-bounded definition of a `high`-importance column (D6). Same
    matching as the knowledge provider's glossary lookup: case-insensitive, a
    space reads as an underscore, aliases count."""
    if not glossary:
        return []
    keys: set[str] = set()
    for term in glossary.get("terms", []) or []:
        if not term.get("maps_toward"):
            continue
        keys.add(str(term.get("term", "")).lower())
        keys.update(str(a).lower() for a in term.get("aliases", []) or [])
    return [c for c in columns if c.lower().replace(" ", "_") in keys]


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
            facts=facts,
            source_system=state["source_system"],
            feed=state["feed"],
            domain=state.get("domain"),
        )
        system, prompt_citation = prompts.load(self.name)
        columns = [c.name for c in facts.columns]
        return {
            "payload": {"observations": job.observations, "context": job.context},
            "citations": job.citations,
            "prompt_citation": prompt_citation,
            "system": system,
            "hints": {c.name: c.hint for c in facts.columns},
            "mapped_columns": glossary_mapped_columns(columns, job.context.get("glossary")),
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
        """One discarded or corrected model candidate (a claim, a signal, a column
        role), recorded as an `info` signal rather than silently lost - same
        audit-trail intent the old `unknowns` bucket served for dropped claims."""
        return Signal(
            kind="unknown",
            claim=reason,
            basis="Assembled deterministically from the model's raw output.",
            check="Open Forensic mode to see the discarded candidate verbatim.",
            consequence="Nothing was written for this candidate; it is simply absent.",
            severity="info",
        )

    @staticmethod
    def _risk(claim: str, *, basis: str, check: str, consequence: str) -> Signal:
        return Signal(
            kind="risk",
            claim=claim,
            basis=basis,
            check=check,
            consequence=consequence,
            severity="warn",
        )

    def _anomaly_signals(self, facts: ProfileFacts) -> list[Signal]:
        """Deterministic risks read straight off the v2 facts. Provider-independent
        by construction: the stub and a real model get the same ones."""
        signals: list[Signal] = []
        rows = facts.row_count
        for column in facts.columns:
            non_null = rows - column.null_count
            if rows and column.null_count == rows:
                signals.append(
                    self._risk(
                        f"{column.name} is empty in every row.",
                        basis=f"Profiled null ratio 1.0 for {column.name} ({rows} rows).",
                        check=f"Open Forensic mode and confirm {column.name} has no values.",
                        consequence="The column lands in Bronze empty; nothing downstream "
                        "can be mapped from it.",
                    )
                )
                continue
            if rows and column.null_count and column.null_count / rows >= NULL_RATE_SIGNAL_RATIO:
                pct = 100 * column.null_count / rows
                signals.append(
                    self._risk(
                        f"{column.name} is null in {pct:.1f}% of rows "
                        f"({column.null_count}/{rows}).",
                        basis=f"Computed directly from the profiled column ({column.name}).",
                        check=f"Open Forensic mode and check {column.name}'s null count "
                        "against the sample rows.",
                        consequence="The rows still land in Bronze unchanged; a mapping rule "
                        "can enforce this later if needed.",
                    )
                )
            if column.constant and rows > 1 and column.hint != "technical":
                signals.append(
                    self._risk(
                        f"{column.name} holds one value across all {non_null} populated rows.",
                        basis=f"Profiled distinct_count 1 for {column.name}.",
                        check=f"Open Forensic mode: {column.name}'s top value is its only value.",
                        consequence="A constant column carries no information for this "
                        "delivery; if it is meant to vary, the extract may be filtered.",
                    )
                )
            if (
                column.hint == "date"
                and non_null
                and column.sentinel_count / non_null >= SENTINEL_HEAVY_RATIO
            ):
                pct = 100 * column.sentinel_count / non_null
                signals.append(
                    self._risk(
                        f"{column.name} uses placeholder dates in {pct:.1f}% of its values.",
                        basis=f"Profiled sentinel_count {column.sentinel_count} of {non_null} "
                        f"populated values in {column.name} (1900-01-01, 9999-12-31, "
                        "all-zero or all-nine).",
                        check=f"Open Forensic mode and read {column.name}'s min/max - they "
                        "exclude the placeholders.",
                        consequence="Placeholders read as real dates downstream unless a "
                        "mapping rule nulls them; date arithmetic over them is wrong.",
                    )
                )
        if facts.duplicate_rows:
            signals.append(
                self._risk(
                    f"{facts.duplicate_rows} fully duplicated rows present.",
                    basis="Computed by the profiler by comparing every column across rows.",
                    check="Open Forensic mode to see the duplicate-row count restated against "
                    "the sample.",
                    consequence="Duplicates are not removed at this stage; Bronze is a verbatim "
                    "copy of the file.",
                )
            )
        return signals

    def _column_roles(
        self,
        raw_roles: list[Any],
        *,
        hints: dict[str, str],
        mapped: set[str],
        signals: list[Signal],
    ) -> list[ColumnRoleOut]:
        """The model's roles, checked against what was observed (see module
        docstring). One entry per observed column comes out, always."""
        by_name: dict[str, ColumnRoleOut] = {}
        for candidate in raw_roles or []:
            try:
                parsed = LlmColumnRole.model_validate(candidate)
            except Exception:
                signals.append(
                    self._dropped(f"malformed column role discarded: {str(candidate)[:120]}")
                )
                continue
            if parsed.name not in hints:
                signals.append(
                    self._dropped(f"column role for unobserved column discarded: {parsed.name}")
                )
                continue
            if parsed.name in by_name:
                signals.append(self._dropped(f"duplicate column role discarded: {parsed.name}"))
                continue

            hint = hints[parsed.name]
            role = parsed.role
            importance = parsed.importance
            reason = parsed.reason.strip()
            if role not in COLUMN_ROLES:
                signals.append(
                    self._dropped(f"unknown role '{role}' for {parsed.name} set to unclassified")
                )
                role = "unclassified"
            if importance not in IMPORTANCE_LEVELS:
                importance = "medium"
            if role != hint and hint != "unclassified" and not reason:
                # A contradiction has to be argued; unargued, the observation stands.
                signals.append(
                    self._dropped(
                        f"role '{role}' for {parsed.name} contradicts hint '{hint}' without a "
                        "reason; hint kept"
                    )
                )
                role = hint
                reason = "from profile hint"
            if role == "technical" and importance != "low":
                signals.append(
                    self._dropped(
                        f"technical column {parsed.name} marked {importance}; demoted to low"
                    )
                )
                importance = "low"
            by_name[parsed.name] = ColumnRoleOut(
                name=parsed.name,
                role=role,
                importance=importance,
                reason=reason or "from profile hint",
                hint=hint,
                source="model",
            )

        out: list[ColumnRoleOut] = []
        for name, hint in hints.items():
            if name in by_name:
                out.append(by_name[name])
                continue
            # The model said nothing about this column: the observation stands.
            out.append(
                ColumnRoleOut(
                    name=name,
                    role=hint,
                    importance="low"
                    if hint in ("technical", "unclassified")
                    else "high"
                    if name in mapped
                    else "medium",
                    reason="from profile hint",
                    hint=hint,
                    source="hint",
                )
            )
        return out

    def _assemble(self, state: State) -> State:
        """No model. Validates, drops unusable claims and signals, keeps the
        record structured, and composes the headline/recommended_action."""
        raw = state.get("raw") or {}
        facts = ProfileFacts.model_validate(state["facts"])
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

        # What the facts already state as numbers, said once, whatever answered.
        signals.extend(self._anomaly_signals(facts))

        column_roles = self._column_roles(
            raw.get("column_roles", []) or [],
            hints=state.get("hints") or {c.name: c.hint for c in facts.columns},
            mapped=set(state.get("mapped_columns") or []),
            signals=signals,
        )

        headline, recommended_action = self._headline(signals)
        content = InterpretationContent(
            claims=claims,
            signals=signals,
            column_roles=column_roles,
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
        domain: str | None = None,
        on_step: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Runs the graph node by node instead of invoking it whole, so a caller
        can be told the moment each node finishes - `on_step` is the only reason
        this streams rather than invokes; the result is identical either way.
        `domain` (the upload's landing domain) selects the domain knowledge whose
        `what_it_answers` bounds importance; optional, for callers without one."""
        state: dict[str, Any] = {
            "facts": facts.model_dump(),
            "source_system": source_system,
            "feed": feed,
            "domain": domain,
        }
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
