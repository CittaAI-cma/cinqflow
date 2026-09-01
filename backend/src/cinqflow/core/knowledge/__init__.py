"""CF-V1-E16-05 · CF-V1-E16-04 — the write side of the knowledge plane,
chunk-boundary half.

    "Knowledge ingestion becomes just another feed — Inbox -> Parse -> Chunk ->
     PHI-verify -> Steward approve -> Embed + Index — sharing the lifecycle
     engine, idempotency and quarantine discipline with data ingestion."
    "Only Published governed objects embed. Draft embeds nothing; Retired
     deletes its chunks."
    — ADR-0007

E16-05 SCOPED HONESTLY, THEN, AND E16-04 CLOSES THE GAP IT NAMED. E16-05's own
first cut chunked only a CLOSED `Incident`'s narrative and a PUBLISHED
`RUNBOOK`'s steps — both already structured Python objects with text already
extracted, so "Inbox" and "Parse" were, honestly, "the object already IS the
parsed unit". `chunk_document` is what E16-04 adds: a THIRD source, an
uploaded payer companion guide or client spec, which genuinely does arrive as
bytes that need `ports.document_parse` before anything here can chunk it. The
parsing itself happens one layer up — see this function's own docstring for
why — so this module's PURITY claim below is unchanged by the addition.

PURE. No port call lives in this module — mirroring `core.agents
.fingerprint_match.graph`'s own reason for staying beneath `cinqflow.ports`
in `.importlinter`'s layer stack: deciding chunk BOUNDARIES from text a
caller already extracted, and computing a content-addressed idempotency key,
touch nothing external. The stage that actually calls `PhiScrubPort`,
`LlmGateway.embed` and `VectorPort.index` is `workers.knowledge` — Archetype
B, the same shape `workers.incidents` and `workers.sla` already are.

CHUNKING, SCOPED HONESTLY. Naive is correct at this size: ONE chunk for an
incident's whole narrative (it is already one short, human-written account),
ONE chunk per runbook STEP, and ONE chunk per document PAGE — never the whole
guide as one blob, because a reviewer or a retriever wants to cite "step 3"
or "page 14", not the whole thing. A general-purpose sliding-window/token-
aware splitter would be solving a problem — long unstructured documents —
this slab does not have; a PAGE is already the unit E16-06's own happy path
cites by ("the companion guide p.14 defines MBR_DOB as CCYYMMDD").
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


def chunk_document(document: GovernedObject) -> tuple[ChunkCandidate, ...]:
    """One chunk per PAGE, from a PUBLISHED `ObjectType.KNOWLEDGE_DOCUMENT`.

    Reads `document.body["pages"]` as PLAIN DATA — a list of
    `{"number": int, "text": str}` mappings — never `ports.document_parse
    .ParsedDocument` itself: that type lives on the far side of
    `cinqflow.ports` in `.importlinter`'s layer stack, and this module stays
    beneath it exactly as its own docstring states. The API route that
    creates a document's Draft `GovernedObject` is what converts a real
    `ParsedDocument` into this body shape — the same place `GlossaryTerm
    .as_governed`'s body dict is built, one layer above `core`.

    A `ParsedTable` on a page is not chunked separately: `adapters.local
    .file_document_parse` already folds a table's `as_text()` into its
    page's `text`, so "never split a table mid-row" is a property of what
    arrives here, not a second rule this function has to re-enforce.

    Refuses (`KnowledgeSourceError`) a document that is not Published — the
    same ADR-0007 gate `chunk_runbook` enforces for its type, and for the
    identical reason: nothing upstream of this function enforces it for a
    bare `GovernedObject`.
    """
    if document.object_type is not ObjectType.KNOWLEDGE_DOCUMENT:
        raise KnowledgeSourceError(
            f"{document.object_type.value}:{document.object_id} is not a document"
        )
    if document.lifecycle_state is not LifecycleState.PUBLISHED:
        raise KnowledgeSourceError(
            f"document:{document.object_id}@v{document.version} is "
            f"{document.lifecycle_state.value} — only Published governed objects embed"
        )
    pages = tuple(document.body.get("pages", ()))
    if not pages:
        raise KnowledgeSourceError(
            f"document:{document.object_id}@v{document.version} has no pages to chunk"
        )

    feed_id = document.body.get("feed_id")
    domain = document.body.get("domain")
    scope_tags = (f"feed:{feed_id}",) if feed_id else ()

    candidates: list[ChunkCandidate] = []
    for page in pages:
        text = str(page.get("text", "")).strip()
        if not text:
            continue
        number = int(page["number"])
        citation = _page_citation(document.object_id, number)
        candidates.append(
            ChunkCandidate(
                chunk_id=chunk_id_for(citation, index=0, object_version=document.version),
                text=text,
                citation=citation,
                kind="document_page",
                domain=str(domain) if domain else None,
                feed_id=str(feed_id) if feed_id else None,
                object_version=document.version,
                scope_tags=scope_tags,
            )
        )
    if not candidates:
        raise KnowledgeSourceError(
            f"document:{document.object_id}@v{document.version} has no non-empty pages to chunk"
        )
    return tuple(candidates)


def _page_citation(document_id: str, page_number: int) -> CitationId:
    return CitationId(kind=CitationKind.DOCUMENT, subject=document_id, fragment=f"p{page_number}")


__all__ = [
    "ChunkCandidate",
    "KnowledgeIngestResult",
    "KnowledgeSourceError",
    "RefusedChunk",
    "chunk_document",
    "chunk_id_for",
    "chunk_incident",
    "chunk_runbook",
]
