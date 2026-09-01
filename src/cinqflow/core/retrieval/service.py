"""CF-V1-E16-05 — one retrieval, and the record that makes it replayable.

    "RBAC scope filter first, hybrid lexical-plus-vector fetch, metadata boost
     (same feed over same source over same domain over global), rerank, and
     budgeted packing — with every chunk entering prompts labeled by its
     citation_id."
    — CF-V1-E16-05

WHAT WAVE 0 LEFT HERE, AND WHAT THIS ADDS. `core.retrieval.ReferenceIndex` is
the LEXICAL half, written in Wave 0 and unchanged: BM25-ish scoring over the
generated platform vocabulary and the client's seeded glossary. This module is
the other four things the story asks for and Wave 0 explicitly did not ship —
the scope contract, the fusion, the boost ladder and the packing record. It
adds no scorer of its own: a lexical hit and a vector hit arrive already
scored, and this module only decides which of them a prompt gets to see.

PURE. No port call, no embedding, no vector store. `RetrievalQuery` names the
caller's scopes and `scope_filter()` renders them as the filter a `VectorPort
.retrieve` applies BEFORE similarity — this module never sees a chunk that
scope should have excluded, because it never runs the query. The wiring that
does live in `intelligence.retrieval`.

WHY RECIPROCAL-RANK FUSION AND NOT A WEIGHTED SUM. BM25 scores and cosine
similarities are not on one scale and never will be: BM25 is unbounded and
corpus-relative, cosine is [-1, 1] and query-relative, and the constant that
makes them comparable on the glossary is the wrong constant on the runbook
corpus. RRF compares RANKS, which are the same kind of number in both lists,
so it needs no tuning per corpus — the reason it is the standard answer to
exactly this problem. `K` damps the top of each list so a single list's first
result cannot dominate a document both lists agree on, which is the whole
point of asking two retrievers.

THE BOOST LADDER IS AN ORDERING, NOT A SCORE. "same feed over same source over
same domain over global" is four tiers, and a tier ALWAYS beats the tier below
it regardless of fused score — a Fidelis precedent outranks an Optum one even
when the Optum chunk matched better lexically, because the story says
"Fidelis precedents outrank Optum ones" without qualification. Adding tier
weights to the fused score instead would make that a tendency; making the tier
the primary sort key makes it a fact.

THIN GROUNDING IS NEVER PADDED. `RELEVANCE_FLOOR` cuts before packing, and a
query whose candidates all fall below it returns `EMPTY`, whose `packed` is
the empty string and whose `is_empty` is True. The caller's contract is to
degrade to "needs your input" — that is the story's own exception path, and
the alternative (returning the best of a bad set) is how an agent comes to
cite a chunk that does not support its claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from cinqflow.core.citations import CitationId
from cinqflow.core.model.identity import Principal
from cinqflow.core.retrieval import ScoredEntry

__all__ = [
    "EMPTY",
    "FLOOR_RANK",
    "PACK_BUDGET_CHARS",
    "RELEVANCE_FLOOR",
    "RRF_K",
    "Candidate",
    "Grounding",
    "PackedChunk",
    "Proximity",
    "RetrievalQuery",
    "SemanticHit",
    "fuse",
]

#: Reciprocal-rank fusion's damping constant. 60 is the value the original
#: RRF paper found stable across corpora, and it is stated rather than tuned
#: because tuning it per corpus is exactly the coupling RRF exists to avoid.
RRF_K = 60

#: The deepest rank at which ONE retriever's opinion, alone, still counts as
#: evidence. A document neither retriever put in its top ten did not match
#: this query; it merely failed to be excluded by it.
FLOOR_RANK = 10

#: The floor, DERIVED from `FLOOR_RANK` rather than chosen as a number,
#: because a bare decimal here would be a constant nobody could argue with.
#: Stated as a rank rule it is arguable, and the rule is:
#:
#:   a candidate survives if EITHER retriever ranked it in its top ten,
#:   OR both retrievers returned it at all.
#:
#: The second clause falls out of the arithmetic rather than being coded: a
#: document at rank 20 of both lists scores 2/80 = 0.025, comfortably above
#: 1/70 ≈ 0.0143, because agreement between two independent retrievers is
#: itself evidence — which is the entire reason for asking two.
#: The epsilon makes rank FLOOR_RANK itself survive under float rounding.
RELEVANCE_FLOOR = 1.0 / (RRF_K + FLOOR_RANK) - 1e-9

#: How much retrieved text a prompt may carry. Bounded because grounding
#: competes with the input for the model's attention and for the budget the
#: gateway meters — an unbounded packer is an unbounded bill.
PACK_BUDGET_CHARS = 2400


class Proximity(IntEnum):
    """The boost ladder, as an ordering. Higher wins, always.

    Named rather than numbered at the call site so the sort key reads as the
    story's own sentence: same feed over same source over same domain over
    global.
    """

    GLOBAL = 0
    SAME_DOMAIN = 1
    SAME_SOURCE = 2
    SAME_FEED = 3


@dataclass(frozen=True)
class RetrievalQuery:
    """What is being asked, by whom, and about what.

    `feed_id`/`source_org`/`domain` do double duty on purpose: they are the
    boost ladder's tiers AND, where the caller's scopes restrict them, the
    filter the vector store applies before similarity. One object, so a
    caller cannot boost toward a feed it is not scoped to see.
    """

    text: str
    feed_id: str | None = None
    source_org: str | None = None
    domain: str | None = None
    #: The caller's feed scopes, verbatim from `Principal.scopes.feeds`.
    #: `{"*"}` (or empty) means unrestricted, which is how `core.model
    #: .identity.Scopes` already spells it.
    allowed_feeds: frozenset[str] = frozenset({"*"})
    limit: int = 8

    @classmethod
    def for_caller(
        cls,
        text: str,
        principal: Principal,
        *,
        feed_id: str | None = None,
        source_org: str | None = None,
        domain: str | None = None,
        limit: int = 8,
    ) -> RetrievalQuery:
        """Build the query from the CALLER, never from the call site's idea
        of who the caller is.

        A system actor (`workers.incidents` off a failed batch, say) has no
        human scope to inherit, and `core.model.identity.Scopes` already
        spells unrestricted as `{"*"}` — so this reads that field rather than
        special-casing the actor type, and a future narrowing of the platform
        principal narrows retrieval with it for free.
        """
        return cls(
            text=text,
            feed_id=feed_id,
            source_org=source_org,
            domain=domain,
            allowed_feeds=frozenset(principal.scopes.feeds or frozenset({"*"})),
            limit=limit,
        )

    @property
    def unrestricted(self) -> bool:
        return not self.allowed_feeds or "*" in self.allowed_feeds

    def scope_filter(self) -> dict[str, str]:
        """The filter `VectorPort.retrieve` applies BEFORE similarity.

        A single-feed caller gets `{"feed_id": ...}` — the restriction in the
        query, never in the answer, which is the story's guardrail stated as
        code. A caller scoped to SEVERAL feeds gets `{}` here and is filtered
        by `admits()` after retrieval, because `VectorPort`'s filter is an
        equality map and cannot express "one of these three"; that is a
        narrower port verb than this platform needs and widening it is a plate
        change, not a decision this module may make. The post-filter is
        correct but weaker — it can return fewer than `limit` results — and
        `Grounding.scope_narrowed` records when it fired so the weakness is
        visible rather than silent.
        """
        if self.unrestricted or len(self.allowed_feeds) != 1:
            return {}
        (only,) = tuple(self.allowed_feeds)
        return {"feed_id": only}

    def admits(self, metadata: dict[str, str]) -> bool:
        """Whether this caller may see a chunk. Global knowledge is admitted:
        a chunk carrying no `feed_id` belongs to no feed, so no feed scope
        excludes it — the platform's own vocabulary and every runbook."""
        if self.unrestricted:
            return True
        feed = metadata.get("feed_id")
        return feed is None or feed == "" or feed in self.allowed_feeds

    def proximity(self, metadata: dict[str, str]) -> Proximity:
        if self.feed_id and metadata.get("feed_id") == self.feed_id:
            return Proximity.SAME_FEED
        if self.source_org and metadata.get("source_org") == self.source_org:
            return Proximity.SAME_SOURCE
        if self.domain and metadata.get("domain") == self.domain:
            return Proximity.SAME_DOMAIN
        return Proximity.GLOBAL


