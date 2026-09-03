"""Knowledge access contract. Graphs depend on this Protocol, never on storage.

Swapping YAML for a database, catalog or vector store means adding a provider,
not rewriting a graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class KnowledgeDoc:
    """A governed fact set, with the version that provenance cites."""

    ref: str  # e.g. "sources/roster.yaml"
    version: int
    content: dict

    @property
    def citation(self) -> str:
        return f"{self.ref}@{self.version}"


class KnowledgeProvider(Protocol):
    def get_source(self, *, source_system: str, feed: str) -> KnowledgeDoc | None: ...

    def get_glossary(self, terms: list[str]) -> KnowledgeDoc | None: ...

    def get_canonical(self, domain: str) -> KnowledgeDoc | None:
        """The governed target model. Its fields are the only legal mapping targets."""
        ...

    def get_approved_mappings(self, domain: str) -> KnowledgeDoc | None:
        """Decisions already made, as exemplars - never as rules."""
        ...

    def get_domain_knowledge(self, domain: str) -> KnowledgeDoc | None:
        """Domain semantics and known structural gaps - never a source of legal
        targets. Explains why a column may have no defensible home."""
        ...

    def get_decision_records(self, *, layer: str | None = None) -> KnowledgeDoc | None:
        """The analyst decision register: governance calls already made about
        meaning, keys, constants and precedence - never a mapping table.

        Distinct from `get_approved_mappings`: that method returns the mechanical
        source-field/target pairs a G2 approval exports; this one returns the
        prose record of *why*, with a human's name against it. `layer` narrows
        to the stage the caller cares about (e.g. `"silver_raw"` for a mapping
        decision, `"bronze"` for an interpretation-stage one).
        """
        ...

    def get_semantic_candidates(self, *, columns: list[str], domain: str) -> KnowledgeDoc | None:
        """The fallback path - callers pass only columns structured lookup
        (`get_glossary`, an exact canonical field name) could not place.

        Never a source of legal targets on its own: a match here only ever
        surfaces a *candidate worth a human's attention*, never an accepted
        mapping - see `knowledge/semantic.py` for why, and for the algorithm.
        """
        ...
