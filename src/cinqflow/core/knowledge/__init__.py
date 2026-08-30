"""CF-V1-E16-05 — the write side of the knowledge plane, chunk-boundary half.

    "Knowledge ingestion becomes just another feed — Inbox -> Parse -> Chunk ->
     PHI-verify -> Steward approve -> Embed + Index — sharing the lifecycle
     engine, idempotency and quarantine discipline with data ingestion."
    "Only Published governed objects embed. Draft embeds nothing; Retired
     deletes its chunks."
    — ADR-0007

SCOPE, STATED HONESTLY. E16-05's full spec eventually chunks payer-spec PDFs,
persona uploads and scenario docs behind a document-upload Inbox with
layout-aware PDF parsing. NOTHING TODAY NEEDS THAT: the only two real
consumers (CF-V1-E16-07) are a CLOSED `Incident`'s narrative and a PUBLISHED
`ObjectType.RUNBOOK`'s steps — both already structured Python objects with
text already extracted. So "Inbox" and "Parse" are, honestly, "the object
already IS the parsed unit" — there is no file to upload and nothing here
parses a PDF. Generic document ingestion stays a separate, later story; the
functions below are named for what they actually do (`chunk_incident`,
`chunk_runbook`), not for a generic `parse()` this slab does not build.

PURE. No port call lives in this module — mirroring `core.agents
.fingerprint_match.graph`'s own reason for staying beneath `cinqflow.ports`
in `.importlinter`'s layer stack: deciding chunk BOUNDARIES from text a
caller already extracted, and computing a content-addressed idempotency key,
touch nothing external. The stage that actually calls `PhiScrubPort`,
`LlmGateway.embed` and `VectorPort.index` is `workers.knowledge` — Archetype
B, the same shape `workers.incidents` and `workers.sla` already are.

CHUNKING, SCOPED HONESTLY. Naive is correct at this size: ONE chunk for an
incident's whole narrative (it is already one short, human-written account),
and ONE chunk per runbook STEP — never the whole guide as one blob, because a
reviewer or a retriever wants to cite "step 3", not the whole guide. A
general-purpose sliding-window/token-aware splitter would be solving a
problem — long unstructured documents — this slab does not have.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType
from cinqflow.core.operations.fingerprint import Incident


class KnowledgeSourceError(RuntimeError):
    """A source object that cannot become knowledge at all.

    Distinct from a per-CHUNK PHI refusal (`workers.knowledge.RefusedChunk`):
    this is the source's OWN gate — ADR-0007's "only Published governed
    objects embed" — checked here because, unlike a closed `Incident`
    (`.narrative()` already refuses anything not CLOSED, and that refusal is
    reused unchanged), a `GovernedObject` carries no equivalent "may this
    embed" method today.
    """


@dataclass(frozen=True)
class ChunkCandidate:
    """One piece of text on its way to becoming a `ports.vector.Chunk` — every
    fact about it decided BEFORE any port is touched.

    Deliberately NOT `ports.vector.Chunk`: that type lives on the far side of
    the PHI-verify gate, and a `ChunkCandidate` a caller mistook for one
    cannot be indexed, because nothing in this module ever calls
    `VectorPort.index`.
    """

    chunk_id: str
    text: str
    citation: CitationId
    #: The plate's own vocabulary (`docs/architecture/plates/12-knowledge-
    #: plane-and-retrieval.md`'s `embedded_content`) — "incident_narrative" or
    #: "runbook_section" here, verbatim.
    kind: str
    domain: str | None = None
    source_org: str | None = None
    feed_id: str | None = None
    #: The governed object's version, for a runbook. `None` for an incident:
    #: `IncidentState` is deliberately NOT ADR-0006's lifecycle (see
    #: `core.operations.fingerprint.IncidentState`'s own docstring), so an
    #: incident has no version to carry.
    object_version: int | None = None
    scope_tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RefusedChunk:
    """A chunk that never reached `VectorPort.index`, and why.

    The knowledge plane's own quarantine record — what `quarantine_records`
    holds for a data row that failed a DQ rule, this holds for a chunk that
    failed PHI-verify: the refusal is recorded, never silent, and the value
    that tripped it is NEVER carried here, only the entity TYPES `Finding`
    itself is willing to name (see `core.model.phi.Finding`'s own docstring).
    """

    citation: CitationId
    reason: str
    entity_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeIngestResult:
    """What one call into the pipeline produced.

    `refused` is not decoration, the same discipline
    `intelligence.agents.fingerprint_match.DraftedGuide.refusals` and
    `mapping_suggestion.SuggestionResult.refusals` already apply: a chunk
    that did not make it is a finding a reviewer should be able to see.
    """

    indexed: tuple[CitationId, ...] = ()
    refused: tuple[RefusedChunk, ...] = ()

    @property
    def indexed_count(self) -> int:
        return len(self.indexed)

    @property
    def refused_count(self) -> int:
        return len(self.refused)


def chunk_id_for(citation: CitationId, *, index: int, object_version: int | None) -> str:
    """Content-addressed, so re-chunking the SAME source at the SAME version
    reproduces the SAME id.

    The idempotency key `input_registry`'s own fingerprint-then-skip
    discipline uses IN SPIRIT, ported to knowledge: `(citation_id,
    chunk_index, object_version)`, never a file fingerprint — there is no
    file here to fingerprint. A real `VectorPort` upserts on this id
    (`ON CONFLICT (chunk_id) DO UPDATE`, `adapters.local.pg_vector
    .PgVectorStore.index`'s own SQL), which is what makes re-running this
    pipeline for an unchanged source a no-op on the STORE rather than a
    second row — the same content-addressing `_incident_id` already relies
    on one plane over.
    """
    material = f"{citation}|{index}|{object_version if object_version is not None else '-'}"
    return "chunk-" + hashlib.sha256(material.encode()).hexdigest()[:24]


def chunk_incident(incident: Incident) -> tuple[ChunkCandidate, ...]:
    """ONE chunk: the closed incident's own narrative.

    `.narrative()` IS the gate — it raises `IncidentTransitionError` for
    anything not CLOSED-with-a-resolution, and this function does not
    duplicate that check, the same way `fingerprint_batch` does not
    re-derive what `separate_cascade` already decided. Callers see that
    exception unwrapped: it is the source's own refusal, not a
    knowledge-plane-specific one.
    """
    text = incident.narrative()
    citation = incident.citation
    return (
        ChunkCandidate(
            chunk_id=chunk_id_for(citation, index=0, object_version=None),
            text=text,
            citation=citation,
            kind="incident_narrative",
            feed_id=incident.feed_id,
            scope_tags=(f"feed:{incident.feed_id}", f"signature:{incident.signature}"),
        ),
    )


def chunk_runbook(runbook: GovernedObject) -> tuple[ChunkCandidate, ...]:
    """One chunk per STEP, never the whole guide.

    Refuses (`KnowledgeSourceError`) a runbook that is not Published — ADR-
    0007's own gate, enforced here because nothing upstream of this function
    enforces it for a bare `GovernedObject`. `steps`, `signatures` and the
    optional `domain`/`feed_id` are read the same tolerant way
    `workers.incidents.recovery_guides` already reads a runbook's body — this
    function does not invent a stricter body contract than the one the
    fingerprint-match agent's drafts already publish.
    """
    if runbook.object_type is not ObjectType.RUNBOOK:
        raise KnowledgeSourceError(
            f"{runbook.object_type.value}:{runbook.object_id} is not a runbook"
        )
    if runbook.lifecycle_state is not LifecycleState.PUBLISHED:
        raise KnowledgeSourceError(
            f"runbook:{runbook.object_id}@v{runbook.version} is "
            f"{runbook.lifecycle_state.value} — only Published governed objects embed"
        )
    steps = tuple(str(step).strip() for step in runbook.body.get("steps", ()) if str(step).strip())
    if not steps:
        raise KnowledgeSourceError(
            f"runbook:{runbook.object_id}@v{runbook.version} has no steps to chunk"
        )

    domain = runbook.body.get("domain")
    feed_id = runbook.body.get("feed_id")
    signatures = tuple(sorted(str(s) for s in runbook.body.get("signatures", ())))
    scope_tags = tuple(f"signature:{sig}" for sig in signatures)
    if feed_id:
        scope_tags = (f"feed:{feed_id}", *scope_tags)

    return tuple(
        ChunkCandidate(
            chunk_id=chunk_id_for(
                _step_citation(runbook.object_id, position), index=0, object_version=runbook.version
            ),
            text=step,
            citation=_step_citation(runbook.object_id, position),
            kind="runbook_section",
            domain=str(domain) if domain else None,
            feed_id=str(feed_id) if feed_id else None,
            object_version=runbook.version,
            scope_tags=scope_tags,
        )
        for position, step in enumerate(steps, start=1)
    )


def _step_citation(guide_id: str, position: int) -> CitationId:
    return CitationId(kind=CitationKind.RUNBOOK, subject=guide_id, fragment=f"step-{position}")


__all__ = [
    "ChunkCandidate",
    "KnowledgeIngestResult",
    "KnowledgeSourceError",
    "RefusedChunk",
    "chunk_id_for",
    "chunk_incident",
    "chunk_runbook",
]