@dataclass(frozen=True)
class SemanticHit:
    """One vector-store result, reduced to what fusion needs.

    Deliberately not `ports.vector.ScoredChunk`: that type lives above this
    module in the layer stack, and a pure core function that imported it
    would make `core` depend on `ports`.
    """

    citation: CitationId
    text: str
    label: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    """One retrievable thing, and every reason it survived.

    `lexical_rank` and `semantic_rank` are 1-based and `None` where the
    retriever in question did not return it at all. Both are kept after
    fusion because "the vector store found this and the index did not" is
    the fact an evaluation replay needs, and a fused score alone cannot say
    it.
    """

    citation: CitationId
    text: str
    label: str
    metadata: dict[str, str] = field(default_factory=dict)
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    fused_score: float = 0.0
    proximity: Proximity = Proximity.GLOBAL

    @property
    def both_lists(self) -> bool:
        return self.lexical_rank is not None and self.semantic_rank is not None


@dataclass(frozen=True)
class PackedChunk:
    """One chunk that actually entered a prompt, with the reason it did.

    `text` is the chunk VERBATIM, and it is here rather than only inside
    `Grounding.packed` for two reasons. A record that cannot reproduce what
    was packed is not replayable, which is the story's own word for what this
    record is for. And the deterministic checks that read retrieved
    knowledge — `core.agents.document_evidence`, which compares a guide's
    stated column count against the file's measured one — need the text
    ATTACHED TO ITS CITATION; re-splitting the joined prompt string to
    recover the pairing would be parsing our own output.
    """

    citation: CitationId
    label: str
    text: str
    chars: int
    fused_score: float
    proximity: Proximity
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True)
class Grounding:
    """The retrieval, its text, and the record that replays it.

    `packed` is what a prompt carries. `chunks` is what an audit reads. They
    are separate because the story asks for both — "every chunk entering
    prompts labeled by its citation_id" and "record which chunks were packed
    per call, so any retrieval is replayable" — and a caller that logged the
    prompt string instead would be logging something a prompt-template change
    silently invalidates.
    """

    packed: str = ""
    chunks: tuple[PackedChunk, ...] = ()
    considered: int = 0
    below_floor: int = 0
    scope_narrowed: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def citations(self) -> tuple[CitationId, ...]:
        return tuple(chunk.citation for chunk in self.chunks)

    def why_empty(self) -> str:
        """The sentence a caller degrades with. Never "no results" alone —
        an agent told only that cannot tell a scoped-out corpus from an
        unindexed one, and neither can the person reading its refusal."""
        if self.chunks:
            return ""
        if self.considered == 0:
            return "no retriever returned a candidate for this query"
        if self.scope_narrowed and self.scope_narrowed == self.considered:
            return "every match lay outside your scope"
        return (
            f"{self.below_floor} of {self.considered} matches fell below the relevance "
            "floor — thin grounding is not padded"
        )


