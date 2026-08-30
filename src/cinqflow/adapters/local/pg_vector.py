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
from contextlib import AbstractContextManager, nullcontext
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

    def _own_transaction_if_none_is_open(self) -> AbstractContextManager[Any]:
        """W1-29 · CF-V1-E16-05. `pg_control.connect()`'s default —
        `autocommit=True` (pg_control.py ~line 100-101) — is the ONLY
        connection-constructor actually wired for non-test use, and under it
        every bare `execute()` commits on its own. Without this, a multi-step
        `index()` loop that fails on chunk 3 of 5 leaves chunks 1-2 durably
        committed and 3-5 never attempted: a partial new version sitting
        beside the untouched old one, worse than "zero new chunks" because
        nothing signals which of the five are real.

        So: when the connection is `autocommit`, THIS METHOD opens the
        transaction nobody else will — `psycopg`'s own `Connection.transaction()`,
        which issues an explicit `BEGIN` and commits or rolls back the whole
        block as one unit regardless of the client-side autocommit setting.

        When the connection is NOT `autocommit` — a test's `plane` fixture, or
        a caller composing multiple adapters inside `pg_control.commit()` —
        somebody has ALREADY opened a transaction around this call and owns
        its commit/rollback. Opening a second one here would finalise this
        adapter's writes early, ahead of whatever else that block does, which
        is exactly the multi-adapter atomicity `pg_control.commit` exists to
        provide (and exactly what would turn a test's `plane` fixture — "a
        connection whose work is ALWAYS rolled back" — into one that quietly
        commits). So this branch changes nothing: a no-op context manager,
        deferring entirely to whatever transaction is already open.

        `index()` and `supersede()` both call this, and a `supersede()` under
        `autocommit` therefore opens ONE outer transaction; `index()`'s own
        call to this method, reached from inside it, sees a transaction
        already open and correctly nests as a SAVEPOINT (`psycopg` decides
        BEGIN-vs-SAVEPOINT from the connection's actual transaction state, not
        from this check) — so the per-chunk inserts and the retire-delete
        commit or roll back together, as one unit.
        """
        if self._db.raw.autocommit:
            return self._db.raw.transaction()
        return nullcontext()

    def index(self, chunks: Sequence[Chunk], vectors: Sequence[tuple[float, ...]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("every chunk needs exactly one vector")
        now = datetime.now(UTC)
        with self._own_transaction_if_none_is_open():
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
        the same `Connection`, wrapped by `_own_transaction_if_none_is_open`
        so both statements commit or roll back together as ONE unit (W1-29;
        see that method for exactly which connections this self-manages and
        which it defers on).

        This UPDATES a prior claim: an earlier version of this docstring said
        both statements landed "inside whichever transaction the caller
        already opened around it" — false the day it was written, since no
        caller anywhere opens one (`grep -rln "PgVectorStore"` outside this
        module found none). The property callers actually get now does not
        depend on a caller having done anything: under `autocommit` — the
        default, and the only connection-constructor wired for non-test use —
        this method opens its own transaction, so a failure ANYWHERE in the
        call (mid-loop in `index()`, or in the retire-delete after it) rolls
        the whole thing back, leaving the untouched OLD chunks and none of the
        new ones. Index is still attempted before retire — matching
        `adapters.mock.vector.ListVector.supersede`'s ordering — but that
        ordering is no longer load-bearing for correctness the way it was
        before this transaction existed: it no longer needs to be, because
        there is no window between the two statements a half-finished write
        can be observed in.
        """
        with self._own_transaction_if_none_is_open():
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
