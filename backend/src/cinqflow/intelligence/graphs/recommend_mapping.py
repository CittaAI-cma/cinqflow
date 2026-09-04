"""recommend_mapping graph: ground -> recommend -> validate.

Only `recommend` calls a model. `validate` is deterministic and is the backstop
that makes the AI safe to listen to: a target the canonical model does not declare
is marked `invalid` and the proposal is persisted as such - never silently
corrected, never quietly dropped, because a fabricated target must be visible.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cinqflow.intelligence import prompts
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.llm import LlmClient
from cinqflow.intelligence.schemas import MappingProposalResponse
from cinqflow.knowledge.semantic import ALGORITHM as SEMANTIC_ALGORITHM
from cinqflow.workflow.models import (
    FieldCandidate,
    ProfileFacts,
    ProposalContent,
    RecommendedAction,
    Transform,
)

#: A semantic match below this can't establish anything on its own; above it,
#: it's worth an analyst's attention but never worth accepting unread - hence
#: the confidence cap in `_apply_semantic_hints` regardless of the raw score.
SEMANTIC_CONFIDENCE_CAP = 0.4

#: Transform vocabulary Stage 3 will accept from a model. Anything else is dropped
#: from the candidate (the mapping itself survives; the unsupported step does not).
#: What the model may propose. Mirrors `mapping_spec.ALLOWED_OPS`, so the AI cannot
#: suggest a transform the analyst would then be unable to save.
ALLOWED_OPS = frozenset({"parse_date", "trim", "upper", "lower", "cast"})


class State(TypedDict, total=False):
    facts: dict[str, Any]
    source_system: str
    feed: str
    domain: str
    payload: dict[str, Any]
    citations: list[str]
    prompt_citation: str
    system: str
    legal_targets: list[str]
    #: Parsed governed decisions and lexical fallback matches, keyed by source
    #: column - never sent to the model (see `ContextBuilder.JobContext`),
    #: consulted only by `_validate`.
    precedent_hints: dict[str, Any]
    semantic_hints: dict[str, Any]
    raw: dict[str, Any]
    content: dict[str, Any]
    status: str


class RecommendMappingGraph:
    name = "recommend_mapping"

    def __init__(self, *, context_builder: ContextBuilder, llm: LlmClient) -> None:
        self.context_builder = context_builder
        self.llm = llm
        self._compiled = self._build().compile()

    # ------------------------------------------------------------------ nodes
    def _ground(self, state: State) -> State:
        """No model. Selects governed knowledge and fixes the legal target list."""
        facts = ProfileFacts.model_validate(state["facts"])
        job = self.context_builder.for_mapping(
            facts=facts,
            source_system=state["source_system"],
            feed=state["feed"],
            domain=state["domain"],
        )
        system, prompt_citation = prompts.load(self.name)
        return {
            "payload": {"observations": job.observations, "context": job.context},
            "citations": job.citations,
            "prompt_citation": prompt_citation,
            "system": system,
            "legal_targets": sorted(self.context_builder.legal_targets(state["domain"])),
            "precedent_hints": job.precedent_hints,
            "semantic_hints": job.semantic_hints,
        }

    def _recommend(self, state: State) -> State:
        """The only model call in the graph."""
        raw = self.llm.complete_json(
            system=state["system"],
            user=json.dumps(state["payload"], sort_keys=True, separators=(",", ":")),
            response_model=MappingProposalResponse,
        )
        return {"raw": raw}

    def _validate(self, state: State) -> State:
        """No model. Governed knowledge decides what survives."""
        raw = state.get("raw") or {}
        legal = set(state.get("legal_targets") or [])
        observed = {c["name"] for c in state["facts"]["columns"]}

        candidates: list[FieldCandidate] = []
        notes = [str(n) for n in (raw.get("notes") or [])]
        seen: set[str] = set()
        invalid = False

        for entry in raw.get("fields") or []:
            if not isinstance(entry, dict):
                notes.append(f"discarded malformed field entry: {str(entry)[:80]}")
                continue

            source = str(entry.get("source", "")).strip()
            if source not in observed:
                notes.append(f"discarded field for column not in Bronze: {source or '<blank>'}")
                continue
            if source in seen:
                notes.append(f"discarded duplicate candidate for {source}")
                continue
            seen.add(source)

            target = entry.get("target")
            target = str(target).strip() if target else None
            concept = entry.get("concept")
            concept = str(concept).strip() if concept else None
            status = str(entry.get("status", "candidate"))
            rejected_target: str | None = None
            reason: str | None = None

            if target and target not in legal:
                # The model named something the canonical model does not have.
                # Persisted as invalid so the fabrication is visible.
                rejected_target, target = target, None
                status = "invalid"
                reason = f"'{rejected_target}' is not a field in the canonical model"
                invalid = True
            elif not target:
                status = "unknown" if status not in ("ambiguous", "unknown") else status
                reason = reason or "no defensible canonical target"
            elif status not in ("candidate", "ambiguous"):
                status = "candidate"

            transform: Transform | None = None
            step = entry.get("transform")
            if isinstance(step, dict) and step.get("op"):
                op = str(step["op"])
                if op in ALLOWED_OPS:
                    raw_args = step.get("args") or []
                    # `args` is a list of {"key", "value"} pairs, not a dict -
                    # OpenAI's Structured Outputs strict mode rejects
                    # dict[str, str]'s open `additionalProperties` schema (see
                    # intelligence/schemas.py:LlmTransform). A plain dict is
                    # still tolerated here for other providers / older data.
                    if isinstance(raw_args, dict):
                        parsed_args = {str(k): str(v) for k, v in raw_args.items()}
                    elif isinstance(raw_args, list):
                        parsed_args = {
                            str(pair["key"]): str(pair["value"])
                            for pair in raw_args
                            if isinstance(pair, dict) and "key" in pair and "value" in pair
                        }
                    else:
                        parsed_args = {}
                    transform = Transform(op=op, args=parsed_args)
                else:
                    notes.append(f"dropped unsupported transform '{op}' on {source}")

            evidence = [str(e) for e in (entry.get("evidence") or []) if str(e).strip()]
            try:
                confidence = float(entry.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = min(max(confidence, 0.0), 1.0)
            if not evidence:
                # Same rule as Stage 1: a claim without evidence is not a claim.
                evidence = ["no evidence supplied by model"]
                confidence = 0.0
                if status == "candidate":
                    status = "ambiguous"

            candidates.append(
                FieldCandidate(
                    source=source,
                    target=target,
                    concept=concept,
                    transform=transform,
                    confidence=confidence,
                    evidence=evidence,
                    status=status,
                    rejected_target=rejected_target,
                    reason=reason,
                )
            )

        # Every observed column must appear, even if the model ignored it.
        for column in sorted(observed - seen):
            candidates.append(
                FieldCandidate(
                    source=column,
                    target=None,
                    confidence=0.0,
                    evidence=["column not addressed by the model"],
                    status="unknown",
                    reason="no candidate returned for this column",
                )
            )

        by_source = {c.source: c for c in candidates}
        self._apply_precedent_hints(
            candidates=by_source,
            hints=state.get("precedent_hints") or {},
            legal=legal,
            notes=notes,
        )
        self._apply_semantic_hints(
            candidates=by_source, hints=state.get("semantic_hints") or {}, legal=legal
        )

        # Two source columns cannot both land in one canonical field. That is real
        # ambiguity for the analyst to resolve, so it is detected here rather than
        # discovered later when the write fails.
        claimants: dict[str, list[FieldCandidate]] = {}
        for candidate in candidates:
            if candidate.target and candidate.status == "candidate":
                claimants.setdefault(candidate.target, []).append(candidate)
        for target, contenders in sorted(claimants.items()):
            if len(contenders) < 2:
                continue
            names = ", ".join(sorted(c.source for c in contenders))
            for candidate in contenders:
                others = sorted(c.source for c in contenders if c.source != candidate.source)
                candidate.status = "ambiguous"
                candidate.reason = f"also proposed for {target}: {', '.join(others)}"
                candidate.evidence.append(f"contested target with {', '.join(others)}")
            notes.append(f"{len(contenders)} columns propose {target}: {names}. One must win.")

        candidates.sort(key=lambda c: (c.status != "invalid", c.status, c.source))
        headline, recommended_action = self._headline(candidates)
        content = ProposalContent(
            fields=candidates, notes=notes, headline=headline, recommended_action=recommended_action
        )
        return {"content": content.model_dump(), "status": "invalid" if invalid else "proposed"}

    @staticmethod
    def _headline(candidates: list[FieldCandidate]) -> tuple[str, RecommendedAction]:
        """Composed from the final field statuses, never asked of the model -
        same rule as `interpret_file`'s headline (§ its own docstring). Mirrors
        S4's own filter default: invalid first, then anything needing a
        decision, matching `ProposalContent.counts`."""
        total = len(candidates)
        needs_decision_statuses = ("ambiguous", "unknown", "invalid")
        invalid = sum(1 for c in candidates if c.status == "invalid")
        needs_decision = sum(1 for c in candidates if c.status in needs_decision_statuses)
        if invalid:
            return (
                f"{invalid} field{'s' if invalid != 1 else ''} named a target that doesn't "
                "exist — needs a fix before this can be trusted.",
                "review_first",
            )
        if needs_decision:
            return (
                f"{needs_decision} of {total} field{'s' if total != 1 else ''} need a decision "
                "— the rest look defensible.",
                "review_first",
            )
        plural = "s" if total != 1 else ""
        return (f"All {total} field{plural} have a defensible target.", "approve")

    @staticmethod
    def _apply_precedent_hints(
        *,
        candidates: dict[str, FieldCandidate],
        hints: dict[str, Any],
        legal: set[str],
        notes: list[str],
    ) -> None:
        """Prefer an already-approved decision over the model's own guess.

        Runs regardless of what the model proposed, including a stub or a real
        provider that never read `context.precedents` at all: a decision this
        organisation already approved is applied deterministically, not offered
        as a suggestion the model might take or leave. The one thing this never
        does is invent a target - a precedent naming a target outside the
        current canonical model is surfaced as a note for a steward, not applied.
        """
        for column, hint in hints.items():
            target = hint.get("target")
            decision_id = hint.get("decision_id", "?")
            if target not in legal:
                notes.append(
                    f"precedent {decision_id} names '{target}' for {column}, which is not "
                    "in the current canonical model - needs steward review, not applied."
                )
                continue
            candidate = candidates.get(column)
            if candidate is None:
                continue
            citation = f"precedent:{decision_id}"

            if candidate.status == "invalid":
                # The model fabricated a target outside the canonical model for
                # this column - that is worth keeping visible as `invalid`, not
                # quietly papering over, even though a fix is available. The
                # precedent is surfaced so the analyst can apply it by hand.
                notes.append(
                    f"{column}: rejected as invalid ({candidate.rejected_target!r} is not "
                    f"a canonical field), but approved decision {decision_id} already "
                    f"routes it to {target} - apply that mapping directly."
                )
            elif candidate.status == "candidate" and candidate.target == target:
                # The model independently agreed with governed history: that
                # agreement is itself strong evidence, so confidence rises.
                candidate.confidence = max(candidate.confidence, 0.95)
                if citation not in candidate.evidence:
                    candidate.evidence.append(citation)
            elif candidate.status == "candidate" and candidate.target != target:
                # A real conflict between the model and an approved decision is
                # not this code's call to make either way - surfaced, not resolved.
                candidate.status = "ambiguous"
                candidate.reason = (
                    f"model proposed {candidate.target}, but approved decision "
                    f"{decision_id} already routes {column} to {target}"
                )
                candidate.evidence.append(citation)
                notes.append(
                    f"{column}: model and precedent {decision_id} disagree "
                    f"({candidate.target} vs {target}); analyst must resolve."
                )
            else:
                # unknown or ambiguous with no target of its own: the decision
                # already settled this, so it stands in as the candidate.
                candidate.status = "candidate"
                candidate.target = target
                candidate.confidence = 0.95
                candidate.reason = None
                candidate.rejected_target = None
                candidate.evidence.append(citation)
                if not candidate.concept:
                    candidate.concept = hint.get("title")

    @staticmethod
    def _apply_semantic_hints(
        *, candidates: dict[str, FieldCandidate], hints: dict[str, Any], legal: set[str]
    ) -> None:
        """The fallback: only for a column still `unknown` after everything
        structured - the model, the glossary, an approved decision - found
        nothing. Never promotes past `ambiguous`, and never past the
        `SEMANTIC_CONFIDENCE_CAP`: this is a hint worth a human's attention,
        not a decision.
        """
        for column, matches in hints.items():
            candidate = candidates.get(column)
            if candidate is None or candidate.status != "unknown" or not matches:
                continue
            best = matches[0]
            target = best.get("target")
            score = float(best.get("score", 0.0))
            if not target or target not in legal:
                continue
            candidate.status = "ambiguous"
            candidate.target = target
            candidate.confidence = min(SEMANTIC_CONFIDENCE_CAP, round(score, 2))
            candidate.reason = (
                f"lexical similarity to {best['concept_ref']} (score {score:.2f}); no "
                "glossary, canonical name or approved decision matched - confirm before accepting"
            )
            candidate.evidence.append(f"semantic:{target}~{score:.2f} ({SEMANTIC_ALGORITHM})")

    def _build(self) -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("ground", self._ground)
        graph.add_node("recommend", self._recommend)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "ground")
        graph.add_edge("ground", "recommend")
        graph.add_edge("recommend", "validate")
        graph.add_edge("validate", END)
        return graph

    # -------------------------------------------------------------------- run
    def run(
        self, *, facts: ProfileFacts, source_system: str, feed: str, domain: str
    ) -> dict[str, Any]:
        final = self._compiled.invoke(
            {
                "facts": facts.model_dump(),
                "source_system": source_system,
                "feed": feed,
                "domain": domain,
            }
        )
        return {
            "content": ProposalContent.model_validate(final["content"]),
            "status": final["status"],
            "prompt": final["prompt_citation"],
            "knowledge": final.get("citations", []),
            "model": self.llm.model_id,
        }