#: The one empty result, so a caller can compare identity rather than
#: reconstructing the reason a retrieval found nothing.
EMPTY = Grounding()


def fuse(
    query: RetrievalQuery,
    *,
    lexical: Sequence[ScoredEntry] = (),
    semantic: Sequence[SemanticHit] = (),
    budget_chars: int = PACK_BUDGET_CHARS,
) -> Grounding:
    """Scope, fuse, boost, floor, pack — in that order, once.

    The order is the story's, and every step of it is load-bearing:

      • SCOPE first, so a chunk the caller cannot reach never competes for a
        place in the budget (and, where the filter could be pushed into the
        query, never reached this function at all).
      • FUSE by reciprocal rank, because BM25 and cosine do not share a scale.
      • BOOST as a tier ordering, so precedent proximity is a fact rather
        than a tendency.
      • FLOOR before packing, so a thin result set becomes an honest refusal
        rather than a full budget of weak matches.
      • PACK by whole chunk, never mid-text — half a definition is a fact
        with its qualifier removed.
    """
    pooled: dict[str, Candidate] = {}
    narrowed = 0

    for rank, scored in enumerate(lexical, start=1):
        metadata = {"kind": "term", "source_org": scored.entry.source}
        if not query.admits(metadata):
            narrowed += 1
            continue
        key = str(scored.entry.citation)
        pooled[key] = Candidate(
            citation=scored.entry.citation,
            text=f"{scored.entry.term}: {scored.entry.definition}",
            label=scored.entry.term,
            metadata=metadata,
            lexical_rank=rank,
        )

    for rank, hit in enumerate(semantic, start=1):
        if not query.admits(hit.metadata):
            narrowed += 1
            continue
        key = str(hit.citation)
        existing = pooled.get(key)
        if existing is None:
            pooled[key] = Candidate(
                citation=hit.citation,
                text=hit.text,
                label=hit.label,
                metadata=hit.metadata,
                semantic_rank=rank,
            )
        else:
            # The same citation from both retrievers. Keep the LEXICAL text:
            # a glossary definition is the approved wording, and a vector
            # chunk of the same term is a projection of it.
            pooled[key] = Candidate(
                citation=existing.citation,
                text=existing.text,
                label=existing.label,
                metadata={**hit.metadata, **existing.metadata},
                lexical_rank=existing.lexical_rank,
                semantic_rank=rank,
            )

    scored_pool: list[Candidate] = []
    for candidate in pooled.values():
        score = 0.0
        if candidate.lexical_rank is not None:
            score += 1.0 / (RRF_K + candidate.lexical_rank)
        if candidate.semantic_rank is not None:
            score += 1.0 / (RRF_K + candidate.semantic_rank)
        scored_pool.append(
            Candidate(
                citation=candidate.citation,
                text=candidate.text,
                label=candidate.label,
                metadata=candidate.metadata,
                lexical_rank=candidate.lexical_rank,
                semantic_rank=candidate.semantic_rank,
                fused_score=score,
                proximity=query.proximity(candidate.metadata),
            )
        )

    considered = len(scored_pool) + narrowed
    above = [c for c in scored_pool if c.fused_score >= RELEVANCE_FLOOR]
    below = len(scored_pool) - len(above)

    # Tier first, fused score second, citation third. The third key is not
    # decoration: two chunks in one tier with one fused score must order the
    # same way on every run, or a retrieval replay compares packings that
    # differ for no reason a reviewer could act on.
    above.sort(key=lambda c: (-int(c.proximity), -c.fused_score, str(c.citation)))

    lines: list[str] = []
    packed: list[PackedChunk] = []
    used = 0
    for candidate in above[: query.limit]:
        block = f"[{candidate.citation}] {candidate.text}"
        if used + len(block) > budget_chars:
            break
        lines.append(block)
        used += len(block)
        packed.append(
            PackedChunk(
                citation=candidate.citation,
                label=candidate.label,
                text=candidate.text,
                chars=len(block),
                fused_score=round(candidate.fused_score, 6),
                proximity=candidate.proximity,
                lexical_rank=candidate.lexical_rank,
                semantic_rank=candidate.semantic_rank,
            )
        )

    return Grounding(
        packed="\n".join(lines),
        chunks=tuple(packed),
        considered=considered,
        below_floor=below,
        scope_narrowed=narrowed,
    )
