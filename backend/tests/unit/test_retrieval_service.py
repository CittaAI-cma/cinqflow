"""CF-V1-E16-05 — the scope filter, the fusion, the ladder and the floor.

"Apply the caller's scopes as query filters before any similarity
 computation."
"Combine lexical and vector candidates ... merged by reciprocal-rank
 fusion."
"Given a query matches nothing above the relevance floor ... the agent
 receives an explicit empty-grounding result and must degrade to 'needs
 your input' — thin grounding is never padded with weak matches."
— CF-V1-E16-05
"""

from __future__ import annotations

import pytest

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.retrieval import ReferenceEntry, ScoredEntry
from cinqflow.core.retrieval.service import (
    FLOOR_RANK,
    RRF_K,
    Grounding,
    Proximity,
    RetrievalQuery,
    SemanticHit,
    fuse,
)

pytestmark = pytest.mark.unit


def _entry(slug: str, term: str) -> ReferenceEntry:
    return ReferenceEntry(
        term=term,
        definition=f"the definition of {term}",
        source="platform",
        slug=slug,
    )


def _lexical(*slugs: str) -> tuple[ScoredEntry, ...]:
    return tuple(
        ScoredEntry(entry=_entry(slug, slug.replace("-", " ")), score=1.0, matched=())
        for slug in slugs
    )


def _hit(
    subject: str, *, kind: CitationKind = CitationKind.DOCUMENT, **metadata: str
) -> SemanticHit:
    return SemanticHit(
        citation=CitationId(kind, subject),
        text=f"page text for {subject}",
        label="document",
        score=0.9,
        metadata=metadata,
    )


# ── the scope filter lives in the QUERY ──────────────────────────────────────


def test_a_single_feed_caller_pushes_the_restriction_into_the_query() -> None:
    query = RetrievalQuery(text="dob", allowed_feeds=frozenset({"fidelis"}))
    assert query.scope_filter() == {"feed_id": "fidelis"}


def test_an_unrestricted_caller_filters_nothing() -> None:
    query = RetrievalQuery(text="dob", allowed_feeds=frozenset({"*"}))
    assert query.scope_filter() == {}
    assert query.admits({"feed_id": "anything"})


def test_a_chunk_from_another_feed_is_refused_even_when_the_store_returned_it() -> None:
    """The port's equality filter cannot express "one of three", so a
    multi-feed caller is filtered after retrieval — and the count says so."""
    query = RetrievalQuery(text="dob", allowed_feeds=frozenset({"fidelis", "centene"}))
    assert query.scope_filter() == {}, "no single feed to push down"
    assert not query.admits({"feed_id": "optum"})
    grounding = fuse(query, semantic=(_hit("optum-guide", feed_id="optum"),))
    assert grounding.is_empty
    assert grounding.scope_narrowed == 1
    assert "outside your scope" in grounding.why_empty()


def test_global_knowledge_belongs_to_no_feed_so_no_feed_scope_excludes_it() -> None:
    query = RetrievalQuery(text="dob", allowed_feeds=frozenset({"fidelis"}))
    assert query.admits({}), "a chunk with no feed_id is the glossary, a runbook, the vocabulary"


def test_the_query_is_built_from_the_callers_own_scopes() -> None:
    principal = Principal(
        subject="ba@x", display_name="BA", scopes=Scopes(feeds=frozenset({"fidelis"}))
    )
    query = RetrievalQuery.for_caller("dob", principal)
    assert query.allowed_feeds == frozenset({"fidelis"})
    assert query.scope_filter() == {"feed_id": "fidelis"}


# ── reciprocal-rank fusion ───────────────────────────────────────────────────


def test_agreement_between_two_retrievers_outranks_one_retrievers_first_place() -> None:
    """The whole reason for asking two retrievers, as arithmetic."""
    query = RetrievalQuery(text="dob")
    # The SAME citation from both retrievers — a lexical entry cites
    # `term:<slug>`, so an agreeing chunk must too.
    agreed = CitationId(CitationKind.TERM, "agreed")
    lexical = (
        ScoredEntry(entry=_entry("solo", "solo"), score=9.0, matched=()),
        ScoredEntry(entry=_entry("agreed", "agreed"), score=1.0, matched=()),
    )
    semantic = (SemanticHit(citation=agreed, text="t", label="term", score=0.5),)
    grounding = fuse(query, lexical=lexical, semantic=semantic)
    ranked = [str(chunk.citation) for chunk in grounding.chunks]
    assert ranked[0] == "term:agreed", ranked


