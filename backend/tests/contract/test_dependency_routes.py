"""CF-V1-E8-03 through the API — the dependency picture Operations reads.

    "Show the dependency picture on the operations screens so a hold is
     self-explanatory."
    — CF-V1-E8-03

`tests/unit/test_scheduling_dependencies.py` proves the rulings and
`tests/pipeline/test_dependencies_on_the_real_plane.py` proves the runner
honours them. This proves the route hands a screen everything it needs to
explain a hold WITHOUT a second request: the blocking batch, the layer it
failed at, the chain that reached it, and the blast radius.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer
from cinqflow.core.scheduling import DEPENDS_ON_KEY
from cinqflow.ports.control_tables import BatchControl, StageStatus

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

ENGINEER = "dev-engineer@cinqcare.test"
CLAIMS = "centene-medicare-claims"
ENROLLMENT = "centene-medicare-enrollment"
PERIOD = "2026-08"
NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)

SEED = Actor(subject="seed@cinqcare.test", actor_type=ActorType.HUMAN)
APPROVER = Actor(subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _published(feed_id: str, *, depends_on: tuple[str, ...] = ()) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=SEED,
        created_ts=NOW,
        body={DEPENDS_ON_KEY: list(depends_on), "schedule_cron": "0 6 1 * *"},
        approved_by=APPROVER,
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    memory.save(_published(ENROLLMENT))
    memory.save(_published(CLAIMS, depends_on=(ENROLLMENT,)))
    return memory


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


@pytest.fixture
def client(store: MemMetadataDb, control: MemStoreControlTables) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


def _upstream(control: MemStoreControlTables, state: BatchState, *, layer: Layer | None = None):
    batch = BatchControl(
        batch_id="ENR-8842",
        feed_id=ENROLLMENT,
        feed_version=1,
        business_date=PERIOD,
        state=BatchState.RECEIVED,
        started_ts=NOW,
    )
    control.open_batch(batch)
    control.update_batch_state(batch.batch_id, state)
    if layer is not None:
        control.record_stage(
            StageStatus(batch_id=batch.batch_id, stage=layer, state=state, started_ts=NOW)
        )
    return batch


# ── the picture ──────────────────────────────────────────────────────────────
def test_a_healthy_upstream_leaves_the_feed_free_to_run(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _upstream(control, BatchState.COMPLETED)
    body = client.get(f"/api/feeds/{CLAIMS}/dependencies", headers=_as(ENGINEER)).json()
    assert body["decision"]["may_run"] is True
    assert body["decision"]["blockers"] == []


def test_a_hold_explains_itself_in_one_response(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """Everything a screen needs, without a second request: the reason, the
    blocking batch, the layer it failed at, and the chain."""
    _upstream(control, BatchState.FAILED, layer=Layer.SILVER_RAW)
    body = client.get(
        f"/api/feeds/{CLAIMS}/dependencies?business_date={PERIOD}", headers=_as(ENGINEER)
    ).json()

    assert body["is_self_explanatory"] is True
    decision = body["decision"]
    assert decision["may_run"] is False
    assert decision["batch_state"] == "BLOCKED"
    assert decision["status"] == "Needs Attention"

    blocker = decision["blockers"][0]
    assert blocker["reason"] == "upstream_failed"
    assert blocker["batch_id"] == "ENR-8842"
    assert blocker["layer"] == "silver_raw"
    assert blocker["chain"] == [CLAIMS, ENROLLMENT]
    assert "failed at silver_raw" in blocker["explanation"]


def test_the_picture_names_the_root_cause_node_and_the_edge(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _upstream(control, BatchState.FAILED)
    body = client.get(f"/api/feeds/{CLAIMS}/dependencies", headers=_as(ENGINEER)).json()

    causes = [node["feed_id"] for node in body["nodes"] if node["is_root_cause"]]
    subject = [node["feed_id"] for node in body["nodes"] if node["is_subject"]]
    assert causes == [ENROLLMENT]
    assert subject == [CLAIMS]
    assert [ENROLLMENT, CLAIMS] in body["edges"]


def test_the_blast_radius_is_shown_even_when_nothing_is_wrong(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """An engineer editing a feed's schedule needs to know what waits on it
    BEFORE they change it."""
    _upstream(control, BatchState.COMPLETED)
    body = client.get(f"/api/feeds/{ENROLLMENT}/dependencies", headers=_as(ENGINEER)).json()
    assert body["blast_radius"] == [CLAIMS]


def test_an_upstream_that_never_arrived_reads_as_missing(
    client: TestClient,
) -> None:
    body = client.get(
        f"/api/feeds/{CLAIMS}/dependencies?business_date={PERIOD}", headers=_as(ENGINEER)
    ).json()
    assert body["decision"]["status"] == "Missing"
    assert body["decision"]["blockers"][0]["reason"] == "upstream_not_arrived"


def test_a_draft_feeds_dependency_does_not_gate_production(
    client: TestClient, store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """A person must not be able to stop a live feed by saving a draft."""
    store.save(
        GovernedObject(
            object_type=ObjectType.FEED,
            object_id="somebody-else-draft",
            version=1,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=SEED,
            created_ts=NOW,
            body={DEPENDS_ON_KEY: [CLAIMS]},
        )
    )
    _upstream(control, BatchState.COMPLETED)
    body = client.get(f"/api/feeds/{CLAIMS}/dependencies", headers=_as(ENGINEER)).json()
    assert body["blast_radius"] == []
    assert body["decision"]["may_run"] is True


# ── the guardrail ────────────────────────────────────────────────────────────
def test_an_unauthenticated_caller_sees_nothing(client: TestClient) -> None:
    assert client.get(f"/api/feeds/{CLAIMS}/dependencies").status_code == 401
