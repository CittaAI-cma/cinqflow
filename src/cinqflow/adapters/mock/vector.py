"""list — an in-memory vector index that stays EMPTY in Wave 0."""

from __future__ import annotations

from collections.abc import Sequence

from cinqflow.ports import port
from cinqflow.ports.vector import Chunk, ScoredChunk


@port("vector", "mock")
class ListVector:
    """Cosine similarity over a list.

    Wave 0 asserts `count() == 0`. The knowledge plane — chunking, the
    PHI-verify gate, steward approval, embedding — is Wave 1, and provisioning
    the store while leaving it empty is the honest way to say so.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[Chunk, tuple[float, ...]]] = []

    def index(self, chunks: Sequence[Chunk], vectors: Sequence[tuple[float, ...]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("every chunk needs exactly one vector")
        self._entries.extend(zip(chunks, vectors, strict=True))

    def retrieve(
        self, vector: tuple[float, ...], *, limit: int = 10, scope_filter: dict[str, str]
    ) -> Sequence[ScoredChunk]:
        # Scope filters the candidate set BEFORE similarity — never the results.
        candidates = [
            (chunk, vec)
            for chunk, vec in self._entries
            if all(chunk.metadata.get(k) == v for k, v in scope_filter.items())
        ]
        scored = [ScoredChunk(chunk=chunk, score=_cosine(vector, vec)) for chunk, vec in candidates]
        return tuple(sorted(scored, key=lambda s: s.score, reverse=True)[:limit])

    def count(self) -> int:
        return len(self._entries)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    magnitude = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / magnitude if magnitude else 0.0
