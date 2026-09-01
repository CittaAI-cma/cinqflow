"""CF-V1-E16-05 — the chunk/verify/embed spine, wired, against the mocks.

    "PHI or member-level data (verified per chunk by Presidio before
     embedding) ... never embedded."
    "Scope filter runs FIRST, before any similarity computation."
    "Every factual claim carries a resolvable citation_id."
    — ADR-0007

Every test here runs against `PatternPhiScrub`, `ScriptedLlm` and
`ListVector` — fast, deterministic, no real Postgres. The chunk-boundary
logic these depend on (`core.knowledge`) has its own PURE suite in
`tests/unit/test_knowledge_chunking.py`; this file proves the WIRING: the
PHI gate actually refuses before a model is ever called, the embed call
actually reaches `VectorPort.index`, and a re-run does not double the store.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.core.citations import CitationKind
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.knowledge import KnowledgeSourceError
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, ErrorCategory, Layer
from cinqflow.core.operations.fingerprint import IncidentTransitionError, fingerprint_batch
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.ports.control_tables import ErrorRecord
from cinqflow.ports.llm import Embedding
from cinqflow.ports.vector import Chunk, ScoredChunk
from cinqflow.workers.knowledge import KnowledgeIngestWorker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "fidelis_roster"
STEWARD = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")
CALLER = Actor(subject="platform@cinqflow", actor_type=ActorType.SYSTEM, display_name="platform")

#: A synthetic SSN — the same shape `test_llm_gateway.py`'s own scrub-ordering
#: test uses. Never a real identifier; the pattern is what `PatternPhiScrub`
#: (and Presidio, per `adapters.local.presidio_scrub`'s own note on why it
#: masks this exact shape despite failing the Luhn-style plausibility check) is
#: built to catch.
PHI_SHAPED_TEXT = "Call the member at 123-45-6789 to confirm enrollment."


class SpyLlm(ScriptedLlm):
    """`ScriptedLlm` records `.complete()` calls in `.calls`, never `.embed()`
    ones — this adds that, so a refused-before-any-port-call test has
    something to assert zero of."""

    def __init__(self) -> None:
        super().__init__()
        self.embed_calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        self.embed_calls.append(texts)
        return super().embed(texts)


class SpyVector(ListVector):
    """Records every text handed to `index()`, so a refusal can be proven to
    have never reached it — not merely inferred from a final count."""

    def __init__(self) -> None:
        super().__init__()
        self.indexed_texts: list[str] = []

    def index(self, chunks: Sequence[Chunk], vectors: Sequence[tuple[float, ...]]) -> None:
        self.indexed_texts.extend(chunk.text for chunk in chunks)
        super().index(chunks, vectors)


def _harness(
    *, vector: ListVector | None = None, llm: ScriptedLlm | None = None
) -> tuple[KnowledgeIngestWorker, MemMetadataDb, ListVector, ScriptedLlm]:
    store = MemMetadataDb()
    raw_llm = llm or ScriptedLlm()
    gateway = LlmGateway(
        llm=raw_llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("1"), per_agent_per_day_usd=Decimal("10")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    vec = vector or ListVector()
    worker = KnowledgeIngestWorker(
        phi_scrub=PatternPhiScrub(), llm=gateway, vector=vec, metadata=store, clock=lambda: NOW
    )
    return worker, store, vec, raw_llm


def _closed_incident(resolution: str = "Re-ran validate_input; business_date restored."):
    error = ErrorRecord(
        error_id_hash="e1",
        batch_id="8842",
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message="required key 'business_date' absent",
        occurred_ts=NOW,
    )
    incident = fingerprint_batch(batch_id="8842", feed_id=FEED, errors=[error], now=NOW)
    return (
        incident.acknowledge(by=STEWARD.subject)
        .resolve(resolution=resolution, at=NOW + timedelta(minutes=18))
        .close()
    )


def _published_runbook(**body_overrides: object) -> GovernedObject:
    body: dict[str, object] = {
        "title": "Missing business_date",
        "signatures": ["fp-abc123"],
        "steps": ["Check the upstream XCom.", "Re-run validate_input.", "Confirm the row count."],
        "feed_id": FEED,
    }
    body.update(body_overrides)
    return GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body=body,
        approved_by=STEWARD,
        approved_ts=NOW,
    )


def _all_indexed(vector: ListVector) -> tuple[ScoredChunk, ...]:
    """Every chunk currently indexed, via the PORT'S OWN read verb — never the
    mock's private state — so this reads exactly as a real caller would."""
    return tuple(vector.retrieve((0.0,) * 8, limit=100, scope_filter={}))


# ── the happy paths ──────────────────────────────────────────────────────────