def test_a_document_both_lists_rank_deep_still_survives_the_floor() -> None:
    """Rank 20 in both lists scores 2/80, comfortably above 1/70 — which is
    the second clause of the floor's rule, arrived at by arithmetic rather
    than by being coded."""
    query = RetrievalQuery(text="dob", limit=50)
    deep = CitationId(CitationKind.TERM, "deep")
    lexical = (
        *(ScoredEntry(entry=_entry(f"f{i}", f"f{i}"), score=1.0, matched=()) for i in range(19)),
        ScoredEntry(entry=_entry("deep", "deep"), score=1.0, matched=()),
    )
    semantic = (
        *(_hit(f"s{i}") for i in range(19)),
        SemanticHit(citation=deep, text="t", label="term", score=0.1),
    )
    grounding = fuse(query, lexical=lexical, semantic=semantic, budget_chars=100_000)
    assert "term:deep" in {str(c.citation) for c in grounding.chunks}


def test_a_single_list_hit_below_the_floor_rank_is_not_grounding() -> None:
    query = RetrievalQuery(text="dob", limit=50)
    lexical = tuple(
        ScoredEntry(entry=_entry(f"f{i}", f"f{i}"), score=1.0, matched=())
        for i in range(FLOOR_RANK + 3)
    )
    grounding = fuse(query, lexical=lexical, budget_chars=100_000)
    assert len(grounding.chunks) == FLOOR_RANK
    assert grounding.below_floor == 3
    assert grounding.chunks[-1].fused_score > 1.0 / (RRF_K + FLOOR_RANK + 1)


# ── the boost ladder is an ORDERING, not a nudge ─────────────────────────────


def test_a_same_feed_precedent_outranks_a_better_scoring_global_one() -> None:
    """ "Fidelis precedents outrank Optum ones" — without qualification, so
    the tier is the primary sort key rather than a weight on the score."""
    query = RetrievalQuery(text="dob", feed_id="fidelis", source_org="fidelis-health")
    semantic = (
        _hit("global-guide"),  # rank 1, best fused score
        _hit("fidelis-guide", feed_id="fidelis"),  # rank 2
    )
    grounding = fuse(query, semantic=semantic)
    assert [c.proximity for c in grounding.chunks] == [Proximity.SAME_FEED, Proximity.GLOBAL]
    assert grounding.chunks[0].fused_score < grounding.chunks[1].fused_score, (
        "the tier won DESPITE the score, which is the point"
    )


def test_the_ladder_descends_feed_then_source_then_domain_then_global() -> None:
    query = RetrievalQuery(
        text="dob", feed_id="fidelis", source_org="fidelis-health", domain="member"
    )
    semantic = (
        _hit("d", domain="member"),
        _hit("c", source_org="fidelis-health"),
        _hit("b", feed_id="fidelis"),
        _hit("a"),
    )
    grounding = fuse(query, semantic=semantic)
    assert [c.proximity.name for c in grounding.chunks] == [
        "SAME_FEED",
        "SAME_SOURCE",
        "SAME_DOMAIN",
        "GLOBAL",
    ]


# ── thin grounding is never padded ───────────────────────────────────────────


def test_nothing_retrieved_is_an_explicit_empty_result_the_caller_must_degrade_on() -> None:
    grounding = fuse(RetrievalQuery(text="dob"))
    assert grounding.is_empty
    assert grounding.packed == ""
    assert grounding.citations == ()
    assert "no retriever returned a candidate" in grounding.why_empty()


def test_the_pack_truncates_by_whole_chunk_never_mid_definition() -> None:
    query = RetrievalQuery(text="dob", limit=10)
    lexical = _lexical("alpha", "beta", "gamma")
    grounding = fuse(query, lexical=lexical, budget_chars=60)
    assert len(grounding.chunks) < 3
    for chunk in grounding.chunks:
        assert f"[{chunk.citation}]" in grounding.packed
    assert not grounding.packed.endswith("…")


# ── the record is what makes a retrieval replayable ──────────────────────────


def test_every_packed_chunk_records_why_it_was_packed() -> None:
    query = RetrievalQuery(text="dob", feed_id="fidelis")
    grounding = fuse(
        query,
        lexical=_lexical("alpha"),
        semantic=(_hit("fidelis-guide", feed_id="fidelis"),),
    )
    assert grounding.chunks
    for chunk in grounding.chunks:
        assert chunk.chars > 0
        assert chunk.fused_score > 0
        assert chunk.lexical_rank is not None or chunk.semantic_rank is not None


def test_ties_order_identically_on_every_run() -> None:
    """A replay that compares two packings must compare the same packing."""
    query = RetrievalQuery(text="dob")
    semantic = (_hit("zulu"), _hit("alpha"))
    first = fuse(query, semantic=semantic)
    second = fuse(query, semantic=semantic)
    assert [str(c.citation) for c in first.chunks] == [str(c.citation) for c in second.chunks]


def test_an_empty_grounding_is_falsy_about_its_own_citations() -> None:
    assert Grounding().is_empty
    assert Grounding().citations == ()
