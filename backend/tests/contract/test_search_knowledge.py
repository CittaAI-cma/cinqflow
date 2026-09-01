"""CF-V1-E16-04/E16-05 — `search_knowledge`, the semantic (K2) half of hybrid
retrieval, joining `lookup_reference`'s lexical (K1) half.

    "This is the lexical half of hybrid retrieval arriving early... Wave 1's
     vector half adds a second scorer and reuses all of this unchanged."
    — core/retrieval, CF-V0-E16-09

Three honest degrades and one real hit, mirroring exactly what
`intelligence.tools._search_knowledge`'s own docstring promises: not fitted,
empty store, PHI-refused, and — the case none of the other four tests can
stand in for — an actual embedded chunk coming back with its citation intact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.identity import Role
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext, invoke
from cinqflow.ports.authn import Principal, Scopes
from cinqflow.ports.phi_scrub import PhiScrubPort
from cinqflow.ports.vector import Chunk

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def _principal() -> Principal:
    return Principal(
        subject="priya@cinqcare.test",
        display_name="Priya Nair",
        roles=frozenset({Role.OPERATIONS}),
        scopes=Scopes(feeds=frozenset({"*"}), domains=frozenset({"*"})),
    )


def _gateway(
    llm: ScriptedLlm, metadata: MemMetadataDb, *, scrub: PhiScrubPort | None = None
) -> LlmGateway:
    return LlmGateway(
        llm=llm,
        phi_scrub=scrub or PatternPhiScrub(),
        metadata_db=metadata,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("1"), per_agent_per_day_usd=Decimal("10")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )


def _context(
    *,
    llm: LlmGateway | None = None,
    vector: ListVector | None = None,
    phi_scrub: PatternPhiScrub | None = None,
) -> ToolContext:
    return ToolContext(
        principal=_principal(),
        control=MemStoreControlTables(),
        metadata=MemMetadataDb(),
        run_id="run-search-knowledge",
        agent="fingerprint-match",
        now=NOW,
        llm=llm,
        vector=vector,
        phi_scrub=phi_scrub,
    )


def test_neither_pin_fitted_degrades_honestly() -> None:
    context = _context()
    result = invoke(context, "search_knowledge", {"query": "missing parameter enrollment"})
    assert result.is_empty
    assert "not fitted" in result.note


def test_llm_fitted_but_vector_absent_still_degrades() -> None:
    llm = ScriptedLlm()
    context = _context(llm=_gateway(llm, MemMetadataDb()))
    result = invoke(context, "search_knowledge", {"query": "missing parameter"})
    assert result.is_empty
    assert "not fitted" in result.note


def test_an_empty_vector_store_is_reported_not_faked() -> None:
    context = _context(llm=_gateway(ScriptedLlm(), MemMetadataDb()), vector=ListVector())
    result = invoke(context, "search_knowledge", {"query": "missing parameter enrollment"})
    assert result.is_empty
    assert "empty" in result.note


def test_a_query_that_trips_phi_is_refused_before_reaching_the_model() -> None:
    llm = ScriptedLlm()
    vector = ListVector()
    vector.index(
        (
            Chunk(
                chunk_id="chunk-1",
                text="a runbook step",
                citation=CitationId(kind=CitationKind.RUNBOOK, subject="RB-1", fragment="step-1"),
            ),
        ),
        ((1.0,) * 8,),
    )
    context = _context(
        llm=_gateway(llm, MemMetadataDb()), vector=vector, phi_scrub=PatternPhiScrub()
    )
    # PatternPhiScrub flags an SSN-shaped string.
    result = invoke(context, "search_knowledge", {"query": "member SSN 123-45-6789"})
    assert result.is_empty
    assert "refused" in result.note
    assert llm.calls == [], "the model must never see a query that tripped PHI detection"


def test_a_real_semantic_hit_comes_back_with_its_citation() -> None:
    metadata = MemMetadataDb()
    llm = ScriptedLlm()
    vector = ListVector()
    citation = CitationId(kind=CitationKind.DOCUMENT, subject="doc-abc123", fragment="p14")
    # The mock embeds deterministically from text — indexing the SAME text as
    # the query guarantees a same-vector, top-ranked hit without needing a
    # real similarity model in a Lane-1 test.
    matching_text = "missing parameter enrollment failure"
    chunk = Chunk(
        chunk_id="chunk-doc-1",
        text=matching_text,
        citation=citation,
        metadata={"kind": "document_page"},
    )
    vector.index((chunk,), (llm.embed((matching_text,))[0].vector,))
    context = _context(
        llm=_gateway(llm, metadata),
        vector=vector,
        phi_scrub=PatternPhiScrub(),
    )
    result = invoke(context, "search_knowledge", {"query": matching_text, "limit": 3})
    assert not result.is_empty
    assert result.citations == (citation,)
    assert result.rows[0]["kind"] == "document_page"
    assert result.rows[0]["citation_id"] == str(citation)


def test_search_knowledge_is_unscoped_by_feed() -> None:
    """ADR-0023's own reasoning for why a recovery guide is keyed by
    fingerprint, never by feed, applies identically here: the same failure
    recurring on a SECOND feed is exactly what this tool exists to surface."""
    from cinqflow.core.tools import spec_for

    assert spec_for("search_knowledge").scoped_by_feed is False


def test_search_knowledge_reads_only_the_knowledge_chunk_plane() -> None:
    from cinqflow.core.tools import READABLE, spec_for

    spec = spec_for("search_knowledge")
    assert spec.reads == frozenset({"knowledge.chunk"})
    assert spec.reads <= READABLE


def test_an_embedding_failure_degrades_rather_than_crashes_the_node() -> None:
    from cinqflow.intelligence.gateway import EmbeddingFailedError
    from cinqflow.ports.llm import Embedding

    class _RefusingLlm(ScriptedLlm):
        def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
            raise EmbeddingFailedError("transport failure")

    # A non-empty store, so the call reaches the embed step this test is
    # actually about rather than degrading earlier at the "store is empty"
    # check — that check is a real, separate degrade path, exercised on its
    # own above.
    vector = ListVector()
    vector.index(
        (
            Chunk(
                chunk_id="chunk-1",
                text="a runbook step",
                citation=CitationId(kind=CitationKind.RUNBOOK, subject="RB-1", fragment="step-1"),
            ),
        ),
        ((1.0,) * 8,),
    )
    gateway = _gateway(_RefusingLlm(), MemMetadataDb())
    context = _context(llm=gateway, vector=vector)
    result = invoke(context, "search_knowledge", {"query": "missing parameter enrollment"})
    assert result.is_empty
    assert "embedding failed" in result.note
