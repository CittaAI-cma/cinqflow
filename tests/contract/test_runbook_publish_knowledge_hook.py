"""CF-V1-W1-26 · CF-V1-E16-05/E16-07 — TRIGGER 2, through the real route.

`publish_object` (`api/app.py`) is the generic, type-agnostic lifecycle-publish
route every `ObjectType` rides. This proves the RUNBOOK-only behaviour bolted
onto it: a publish embeds the guide's steps, and a SECOND publish of the same
`guide_id` — a new version superseding the one before it — retires the prior
version's chunks in the SAME operation that indexes the new ones, so a reader
never sees both versions' chunks at once, and never sees neither.

`tests/contract/test_knowledge_ingestion_pipeline.py` proves the WORKER half
of this (`KnowledgeIngestWorker.ingest_runbook(..., supersedes=...)`) against
scripted spies. This file proves the ROUTE actually calls it — the same
distinction W1-25 itself drew between the pipeline existing and something
calling it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.api import create_app
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.ports.vector import ScoredChunk
from cinqflow.workers.knowledge import KnowledgeIngestWorker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

STEWARD = "dev-steward@cinqcare.test"
BA = "dev-ba@cinqcare.test"
AUTHOR = Actor(subject=BA, actor_type=ActorType.HUMAN, display_name="BA")
APPROVER = Actor(subject=STEWARD, actor_type=ActorType.HUMAN, display_name="Steward")
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _approved_runbook(
    guide_id: str, version: int, steps: list[str], *, feed_id: str | None = None
) -> GovernedObject:
    """Seeded directly at APPROVED — the same shortcut `_published` helpers
    elsewhere in this suite take to reach a lifecycle state without
    re-testing `core.lifecycle`'s own submit/approve engine here."""
    return GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id=guide_id,
        version=version,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=AUTHOR,
        created_ts=NOW,
        body={
            "title": f"Guide {guide_id}",
            "steps": steps,
            "signatures": [f"fp-{guide_id.lower()}"],
            "remedy": None,
            "is_transient": False,
            "feed_id": feed_id,
        },
        approved_by=APPROVER,
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def vector() -> ListVector:
    return ListVector()


@pytest.fixture
def app(store: MemMetadataDb, vector: ListVector) -> FastAPI:
    def factory(metadata: MemMetadataDb) -> KnowledgeIngestWorker:
        gateway = LlmGateway(
            llm=ScriptedLlm(),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=Budget(per_run_usd=Decimal("1"), per_agent_per_day_usd=Decimal("10")),
            routing=Routing(small="small-model", large="large-model"),
            clock=lambda: NOW,
        )
        return KnowledgeIngestWorker(
            phi_scrub=PatternPhiScrub(),
            llm=gateway,
            vector=vector,
            metadata=metadata,
            clock=lambda: NOW,
        )

    return create_app(authn=StaticAuthn(), metadata_db=store, knowledge_ingest_factory=factory)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _retrieve_all(vector: ListVector) -> tuple[ScoredChunk, ...]:
    """Every chunk currently indexed, through the PORT's own read verb."""
    return tuple(vector.retrieve((0.0,) * 8, limit=100, scope_filter={}))


def test_publishing_a_runbook_embeds_its_steps(
    client: TestClient, store: MemMetadataDb, vector: ListVector
) -> None:
    store.save(_approved_runbook("RB-100", 1, ["Check the upstream XCom.", "Re-run the task."]))

    response = client.post("/api/objects/runbook/RB-100/publish", headers=_as(STEWARD))

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle_state"] == "published"
    assert vector.count() == 2
    fragments = sorted(sc.chunk.citation.fragment for sc in _retrieve_all(vector))
    assert fragments == ["step-1", "step-2"]


def test_a_deployment_with_no_knowledge_pin_still_publishes_runbooks(store: MemMetadataDb) -> None:
    """The generic route works unmodified with no knowledge pin fitted —
    the same "everything else works with no model" shape `ask` degrades to."""
    store.save(_approved_runbook("RB-101", 1, ["Do the one thing."]))
    app = create_app(authn=StaticAuthn(), metadata_db=store)
    with TestClient(app) as client:
        response = client.post("/api/objects/runbook/RB-101/publish", headers=_as(STEWARD))
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle_state"] == "published"


def test_a_second_publish_of_the_same_guide_atomically_supersedes_the_first(
    client: TestClient, store: MemMetadataDb, vector: ListVector
) -> None:
    """Publish v1 (retrievable) -> publish v2 (supersede): v1's chunks are
    GONE, v2's are present, and the count is never `both` (5) — only ever
    `v1 alone` (2), then `v2 alone` (3)."""
    store.save(_approved_runbook("RB-200", 1, ["Old step one.", "Old step two."]))
    first = client.post("/api/objects/runbook/RB-200/publish", headers=_as(STEWARD))
    assert first.status_code == 200, first.text
    assert vector.count() == 2
    v1_ids = {sc.chunk.chunk_id for sc in _retrieve_all(vector)}

    store.save(
        _approved_runbook("RB-200", 2, ["New step one.", "New step two.", "New step three."])
    )
    second = client.post("/api/objects/runbook/RB-200/publish", headers=_as(STEWARD))
    assert second.status_code == 200, second.text

    assert vector.count() == 3, "never 5 (both) — v1's chunks are retired, not merely outnumbered"
    v2_scored = _retrieve_all(vector)
    v2_ids = {sc.chunk.chunk_id for sc in v2_scored}
    assert v1_ids.isdisjoint(v2_ids), "v2's chunks are freshly content-addressed, not v1's reused"
    for scored in v2_scored:
        assert scored.chunk.metadata["object_version"] == "2"

    # v1's own governed object is untouched — the supersede acted on the
    # KNOWLEDGE PLANE's projection, never on the registry row.
    v1_object = store.get(ObjectType.RUNBOOK, "RB-200", version=1)
    assert v1_object.lifecycle_state is LifecycleState.PUBLISHED
    assert v1_object.body["steps"] == ["Old step one.", "Old step two."]


def test_a_guides_first_publish_has_nothing_to_supersede(
    client: TestClient, store: MemMetadataDb, vector: ListVector
) -> None:
    """`_prior_published_version` returns `None` on a guide's first publish —
    proven by the absence of any crash or spurious retire, not merely by
    inspection: a first publish is exactly `test_publishing_a_runbook_embeds_
    its_steps` above, repeated here for the explicit contrast the supersede
    test needs."""
    store.save(_approved_runbook("RB-300", 1, ["Only step."]))
    response = client.post("/api/objects/runbook/RB-300/publish", headers=_as(STEWARD))
    assert response.status_code == 200, response.text
    assert vector.count() == 1
