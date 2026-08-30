"""knowledge.chunk on Postgres + pgvector — the K2 store's real seat.

    "vector: index/retrieve_chunks   mock: list   dev: REAL_pgvector
     target: pgvector|ai_search"
    — docs/architecture/plates/04-pin-out-map.md

    "The vector store is pgvector inside the same Postgres that holds the
     metadata registry ... RBAC scope filters join in the same engine as the
     data they protect."
    — ADR-0007

The scope filter is applied as WHERE clauses BEFORE the distance computation —
the restriction lives in the query, not in the answer. Azure AI Search /
Databricks Vector Search keep their adapter seats; swapping is a profile line.

The engine-specific columns this adapter reads (`embedding_vec vector`, the
generated `tsv`) are added by the installer's Postgres rendering — they are
dialect, so they are not in the portable spec.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.citations import parse
from cinqflow.ports import port
from cinqflow.ports.vector import Chunk, ScoredChunk

#: Chunk-record columns a caller may scope-filter on directly. Anything else
#: filters through the metadata document.
_FIRST_CLASS = frozenset({"kind", "domain", "source_org", "feed_id", "lifecycle_state"})


def _literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


@port("vector", "pgvector")
class PgVectorStore:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def index(self, chunks: Sequence[Chunk], vectors: Sequence[tuple[float, ...]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("every chunk needs exactly one vector")
        now = datetime.now(UTC)
        for chunk, vector in zip(chunks, vectors, strict=True):
            metadata = dict(chunk.metadata)
            self._db.execute(
                "INSERT INTO knowledge.chunk (chunk_id, kind, citation_id, text, domain, "
                "source_org, feed_id, lifecycle_state, metadata, embedded_at, embedding_vec) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) "
                "ON CONFLICT (chunk_id) DO UPDATE SET kind = EXCLUDED.kind, "
                "citation_id = EXCLUDED.citation_id, text = EXCLUDED.text, "
                "domain = EXCLUDED.domain, source_org = EXCLUDED.source_org, "
                "feed_id = EXCLUDED.feed_id, lifecycle_state = EXCLUDED.lifecycle_state, "
                "metadata = EXCLUDED.metadata, embedded_at = EXCLUDED.embedded_at, "
                "embedding_vec = EXCLUDED.embedding_vec",
                (
                    chunk.chunk_id,
                    metadata.get("kind", "k2"),
                    str(chunk.citation),
                    chunk.text,
                    metadata.get("domain"),
                    metadata.get("source_org"),
                    metadata.get("feed_id"),
                    # The ingestion pipeline (CF-V1-E16-04) is the gate: only
                    # Published objects reach this verb. The column records
                    # what was true at embed time so retire-deletes are exact.
                    metadata.get("lifecycle_state", "published"),
                    json.dumps(metadata, sort_keys=True),
                    now,
                    _literal(vector),
                ),
            )

    def supersede(
        self,
        *,
        retire: Sequence[str],
        chunks: Sequence[Chunk],
        vectors: Sequence[tuple[float, ...]],
    ) -> None:
        """`index()`'s upsert, then a `DELETE ... WHERE chunk_id = ANY(...)` —
        the same `Connection`, so both statements land inside whichever
        transaction the caller already opened around it (`adapters.local
        .pg_control.commit`'s "everything inside the block is one
        transaction", the `pg_compute` precedent this mirrors). Index BEFORE
        retire, for the reason `adapters.mock.vector.ListVector.supersede`
        states: a failure between the two statements must never leave a
        guide with ZERO chunks.
        """
        self.index(chunks, vectors)
        if retire:
            self._db.execute(
                "DELETE FROM knowledge.chunk WHERE chunk_id = ANY(%s)",
                (list(retire),),
            )

    def retrieve(
        self, vector: tuple[float, ...], *, limit: int = 10, scope_filter: dict[str, str]
    ) -> Sequence[ScoredChunk]:
        # Scope filters the candidate set BEFORE similarity — never the results.
        # Every key and value is a bound parameter against the metadata
        # document (the promoted columns duplicate it for reads that want
        # them); no identifier ever enters this statement from a caller.
        scope_clause = " AND metadata->>%s = %s" * len(scope_filter)
        parameters: list[Any] = []
        for key, value in sorted(scope_filter.items()):
            parameters.extend((key, value))
        literal = _literal(vector)
        rows = self._db.fetch_all(
            "SELECT chunk_id, text, citation_id, metadata, "
            "1 - (embedding_vec <=> %s::vector) AS score "
            "FROM knowledge.chunk WHERE embedding_vec IS NOT NULL"
            + scope_clause
            + " ORDER BY embedding_vec <=> %s::vector LIMIT %s",
            (literal, *parameters, literal, limit),
        )
        return tuple(
            ScoredChunk(
                chunk=Chunk(
                    chunk_id=chunk_id,
                    text=text,
                    citation=parse(citation_id),
                    metadata={k: str(v) for k, v in (metadata or {}).items()},
                ),
                score=float(score),
            )
            for chunk_id, text, citation_id, metadata, score in rows
        )

    def count(self) -> int:
        row = self._db.fetch_one("SELECT count(*) FROM knowledge.chunk")
        return int(row[0]) if row else 0
