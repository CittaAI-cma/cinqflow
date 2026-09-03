"""Context assembly: knowledge selected for THIS job, never the whole base."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.decisions import hints_for_columns, parse_decision_hints
from cinqflow.knowledge.provider import KnowledgeProvider
from cinqflow.workflow.models import ProfileFacts


@dataclass
class JobContext:
    observations: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)
    #: Parsed, machine-usable form of `context["precedents"]` /
    #: `context["semantic_candidates"]` - never serialized to a model, only
    #: read by the graph's own deterministic validation node. Keeping these
    #: outside `context` is what keeps "the model sees selected knowledge" and
    #: "the code enforces governed knowledge" two separate guarantees: a model
    #: that ignored `context.precedents` entirely still can't get a wrong
    #: mapping past `_validate`.
    precedent_hints: dict[str, Any] = field(default_factory=dict)
    semantic_hints: dict[str, Any] = field(default_factory=dict)


class ContextBuilder:
    def __init__(self, knowledge: KnowledgeProvider) -> None:
        self.knowledge = knowledge

    def for_interpretation(
        self, *, facts: ProfileFacts, source_system: str, feed: str
    ) -> JobContext:
        """Observations are the deterministic profile. Context is the source
        definition plus only the glossary terms matching observed columns."""
        observations = facts.model_dump()
        # Samples stay out of the prompt: column facts carry bounded example values
        # already, and full rows are PHI-bearing.
        observations.pop("sample_rows", None)

        selected: dict[str, Any] = {}
        citations: list[str] = []

        source = self.knowledge.get_source(source_system=source_system, feed=feed)
        if source:
            selected["source"] = {"citation": source.citation, "content": source.content}
            citations.append(source.citation)

        columns = [c.name for c in facts.columns]
        glossary = self.knowledge.get_glossary(columns)
        if glossary and glossary.content.get("terms"):
            selected["glossary"] = {
                "citation": glossary.citation,
                "terms": glossary.content["terms"],
            }
            citations.append(glossary.citation)

        # Prior governed decisions at this stage (bronze), e.g. a source-specific
        # timestamp rule. Interpretation only ever *cites* these - it makes no
        # target decision that a precedent could override the way `for_mapping`
        # does, so nothing further consumes `precedent_hints` here.
        precedents = self._precedents_context(layer="bronze", columns=columns)
        if precedents:
            selected["precedents"] = precedents["context"]
            citations.append(precedents["citation"])

        return JobContext(observations=observations, context=selected, citations=citations)

    def for_mapping(
        self, *, facts: ProfileFacts, source_system: str, feed: str, domain: str
    ) -> JobContext:
        """Bronze observations + the governed target model + prior decisions.

        Selective by construction: the canonical model is trimmed to field names,
        types and meanings; glossary terms are limited to the observed columns; and
        approved decision sets come in as exemplars. Sample rows never travel.
        """
        observations = facts.model_dump()
        observations.pop("sample_rows", None)

        selected: dict[str, Any] = {}
        citations: list[str] = []
        columns = [c.name for c in facts.columns]

        canonical = self.knowledge.get_canonical(domain)
        if canonical:
            selected["canonical"] = {
                "citation": canonical.citation,
                "entities": [
                    {
                        "entity": entity.get("entity"),
                        "table": entity.get("table"),
                        "grain": entity.get("grain"),
                        "primary_key": entity.get("primary_key", []),
                        "fields": [
                            {
                                "name": f"{entity.get('table')}.{field['name']}",
                                "type": field.get("type"),
                                "means": field.get("means"),
                            }
                            for field in entity.get("fields", [])
                        ],
                    }
                    for entity in canonical.content.get("entities", [])
                ],
                "system_populated": canonical.content.get("system_populated", []),
                "contested_fields": [
                    c.get("field") for c in canonical.content.get("contested_fields", [])
                ],
            }
            citations.append(canonical.citation)

        source = self.knowledge.get_source(source_system=source_system, feed=feed)
        if source:
            selected["source"] = {"citation": source.citation, "content": source.content}
            citations.append(source.citation)

        glossary = self.knowledge.get_glossary(columns)
        unmatched_columns = columns
        if glossary:
            unmatched_columns = glossary.content.get("unmatched_columns", columns)
            if glossary.content.get("terms"):
                selected["glossary"] = {
                    "citation": glossary.citation,
                    "terms": glossary.content["terms"],
                }
                citations.append(glossary.citation)

        domain_knowledge = self.knowledge.get_domain_knowledge(domain)
        if domain_knowledge:
            selected["domain_knowledge"] = {
                "citation": domain_knowledge.citation,
                "what_it_answers": domain_knowledge.content.get("what_it_answers"),
                "grain_rules": domain_knowledge.content.get("grain_rules", []),
                "known_gaps": domain_knowledge.content.get("known_gaps", []),
                "failure_modes": domain_knowledge.content.get("failure_modes", []),
            }
            citations.append(domain_knowledge.citation)

        history = self.knowledge.get_approved_mappings(domain)
        if history:
            selected["history"] = {
                "citation": history.citation,
                "files": history.content.get("files", []),
                "decision_sets": history.content.get("decision_sets", []),
            }
            citations.append(history.citation)

        # Prior governed decisions for THIS layer (Silver Raw routing), preferred
        # over asking the model to re-derive an answer this organisation already
        # settled. `precedent_hints` is what `_validate` enforces deterministically;
        # `context["precedents"]` is only what the model additionally gets to see.
        precedent_hints: dict[str, Any] = {}
        precedents = self._precedents_context(layer="silver_raw", columns=columns)
        if precedents:
            selected["precedents"] = precedents["context"]
            citations.append(precedents["citation"])
            precedent_hints = precedents["hints"]

        # The fallback: only for columns nothing structured (glossary, canonical
        # name, an approved decision) could place at all.
        semantic_hints: dict[str, Any] = {}
        still_unplaced = [c for c in unmatched_columns if c not in precedent_hints]
        if still_unplaced:
            semantic = self.knowledge.get_semantic_candidates(columns=still_unplaced, domain=domain)
            if semantic and semantic.content.get("matches"):
                selected["semantic_candidates"] = {
                    "citation": semantic.citation,
                    "algorithm": semantic.content["algorithm"],
                    "matches": semantic.content["matches"],
                }
                citations.append(semantic.citation)
                semantic_hints = semantic.content["matches"]

        return JobContext(
            observations=observations,
            context=selected,
            citations=citations,
            precedent_hints=precedent_hints,
            semantic_hints=semantic_hints,
        )

    def _precedents_context(self, *, layer: str, columns: list[str]) -> dict[str, Any] | None:
        """Governed decisions that name one of `columns` outright, for `layer`.

        Returns both the trimmed view a model may read (`context`) and the
        parsed hints (`hints`) that `recommend_mapping._validate` applies
        deterministically - the same split as the rest of this class, just
        computed once so both sides agree on exactly which decisions matched.
        """
        decisions = self.knowledge.get_decision_records(layer=layer)
        if not decisions:
            return None
        hints = hints_for_columns(parse_decision_hints(decisions.content["records"]), columns)
        if not hints:
            return None
        return {
            "citation": decisions.citation,
            "hints": {col: hint.__dict__ for col, hint in hints.items()},
            "context": {
                "citation": decisions.citation,
                "decisions": [
                    {
                        "decision_id": hint.decision_id,
                        "title": hint.title,
                        "applies_to": col,
                        "target": hint.target,
                        "rationale": hint.rationale,
                        "reversibility": hint.reversibility,
                    }
                    for col, hint in hints.items()
                ],
            },
        }

    def legal_targets(self, domain: str) -> set[str]:
        """The only targets a proposal may name, as `table.field`.

        Delegates to the shared canonical reader so the deterministic engine
        validates against exactly the same list without importing this package.
        """
        return set(load_canonical(self.knowledge, domain).legal_targets)