def test_a_clean_incident_narrative_embeds_with_a_real_citation() -> None:
    worker, _store, vector, _llm = _harness()
    incident = _closed_incident()

    result = worker.ingest_incident(incident, run_id="run-1", caller=CALLER)

    assert result.indexed == (incident.citation,)
    assert result.refused == ()
    assert vector.count() == 1

    (scored,) = _all_indexed(vector)
    assert scored.chunk.citation == incident.citation
    assert scored.chunk.citation.kind is CitationKind.BATCH
    assert scored.chunk.text == incident.narrative()
    assert scored.chunk.metadata["kind"] == "incident_narrative"
    assert scored.chunk.metadata["phi_verified_at"] == NOW.isoformat()
    assert scored.chunk.metadata["embedding_model_version"]
    assert scored.chunk.metadata["feed_id"] == FEED


def test_clean_runbook_steps_each_embed_with_their_own_step_citation() -> None:
    worker, _store, vector, _llm = _harness()
    runbook = _published_runbook()

    result = worker.ingest_runbook(runbook, run_id="run-2", caller=CALLER)

    assert len(result.indexed) == 3
    assert result.refused == ()
    assert vector.count() == 3
    assert sorted(c.fragment for c in result.indexed) == ["step-1", "step-2", "step-3"]

    by_fragment = {sc.chunk.citation.fragment: sc.chunk for sc in _all_indexed(vector)}
    assert by_fragment["step-2"].text == "Re-run validate_input."
    for chunk in by_fragment.values():
        assert chunk.citation.kind is CitationKind.RUNBOOK
        assert chunk.citation.subject == "RB-1"
        assert chunk.metadata["kind"] == "runbook_section"
        assert chunk.metadata["phi_verified_at"] == NOW.isoformat()
        assert chunk.metadata["object_version"] == "1"


# ── the PHI gate refuses, loudly, never masks-and-continues ──────────────────


def test_a_step_containing_phi_is_refused_and_never_reaches_the_vector_store() -> None:
    worker, store, vector, _llm = _harness(vector=SpyVector())
    runbook = _published_runbook(
        steps=["Check the upstream XCom.", PHI_SHAPED_TEXT, "Confirm the row count."]
    )

    result = worker.ingest_runbook(runbook, run_id="run-3", caller=CALLER)

    assert len(result.indexed) == 2, "the OTHER two steps still embed — one bad step ≠ a dead batch"
    (refused,) = result.refused
    assert refused.citation.fragment == "step-2"
    assert "US_SSN" in refused.entity_types
    assert "123-45-6789" not in refused.reason, "the VALUE never appears, only the entity type"
    assert vector.count() == 2
    assert PHI_SHAPED_TEXT not in vector.indexed_texts  # type: ignore[attr-defined]

    actions = store.read_agent_actions(run_id="run-3")
    (phi_event,) = [a for a in actions if a.outcome is ActionOutcome.REFUSED_PHI]
    assert phi_event.action == "knowledge:phi_verify"
    assert phi_event.actor.subject == CALLER.subject
    assert "123-45-6789" not in phi_event.detail, "the audit row must not leak the value either"


def test_a_dirty_incident_narrative_is_refused_wholesale_nothing_to_salvage() -> None:
    worker, store, vector, llm = _harness(vector=SpyVector(), llm=SpyLlm())
    incident = _closed_incident(resolution=f"Confirmed with the caregiver. {PHI_SHAPED_TEXT}")

    result = worker.ingest_incident(incident, run_id="run-4", caller=CALLER)

    assert result.indexed == ()
    assert len(result.refused) == 1
    assert vector.count() == 0
    assert vector.indexed_texts == []  # type: ignore[attr-defined]
    assert llm.embed_calls == [], "a refused chunk must never reach the embedding call at all"

    outcomes = [a.outcome for a in store.read_agent_actions(run_id="run-4")]
    assert outcomes == [ActionOutcome.REFUSED_PHI]


# ── idempotency: re-running never doubles the store ──────────────────────────


def test_re_running_the_pipeline_for_the_identical_runbook_version_does_not_double_index() -> None:
    worker, _store, vector, _llm = _harness()
    runbook = _published_runbook()

    first = worker.ingest_runbook(runbook, run_id="run-5a", caller=CALLER)
    second = worker.ingest_runbook(runbook, run_id="run-5b", caller=CALLER)

    assert vector.count() == 3, "the SAME three chunk ids upsert — they do not accumulate"
    assert set(first.indexed) == set(second.indexed)


def test_a_new_runbook_version_gets_its_own_chunks_the_old_ones_are_not_silently_replaced() -> None:
    worker, _store, vector, _llm = _harness()
    v1 = _published_runbook()
    worker.ingest_runbook(v1, run_id="run-6a", caller=CALLER)

    v2_steps = [*v1.body["steps"], "A fourth, newly added step."]
    v2 = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=2,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body={**v1.body, "steps": v2_steps},
        approved_by=STEWARD,
        approved_ts=NOW,
    )
    worker.ingest_runbook(v2, run_id="run-6b", caller=CALLER)

    assert vector.count() == 7, "3 chunks from v1 plus 4 from v2 — versions do not collide"


# ── CF-V1-W1-26 · atomic supersede: `supersedes=` retires the prior version ──


