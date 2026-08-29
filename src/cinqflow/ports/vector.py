"""The `vector` pin — index and retrieve chunks.

    verb: index/retrieve_chunks   mock: list   dev: REAL_pgvector
    target: pgvector|ai_search
    — docs/architecture/plates/04-pin-out-map.md

WAVE 0 PROVISIONS THIS AND LEAVES IT EMPTY.

    "pgvector stays provisioned and empty, exactly as specified."
    — CF-V0-E16-09, the K2 half, honestly scoped

The knowledge plane — chunking, the PHI-verify gate, steward approval,
embedding — is CF-V1-E16-04/05. Wave 0 ships the LEXICAL half of hybrid
retrieval (tsvector over the seeded glossary and rule descriptions), which is
the right half to arrive first: healthcare vocabulary is code-heavy, and
lexical is what catches NPI, DQ-002 and BH-AF-002 that embeddings blur.

    "PHI, member rows, raw feed contents, drafts and secrets never enter the
     vector store"
    — docs/architecture/INVARIANTS.md, intelligence
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from cinqflow.core.citations import CitationId


@dataclass(frozen=True)
class Chunk:
    """A retrievable piece of governed knowledge.

    `citation` is not optional, and that is the design: a chunk that cannot be
    cited cannot ground a claim, and an ungrounded claim is a defect class.
    """

    chunk_id: str
    text: str
    citation: CitationId
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


@runtime_checkable
class VectorPort(Protocol):
    def index(self, chunks: Sequence[Chunk], vectors: Sequence[tuple[float, ...]]) -> None:
        """Index approved chunks. Wave 1 — every chunk passes a PHI-verify gate
        before it reaches here, and a chunk that trips detection quarantines
        for human review."""
        ...

    def retrieve(
        self, vector: tuple[float, ...], *, limit: int = 10, scope_filter: dict[str, str]
    ) -> Sequence[ScoredChunk]:
        """`scope_filter` is REQUIRED, not optional.

        The caller's RBAC scopes must be applied BEFORE any similarity
        computation. A signature with an optional filter is a signature whose
        default is a leak.
        """
        ...

    def count(self) -> int:
        """Wave 0 asserts this is 0. That assertion is the whole story."""
        ...
