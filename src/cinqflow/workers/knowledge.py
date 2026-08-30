"""CF-V1-E16-05 — the chunk/verify/embed spine, wired.

    "Knowledge ingestion becomes just another feed — Inbox -> Parse -> Chunk ->
     PHI-verify -> Steward approve -> Embed + Index — sharing the lifecycle
     engine, idempotency and quarantine discipline with data ingestion."
    "PHI or member-level data (verified per chunk by Presidio before
     embedding) ... never embedded."
    "Scope filter runs FIRST, before any similarity computation."
    "Every factual claim carries a resolvable citation_id."
    — ADR-0007

SCOPE, STATED HONESTLY — the same boundary `core.knowledge`'s own module
docstring states, restated here because this is where it is enforced: E16-05's
full spec eventually adds a document-upload Inbox with layout-aware PDF
parsing for payer specs, persona uploads and scenario docs. NOTHING TODAY
NEEDS THAT. `KnowledgeIngestWorker` has exactly two callers in mind
(CF-V1-E16-07's two content sources — a closed `Incident`'s narrative and a
Published `RUNBOOK`'s steps), both of which hand this worker an
ALREADY-PARSED Python object; there is no upload, so there is nothing here
that reads bytes off `StoragePort`. A generic `ingest_upload(...)` stays a
separate, later story.

ARCHETYPE B — an engine/pipeline stage, not an AI-decision-making agent, the
same shape `workers.incidents.IncidentWorker` and `workers.sla.SlaWorker`
already are: PLAIN SYNCHRONOUS METHODS, callable directly from a test, a CLI
command, or (once E16-07 wires the hook) a lifecycle transition — never
bound to a dispatch mechanism here. There is exactly ONE model call in the
whole thing (`LlmGateway.embed`, for the vector itself), and it makes no
judgement call: nothing here decides whether a chunk is worth keeping,
whether two chunks are "the same", or what a chunk means. That decision
either already happened (`.narrative()`'s CLOSED gate, ADR-0007's
Published-only gate) or belongs to a person (a steward reading a proposal),
never to this module.

THE PHI GATE IS A REFUSAL, NOT A MASK. `LlmGateway.complete()`'s `phi_scrub`
stage masks-and-continues, because a PROMPT can afford to lose a value and
keep its shape. A knowledge chunk cannot: masking it and embedding the
masked text would put a scrubbed-but-still-suspicious string into a store
ADR-0007 says must have NONE, so a chunk `PhiScrubPort.detect` flags is
dropped from the batch entirely — never reaches `LlmGateway.embed`, never
reaches `VectorPort.index` — and the refusal is written to
`audit.agent_action` (`ActionOutcome.REFUSED_PHI`) so it is visible the same
way every other refusal in this codebase is, never silent. One dirty chunk
does not sink the batch: a runbook's OTHER steps still embed, the same
row-level discipline `quarantine_records` already applies to data ingestion.

IDEMPOTENCY LIVES IN THE CHUNK ID, NOT IN THIS WORKER. `core.knowledge
.chunk_id_for` content-addresses every chunk on `(citation, chunk_index,
object_version)`, and every `VectorPort` upserts on it (see
`adapters.mock.vector.ListVector` and `adapters.local.pg_vector
.PgVectorStore`'s own `ON CONFLICT (chunk_id) DO UPDATE`). Re-running this
worker for an unchanged source therefore never DOUBLES the indexed row — it
DOES re-embed and re-spend, honestly: there is no port verb today that lets
this worker ask a `VectorPort` "do you already have this id" without a
similarity query it was never built to answer, and adding one is outside
this slab's authorised surface (`ports/vector.py` is read, not rebuilt, per
this story's own brief).

CF-V1-W1-26 ADDS `VectorPort.supersede` — the ONE new verb this slab does
add, because content-addressing alone does not cover a NEW VERSION: a
version bump changes `object_version`, which changes every chunk id, so a
plain re-run of `index()` would leave the prior version's ids sitting in the
store forever rather than upserting over them. `ingest_runbook`'s
`supersedes` parameter recovers those old ids (by re-chunking the prior,
still-Published version — never a delete keyed on anything guessed) and
retires them in the SAME call that indexes the new ones.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from cinqflow.core.knowledge import (
    ChunkCandidate,
    KnowledgeIngestResult,
    RefusedChunk,
    chunk_incident,
    chunk_runbook,
)
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor, GovernedObject
from cinqflow.core.operations.fingerprint import Incident
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.phi_scrub import PhiScrubPort
from cinqflow.ports.vector import Chunk, VectorPort

#: The `agent` name every ledger row from this worker carries. One name for
#: the whole spine regardless of which source shape called it — the source
#: is already distinguishable in the ledger `detail` and on the chunk's own
#: `kind` ("incident_narrative" / "runbook_section").
AGENT = "knowledge-pipeline"


class KnowledgeIngestWorker:
    def __init__(
        self,
        *,
        phi_scrub: PhiScrubPort,
        llm: LlmGateway,
        vector: VectorPort,
        metadata: MetadataDbPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._scrub = phi_scrub
        self._llm = llm
        self._vector = vector
        self._metadata = metadata
        self._clock = clock or (lambda: datetime.now(UTC))

    # ── the two callers E16-07 has today ─────────────────────────────────────

    def ingest_incident(
        self, incident: Incident, *, run_id: str, caller: Actor
    ) -> KnowledgeIngestResult:
        """Chunk -> PHI-verify -> Embed + Index a CLOSED incident's narrative.

        `chunk_incident` raises `IncidentTransitionError` unwrapped for
        anything not embeddable — the source's own gate, not repeated here.
        """
        return self._ingest(chunk_incident(incident), run_id=run_id, caller=caller)

    def ingest_runbook(
        self,
        runbook: GovernedObject,
        *,
        run_id: str,
        caller: Actor,
        supersedes: GovernedObject | None = None,
    ) -> KnowledgeIngestResult:
        """Chunk -> PHI-verify -> Embed + Index a PUBLISHED runbook's steps.

        `chunk_runbook` raises `KnowledgeSourceError` unwrapped for anything
        not Published — ADR-0007's own gate, not repeated here.

        CF-V1-W1-26 · ATOMIC SUPERSEDE. `supersedes`, when given, is the
        guide_id's immediately PRIOR published version — its steps are
        re-chunked (never re-read from a cache) purely to recover the exact
        chunk ids `chunk_id_for` gave them at THEIR embed time, the same
        content-addressing `ingest_incident`/`ingest_runbook` already lean on
        for idempotency. Those ids are retired in the SAME `VectorPort
        .supersede` call that indexes the new version — never a separate
        `.index()` followed by a separate delete, which is exactly the
        "delete-then-maybe-fail-to-reindex" shape that could leave a guide
        with no chunks at all. `supersedes` must itself still be Published
        (a stored version's lifecycle_state never changes after the fact —
        see `core.model.governed.GovernedObject.new_version`), or
        `chunk_runbook` refuses it exactly as it would any other non-
        Published source.
        """
        retire = (
            tuple(candidate.chunk_id for candidate in chunk_runbook(supersedes))
            if supersedes is not None
            else ()
        )
        return self._ingest(chunk_runbook(runbook), run_id=run_id, caller=caller, retire=retire)

    # ── the shared engine ────────────────────────────────────────────────────

    def _ingest(
        self,
        candidates: Sequence[ChunkCandidate],
        *,
        run_id: str,
        caller: Actor,
        retire: Sequence[str] = (),
    ) -> KnowledgeIngestResult:
        now = self._clock()
        verified: list[ChunkCandidate] = []
        refused: list[RefusedChunk] = []

        for candidate in candidates:
            findings = self._scrub.detect(candidate.text)
            if not findings:
                verified.append(candidate)
                continue
            entity_types = tuple(sorted({finding.entity_type for finding in findings}))
            reason = (
                f"{candidate.citation} tripped PHI detection ({', '.join(entity_types)}) — "
                "refused, never masked and embedded"
            )
            refused.append(
                RefusedChunk(citation=candidate.citation, reason=reason, entity_types=entity_types)
            )
            self._record(
                run_id=run_id,
                caller=caller,
                now=now,
                outcome=ActionOutcome.REFUSED_PHI,
                detail=f"{candidate.chunk_id}: {reason}",
            )

        if not verified:
            # CF-V1-W1-26: a supersede that refused EVERY new chunk retires
            # nothing either — the prior version's chunks are the only clean
            # copy that exists, and leaving them in place (stale in the
            # ordinary sense, never absent) beats deleting the last good
            # answer a guide had.
            return KnowledgeIngestResult(indexed=(), refused=tuple(refused))

        # ONE batched call — the same lesson the scrubber taught the mapping
        # pipeline (`memory/07-runbooks`'s lane-3 gate lessons): many small
        # calls where one will do is both slower and a second place to drift.
        embeddings = self._llm.embed(
            agent=AGENT,
            run_id=run_id,
            caller=caller,
            texts=tuple(candidate.text for candidate in verified),
        )
        phi_verified_at = now.isoformat()
        chunks = tuple(
            Chunk(
                chunk_id=candidate.chunk_id,
                text=candidate.text,
                citation=candidate.citation,
                metadata=_metadata_for(
                    candidate,
                    phi_verified_at=phi_verified_at,
                    embedding_model_version=embedding.model_version,
                ),
            )
            for candidate, embedding in zip(verified, embeddings, strict=True)
        )
        vectors = tuple(embedding.vector for embedding in embeddings)
        if retire:
            self._vector.supersede(retire=retire, chunks=chunks, vectors=vectors)
        else:
            self._vector.index(chunks, vectors)

        return KnowledgeIngestResult(
            indexed=tuple(candidate.citation for candidate in verified),
            refused=tuple(refused),
        )

    # ── audit ────────────────────────────────────────────────────────────────

    def _record(
        self, *, run_id: str, caller: Actor, now: datetime, outcome: ActionOutcome, detail: str
    ) -> None:
        self._metadata.append_agent_action(
            AgentAction(
                run_id=run_id,
                agent=AGENT,
                action="knowledge:phi_verify",
                outcome=outcome,
                actor=caller,
                occurred_ts=now,
                detail=detail,
            )
        )


def _metadata_for(
    candidate: ChunkCandidate, *, phi_verified_at: str, embedding_model_version: str
) -> dict[str, str]:
    """Everything `ports.vector.Chunk.metadata` (`dict[str, str]`) can carry —
    `knowledge.chunk`'s promoted columns (`kind`, `domain`, `source_org`,
    `feed_id`, `lifecycle_state`) plus the facts that column set has no seat
    for yet (`object_version`, `scope_tags`, `phi_verified_at`,
    `embedding_model_version`), all landing in the JSON `metadata` column
    every adapter already persists.
    """
    metadata: dict[str, str] = {
        "kind": candidate.kind,
        # Only a Published source ever reaches this worker (`.narrative()`'s
        # CLOSED gate; `chunk_runbook`'s Published gate) — recording it here
        # is what lets a Retired object's later delete-sweep know which rows
        # were true AT EMBED TIME, per `pg_vector.py`'s own note.
        "lifecycle_state": "published",
        "phi_verified_at": phi_verified_at,
        "embedding_model_version": embedding_model_version,
    }
    if candidate.domain:
        metadata["domain"] = candidate.domain
    if candidate.source_org:
        metadata["source_org"] = candidate.source_org
    if candidate.feed_id:
        metadata["feed_id"] = candidate.feed_id
    if candidate.object_version is not None:
        metadata["object_version"] = str(candidate.object_version)
    if candidate.scope_tags:
        metadata["scope_tags"] = ";".join(candidate.scope_tags)
    return metadata


__all__ = ["AGENT", "KnowledgeIngestWorker"]