def test_a_new_runbook_version_atomically_supersedes_the_priors_chunks() -> None:
    """Write the acceptance test FIRST, as the slab's own brief asks: publish
    v1 (retrievable), publish v2 with `supersedes=v1` — v1's chunks are GONE,
    v2's are present, and the count never passes through a state with both OR
    neither. Proven here at the level this environment can actually prove it:
    ONE method call (`KnowledgeIngestWorker.ingest_runbook` ->
    `VectorPort.supersede`), so there is no second call between which an
    external reader could observe a half-done state — see
    `adapters.mock.vector.ListVector.supersede`'s own index-then-retire
    ordering for the mock's half of that guarantee, and `pg_vector
    .PgVectorStore.supersede`'s docstring for what the real adapter still
    needs (a caller-owned transaction) to make it literal on Postgres too.
    """
    worker, _store, vector, _llm = _harness()
    v1 = _published_runbook()
    worker.ingest_runbook(v1, run_id="run-9a", caller=CALLER)
    assert vector.count() == 3
    v1_ids = {sc.chunk.chunk_id for sc in _all_indexed(vector)}

    v2 = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=2,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body={**v1.body, "steps": [*v1.body["steps"], "A fourth, newly added step."]},
        approved_by=STEWARD,
        approved_ts=NOW,
    )
    result = worker.ingest_runbook(v2, run_id="run-9b", caller=CALLER, supersedes=v1)

    assert len(result.indexed) == 4
    assert vector.count() == 4, "v1's three chunks are GONE — never 7 (both), never 0 (neither)"
    v2_ids = {sc.chunk.chunk_id for sc in _all_indexed(vector)}
    assert v1_ids.isdisjoint(v2_ids), "v2's ids are freshly content-addressed, not v1's reused"
    for scored in _all_indexed(vector):
        assert scored.chunk.metadata["object_version"] == "2"


def test_a_supersede_where_every_new_step_is_refused_leaves_the_priors_chunks_alone() -> None:
    """Never neither: if the NEW version has nothing clean to embed, the OLD
    version's chunks are the only good answer that exists — retiring them
    too would turn a partial failure into total data loss."""
    worker, _store, vector, _llm = _harness()
    v1 = _published_runbook()
    worker.ingest_runbook(v1, run_id="run-10a", caller=CALLER)
    assert vector.count() == 3

    v2 = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=2,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body={**v1.body, "steps": [PHI_SHAPED_TEXT]},
        approved_by=STEWARD,
        approved_ts=NOW,
    )
    result = worker.ingest_runbook(v2, run_id="run-10b", caller=CALLER, supersedes=v1)

    assert result.indexed == ()
    assert len(result.refused) == 1
    assert vector.count() == 3, "v1's chunks are untouched — nothing clean arrived to replace them"


# ── scope_filter: required, and honoured on the write side ───────────────────


def test_feed_id_lands_on_the_chunk_so_a_later_retrieve_can_scope_by_it() -> None:
    worker, _store, vector, _llm = _harness()
    worker.ingest_runbook(_published_runbook(feed_id=FEED), run_id="run-7", caller=CALLER)

    scoped = vector.retrieve((0.0,) * 8, limit=10, scope_filter={"feed_id": FEED})
    assert len(scoped) == 3

    other_feed = vector.retrieve((0.0,) * 8, limit=10, scope_filter={"feed_id": "some_other_feed"})
    assert other_feed == ()


# ── whole-source refusals never touch a port at all ───────────────────────────


def test_an_open_incident_is_refused_before_any_port_is_touched() -> None:
    worker, _store, vector, llm = _harness(vector=SpyVector(), llm=SpyLlm())
    error = ErrorRecord(
        error_id_hash="e2",
        batch_id="9001",
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message="required key 'run_date' absent",
        occurred_ts=NOW,
    )
    open_incident = fingerprint_batch(batch_id="9001", feed_id=FEED, errors=[error], now=NOW)

    with pytest.raises(IncidentTransitionError):
        worker.ingest_incident(open_incident, run_id="run-8", caller=CALLER)

    assert vector.indexed_texts == []  # type: ignore[attr-defined]
    assert llm.embed_calls == []


def test_a_draft_runbook_is_refused_before_any_port_is_touched() -> None:
    worker, _store, vector, llm = _harness(vector=SpyVector(), llm=SpyLlm())
    draft = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-9",
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=STEWARD,
        created_ts=NOW,
        body={"steps": ["Do X."]},
    )

    with pytest.raises(KnowledgeSourceError, match="draft"):
        worker.ingest_runbook(draft, run_id="run-9", caller=CALLER)

    assert vector.indexed_texts == []  # type: ignore[attr-defined]
    assert llm.embed_calls == []


# ── a chunk's own citation is real and resolvable ─────────────────────────────


def test_every_indexed_citation_resolves_to_a_ui_route() -> None:
    worker, _store, _vector, _llm = _harness()
    incident = _closed_incident()
    runbook_result = worker.ingest_runbook(_published_runbook(), run_id="run-10", caller=CALLER)
    incident_result = worker.ingest_incident(incident, run_id="run-11", caller=CALLER)

    for citation in (*runbook_result.indexed, *incident_result.indexed):
        assert citation.route.startswith("/"), citation
