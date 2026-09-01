"""CF-V2-E12-01/02/03/04 through the API — the Control Room's routes.

The unit suites prove the semantics. This proves the routes cannot be talked
past them: that the board's counters carry their provenance, that a batch
answers in ONE response, that the action surface refuses exactly what it
declines to offer, and that fingerprinting calls no model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.api import create_app
from cinqflow.core.agents.alert_enrichment.prompts import TEMPLATES as ALERT_ENRICHMENT_TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.identity import Principal as ToolPrincipal
from cinqflow.core.model.identity import Scopes
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, Layer
from cinqflow.core.operations.fingerprint import signature
from cinqflow.core.registry.operations import (
    DeliveryMethod,
    FeedOperations,
    Owner,
    OwnerRole,
    ServiceLevel,
)
from cinqflow.core.scheduling import DEPENDS_ON_KEY
from cinqflow.intelligence.agents.alert_enrichment import AlertEnrichmentAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.authn import Principal as AuthnPrincipal
from cinqflow.ports.authn import Role as AuthnRole
from cinqflow.ports.authn import Scopes as AuthnScopes
from cinqflow.ports.control_tables import BatchControl, ErrorRecord, StageStatus
from cinqflow.ports.control_tables import SlaCycle as SlaCycleRow
from cinqflow.workers.knowledge import KnowledgeIngestWorker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime.now(UTC)
TODAY = NOW.date().isoformat()

ENGINEER = "dev-engineer@cinqcare.test"
OPERATOR = "dev-operations@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
SEED = Actor(subject="seed@cinqcare.test", actor_type=ActorType.HUMAN)
APPROVER = Actor(subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN)

ENROLLMENT = "centene-medicare-enrollment"
CLAIMS = "centene-medicare-claims"
BATCH = "B-1244"

BH_AF_002_MESSAGE = (
    "evaluate_bronze_load: required key 'business_date' absent in XCom from upstream validate_input"
)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _envelope() -> dict[str, object]:
    return FeedOperations(
        source_id="centene",
        delivery_method=DeliveryMethod.SFTP,
        endpoint_ref="centene-sftp",
        owners=(
            Owner(role=OwnerRole.BUSINESS, subject="dana@cinqcare.test", display_name="Dana"),
            Owner(role=OwnerRole.TECHNICAL, subject="mei@cinqcare.test", display_name="Mei"),
        ),
        service_level=ServiceLevel(expected_by_local_time="06:00", timezone="America/New_York"),
    ).as_body()


def _feed(feed_id: str, *, depends_on: tuple[str, ...] = ()) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=SEED,
        created_ts=NOW,
        body={
            "domain": "Enrollment",
            "operations": _envelope(),
            DEPENDS_ON_KEY: list(depends_on),
        },
        approved_by=APPROVER,
        approved_ts=NOW,
    )


def _guide() -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="BH-AF-002",
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=SEED,
        created_ts=NOW,
        body={
            "title": "Missing mandatory task parameter",
            "signatures": [
                signature(
                    stage=Layer.BRONZE,
                    category=ErrorCategory.SYSTEM,
                    message=BH_AF_002_MESSAGE,
                )
            ],
            "steps": ["Re-run validate_input, then retry the batch."],
            "remedy": "retry",
            "is_transient": True,
            # CF-V1-W1-26 — the feed this guide's originating incident
            # belonged to, read by `workers.incidents._feed_retired` to
            # compute `stale` at match time. Present on every OTHER existing
            # test's read of this guide too; none of them retire ENROLLMENT,
            # so `stale` stays False for them exactly as before.
            "feed_id": ENROLLMENT,
        },
        approved_by=APPROVER,
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    memory.save(_feed(ENROLLMENT))
    memory.save(_feed(CLAIMS, depends_on=(ENROLLMENT,)))
    memory.save(_guide())
    return memory


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


@pytest.fixture
def client(store: MemMetadataDb, control: MemStoreControlTables) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def vector() -> ListVector:
    return ListVector()


def _knowledge_ingest_factory(
    vector: ListVector,
) -> Callable[[MemMetadataDb], KnowledgeIngestWorker]:
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

    return factory


def _broken_knowledge_ingest_factory(
    vector: ListVector,
) -> Callable[[MemMetadataDb], KnowledgeIngestWorker]:
    """CF-V1-W1-28's own repro: a budget too small for the embed call to ever
    clear `Budget.check` — the same "endpoint unreachable/budget ran out"
    shape the adversarial review actually reproduced, not a stand-in for it.
    `per_run_usd` sits below the gateway's own default `estimate_usd`
    (`$0.01`), so `LlmGateway.embed` raises `EmbeddingFailedError` before any
    text ever reaches `ScriptedLlm` — proving the route survives the
    DOCUMENTED failure mode, not merely a mocked one."""

    def factory(metadata: MemMetadataDb) -> KnowledgeIngestWorker:
        gateway = LlmGateway(
            llm=ScriptedLlm(),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=Budget(per_run_usd=Decimal("0.001"), per_agent_per_day_usd=Decimal("0.001")),
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

    return factory


@pytest.fixture
def client_with_knowledge(
    store: MemMetadataDb, control: MemStoreControlTables, vector: ListVector
) -> Iterator[TestClient]:
    """CF-V1-W1-26 — the SAME app, with the knowledge pin actually fitted, so
    the embed-on-close hook has somewhere to write. The plain `client` fixture
    above deliberately leaves `knowledge_ingest_factory=None`, proving the
    hook degrades to a no-op exactly like `ask` does with no LLM pin — this
    one proves what happens when the pin IS fitted."""
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        knowledge_ingest_factory=_knowledge_ingest_factory(vector),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_with_broken_knowledge(
    store: MemMetadataDb, control: MemStoreControlTables, vector: ListVector
) -> Iterator[TestClient]:
    """CF-V1-W1-28 — the knowledge pin is fitted but starved: every embed
    call it makes is refused before it reaches the transport. Proves closing
    an incident is unaffected by the SAME failure mode `client_with_knowledge`
    proves the happy path for."""
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        knowledge_ingest_factory=_broken_knowledge_ingest_factory(vector),
    )
    with TestClient(app) as test_client:
        yield test_client


def _open_batch(
    control: MemStoreControlTables,
    *,
    feed_id: str = ENROLLMENT,
    batch_id: str = BATCH,
    state: BatchState = BatchState.FAILED,
    started: datetime | None = None,
) -> BatchControl:
    batch = BatchControl(
        batch_id=batch_id,
        feed_id=feed_id,
        feed_version=1,
        business_date=TODAY,
        state=BatchState.RECEIVED,
        started_ts=started or NOW - timedelta(hours=2),
    )
    control.open_batch(batch)
    control.update_batch_state(batch_id, state)
    return control.get_batch(batch_id)


def _fail_at_bronze(control: MemStoreControlTables, batch_id: str = BATCH) -> None:
    control.record_stage(
        StageStatus(
            batch_id=batch_id,
            stage=Layer.LANDING,
            state=BatchState.COMPLETED,
            started_ts=NOW - timedelta(hours=2),
            completed_ts=NOW - timedelta(hours=2),
            records_in=10_000,
            records_out=10_000,
        )
    )
    control.record_stage(
        StageStatus(
            batch_id=batch_id,
            stage=Layer.BRONZE,
            state=BatchState.FAILED,
            started_ts=NOW - timedelta(hours=2),
            records_in=10_000,
            records_out=0,
            attributed_drops=10_000,
        )
    )
    for index, (digest, message, offset) in enumerate(
        [
            ("root", BH_AF_002_MESSAGE, 0),
            ("fallout-1", "downstream task failed", 2),
            ("fallout-2", "load_silver_raw skipped: upstream failed", 4),
        ]
    ):
        control.record_error(
            ErrorRecord(
                # The real hash is `hash(batch_id, stage, record_key, ...)`, so two
                # batches never share one. Reusing a digest here would have made
                # prior occurrences overwrite each other in the store.
                error_id_hash=f"{batch_id}-{digest}",
                batch_id=batch_id,
                stage=Layer.BRONZE,
                category=ErrorCategory.SYSTEM,
                message=message,
                occurred_ts=NOW - timedelta(hours=2) + timedelta(seconds=offset),
            )
        )
        _ = index


# ── CF-V2-E12-01 · the board ─────────────────────────────────────────────────
def test_the_board_answers_the_morning_question(client: TestClient) -> None:
    body = client.get("/api/operations/board", headers=_as(ENGINEER)).json()
    assert body["expected"] == 2
    assert body["received"] == 0
    assert set(body["domains"]) == {"Enrollment"}
    assert "expected" in body["explanation"]


def test_every_counter_on_the_wire_carries_its_provenance(client: TestClient) -> None:
    """ "No hand-maintained figures anywhere" — through the API too."""
    body = client.get("/api/operations/board", headers=_as(ENGINEER)).json()
    assert body["counters"]
    assert all(counter["derived_from"].strip() for counter in body["counters"])


def test_a_received_feed_moves_the_counters(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.COMPLETED)
    body = client.get("/api/operations/board", headers=_as(ENGINEER)).json()
    assert body["received"] == 1


def test_a_failed_batch_leads_the_attention_list_with_its_reasoning(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.FAILED)
    body = client.get("/api/operations/board", headers=_as(ENGINEER)).json()
    top = body["attention"][0]
    assert top["feed_id"] == ENROLLMENT
    assert "batch is failed" in top["why"]
    assert top["route"]


def test_the_board_filters_by_domain_and_recomputes(client: TestClient) -> None:
    body = client.get("/api/operations/board?domain=Enrollment", headers=_as(ENGINEER)).json()
    assert body["expected"] == 2
    empty = client.get("/api/operations/board?domain=Claims", headers=_as(ENGINEER)).json()
    assert empty["expected"] == 0


# ── CF-V2-E12-02 · the monitor ───────────────────────────────────────────────
def test_one_response_carries_stages_errors_and_the_cascade(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "Two clicks" means the second click has to find everything already
    here."""
    _open_batch(control)
    _fail_at_bronze(control)
    body = client.get(f"/api/operations/batches/{BATCH}", headers=_as(ENGINEER)).json()

    assert body["failed_at"] == "bronze"
    assert body["rows_written"] == 0
    assert len(body["stages"]) == 2
    assert len(body["errors"]) == 3
    assert sum(1 for e in body["errors"] if e["is_consequence"]) == 2
    assert "2 are consequences of the first" in body["cascade_summary"]
    assert body["flow"][0].startswith("landing: 10,000 in")


def test_an_unknown_batch_is_a_404(client: TestClient) -> None:
    assert client.get("/api/operations/batches/nope", headers=_as(ENGINEER)).status_code == 404


# ── CF-V2-E12-03 · the action surface ────────────────────────────────────────
def test_the_surface_offers_only_what_it_would_permit(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.FAILED)
    offered = client.get(f"/api/operations/batches/{BATCH}/actions", headers=_as(OPERATOR)).json()
    assert "retry" in offered["offered"]
    assert "resume" not in offered["offered"]
    assert all(preview["what_will_happen"] for preview in offered["previews"])


def test_the_surface_offers_nothing_to_a_role_that_cannot_act(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """CF-V2-E12-03 — both matrices, not one. The batch is in a retryable
    state, but the engineer builds and the analyst reads: neither may act, so
    neither is shown a button that would bounce."""
    _open_batch(control, state=BatchState.FAILED)
    for subject in (ENGINEER, READ_ONLY):
        offered = client.get(
            f"/api/operations/batches/{BATCH}/actions", headers=_as(subject)
        ).json()
        assert offered["offered"] == [], subject


def test_retry_is_not_offered_on_a_running_batch(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.IN_PROGRESS)
    offered = client.get(f"/api/operations/batches/{BATCH}/actions", headers=_as(OPERATOR)).json()
    assert "retry" not in offered["offered"]


def test_a_retry_comes_back_requested_and_not_complete(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """'retry requested' is not 'retry succeeded' — on the wire."""
    _open_batch(control, state=BatchState.FAILED)
    body = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Transient cluster error."},
        headers=_as(OPERATOR),
    )
    assert body.status_code == 200, body.text
    record = body.json()
    assert record["phase"] == "requested"
    assert record["is_complete"] is False
    assert "not yet verified" in record["explanation"]


def test_a_requested_action_survives_the_response_and_can_be_polled(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """CF-V2-E12-03 — the record is a LEDGER ROW, not a response body. The
    screen polls it by id until a worker verifies; until then it stays
    REQUESTED, never a tick."""
    _open_batch(control, state=BatchState.FAILED)
    posted = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Transient cluster error."},
        headers=_as(OPERATOR),
    ).json()
    assert posted["record_id"]

    polled = client.get(f"/api/operations/actions/{posted['record_id']}", headers=_as(READ_ONLY))
    assert polled.status_code == 200
    assert polled.json()["phase"] == "requested"
    assert polled.json()["record_id"] == posted["record_id"]

    assert client.get("/api/operations/actions/never-was", headers=_as(OPERATOR)).status_code == 404


def test_bookkeeping_verifies_on_the_wire_and_a_retry_does_not(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """CF-V2-E12-03 — the two-phase boundary, exact. An acknowledgement's only
    effect IS the row, so it comes back complete; a retry touches the engine,
    so it comes back REQUESTED and stays that way until a verifier re-reads
    the control tables."""
    _open_batch(control, state=BatchState.FAILED)
    acked = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "acknowledge"},
        headers=_as(OPERATOR),
    ).json()
    assert acked["phase"] == "verified"
    assert acked["is_complete"] is True
    assert "written down" in acked["outcome"]

    retried = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Transient cluster error."},
        headers=_as(OPERATOR),
    ).json()
    assert retried["phase"] == "requested"
    assert retried["is_complete"] is False


def test_a_refusal_lands_in_the_action_history_too(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "the system refuses ... and RECORDS the refusal" — findable on the
    batch's own history, beside the actions that ran."""
    _open_batch(control, state=BatchState.COMPLETED)
    refused = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Just in case."},
        headers=_as(OPERATOR),
    )
    assert refused.status_code == 409

    history = client.get(
        f"/api/operations/batches/{BATCH}/action-history", headers=_as(READ_ONLY)
    ).json()
    assert [entry["phase"] for entry in history] == ["refused"]
    assert history[0]["action"] == "retry"


def test_a_retry_without_a_reason_is_refused(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.FAILED)
    refused = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry"},
        headers=_as(OPERATOR),
    )
    assert refused.status_code == 409
    assert "say why" in refused.text


def test_a_wrong_state_retry_is_refused_and_leaves_a_row(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    _open_batch(control, state=BatchState.COMPLETED)
    refused = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Just in case."},
        headers=_as(OPERATOR),
    )
    assert refused.status_code == 409
    actions = [entry.action for entry in store.read_audit(object_id=ENROLLMENT)]
    assert "refused:retry" in actions


def test_a_backdate_request_carries_its_business_date_through(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """CF-V2-E8-04's own field, on the shared request body — a caller could
    name a business date without it silently being dropped before it ever
    reached `ops_actions.ActionRequest`."""
    _open_batch(control, state=BatchState.FAILED)
    body = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={
            "action": "backdate",
            "reason": "September roster corrected after the fact.",
            "business_date": "2026-09-01",
            "supersede_acknowledged": True,
        },
        headers=_as(OPERATOR),
    )
    assert body.status_code == 200, body.text
    assert body.json()["phase"] == "requested"


def test_a_free_form_action_is_refused_with_the_vocabulary_named(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "Offer free-form commands or raw SQL anywhere" — the route says no and
    says what it does offer."""
    _open_batch(control, state=BatchState.FAILED)
    refused = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "DROP TABLE members", "reason": "x"},
        headers=_as(OPERATOR),
    )
    assert refused.status_code == 400
    assert "nothing free-form" in refused.text


def test_retrying_a_paused_feed_is_refused_with_a_link(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    from cinqflow.core.registry.suspension import pause

    _open_batch(control, state=BatchState.FAILED)
    store.record_suspension(
        pause(
            ENROLLMENT,
            reason="mapping change pending",
            actor=Actor(subject="j.smith@cinqcare.test", actor_type=ActorType.HUMAN),
            now=NOW,
        )
    )
    refused = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Transient."},
        headers=_as(OPERATOR),
    )
    assert refused.status_code == 409
    assert "mapping change pending" in refused.text


# ── CF-V2-E12-04 · the incident's life, over the wire ────────────────────────
def _opened_incident(control: MemStoreControlTables, store: MemMetadataDb) -> str:
    """A failed batch whose incident the worker has written down — the state
    every lifecycle test starts from, produced the way production produces it."""
    from cinqflow.workers.incidents import IncidentWorker

    _open_batch(control, state=BatchState.FAILED)
    _fail_at_bronze(control)
    worker = IncidentWorker(control=control, metadata=store)
    return worker.on_batch_failed(BATCH, now=NOW).incident_id


def test_an_incident_lives_from_open_to_closed_over_the_api(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """Acknowledge -> resolve -> close, each a row, each visible on the batch's
    own incident view — and only a closed one is embeddable."""
    incident_id = _opened_incident(control, store)

    listed = client.get("/api/operations/incidents?state=open", headers=_as(READ_ONLY)).json()
    assert [row["incident_id"] for row in listed] == [incident_id]

    acked = client.post(
        f"/api/operations/incidents/{incident_id}/acknowledge",
        json={"assigned_to": "mei@cinqcare.test"},
        headers=_as(OPERATOR),
    )
    assert acked.status_code == 200, acked.text
    assert acked.json()["state"] == "acknowledged"
    assert acked.json()["acknowledged_by"] == OPERATOR
    assert acked.json()["assigned_to"] == "mei@cinqcare.test"

    resolved = client.post(
        f"/api/operations/incidents/{incident_id}/resolve",
        json={"resolution": "Re-ran validate_input, then retried the batch."},
        headers=_as(OPERATOR),
    )
    assert resolved.status_code == 200, resolved.text

    closed = client.post(f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR))
    assert closed.status_code == 200, closed.text
    assert closed.json()["state"] == "closed"

    # The batch's own incident view reads the same ledger.
    view = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(READ_ONLY)).json()
    assert view["state"] == "closed"
    assert view["resolution"] == "Re-ran validate_input, then retried the batch."
    # And the open list no longer carries it.
    assert client.get("/api/operations/incidents?state=open", headers=_as(READ_ONLY)).json() == []


def test_closing_an_unresolved_incident_is_refused_with_the_machines_words(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """ "only a closed one teaches" has a precondition: something was learned.
    The machine refuses OPEN -> CLOSED, and the wire carries its sentence."""
    incident_id = _opened_incident(control, store)
    refused = client.post(f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR))
    assert refused.status_code == 409
    assert "cannot go open -> closed" in refused.text


def test_incident_bookkeeping_needs_the_operations_role(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    incident_id = _opened_incident(control, store)
    for subject in (READ_ONLY, ENGINEER):
        denied = client.post(
            f"/api/operations/incidents/{incident_id}/acknowledge",
            json={},
            headers=_as(subject),
        )
        assert denied.status_code == 403, subject
    assert (
        client.post(
            "/api/operations/incidents/INC-never/acknowledge", json={}, headers=_as(OPERATOR)
        ).status_code
        == 404
    )


# ── CF-V1-W1-26 · TRIGGER 1 — closing an incident embeds its narrative ───────


def test_closing_an_incident_embeds_its_narrative_exactly_once(
    client_with_knowledge: TestClient,
    control: MemStoreControlTables,
    store: MemMetadataDb,
    vector: ListVector,
) -> None:
    """The pipeline's two real callers, half one: `Incident.close()`'s state
    transition is what reaches `KnowledgeIngestWorker.ingest_incident` — never
    acknowledge, never resolve, and never a second close."""
    incident_id = _opened_incident(control, store)

    client_with_knowledge.post(
        f"/api/operations/incidents/{incident_id}/acknowledge",
        json={"assigned_to": "mei@cinqcare.test"},
        headers=_as(OPERATOR),
    )
    assert vector.count() == 0, "acknowledged is not embeddable — the hook must not fire yet"

    client_with_knowledge.post(
        f"/api/operations/incidents/{incident_id}/resolve",
        json={"resolution": "Re-ran validate_input, then retried the batch."},
        headers=_as(OPERATOR),
    )
    assert vector.count() == 0, "resolved-but-unclosed is not embeddable either"

    closed = client_with_knowledge.post(
        f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR)
    )
    assert closed.status_code == 200, closed.text
    assert vector.count() == 1

    # Closing is a ONE-WAY transition: the state machine refuses a second
    # CLOSED->CLOSED move with a 409 before `moved` even exists, so the embed
    # hook is structurally unreachable on a retry — not merely un-triggered.
    second_close = client_with_knowledge.post(
        f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR)
    )
    assert second_close.status_code == 409
    assert vector.count() == 1, "a second close attempt cannot double-fire the embed"


def test_closing_an_incident_survives_an_embed_failure_and_records_it(
    client_with_broken_knowledge: TestClient,
    control: MemStoreControlTables,
    store: MemMetadataDb,
    vector: ListVector,
) -> None:
    """CF-V1-W1-28's own repro, reproduced as a permanent regression: the
    CLOSED transition (`metadata.record_incident_event`/`audit.record`) is
    ALREADY DURABLE before `ingest_incident` even runs, and `close()` is
    one-way — so an unhandled 500 here would leave an operator with a
    genuinely closed incident and no legal way to retry closing it. The
    route must return 200, the state must genuinely be CLOSED, the embed
    must genuinely not have happened, and the failure must be auditable."""
    incident_id = _opened_incident(control, store)
    client_with_broken_knowledge.post(
        f"/api/operations/incidents/{incident_id}/acknowledge", json={}, headers=_as(OPERATOR)
    )
    client_with_broken_knowledge.post(
        f"/api/operations/incidents/{incident_id}/resolve",
        json={"resolution": "Re-ran validate_input, then retried the batch."},
        headers=_as(OPERATOR),
    )

    closed = client_with_broken_knowledge.post(
        f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR)
    )

    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["state"] == "closed", "the transition genuinely landed"
    assert vector.count() == 0, "the embed genuinely did not happen"
    assert body["warnings"], "a steward must be told the narrative is not retrievable yet"
    assert "embed-on-close failed" in body["warnings"][0]

    # The state is durable on a fresh read too — not merely in this response.
    view = client_with_broken_knowledge.get(
        f"/api/operations/batches/{BATCH}/incident", headers=_as(READ_ONLY)
    ).json()
    assert view["state"] == "closed"

    audited = [
        entry
        for entry in store.read_audit(object_id=ENROLLMENT)
        if entry.action == "embed_failed:incident_close"
    ]
    assert len(audited) == 1, "the failure must be auditable, not merely swallowed"
    assert incident_id in audited[0].detail
    assert "embed-on-close failed" in audited[0].detail


def test_a_genuine_bug_in_the_incident_embed_path_still_surfaces(
    control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """The inverse of the regression above: W1-28 catches `EmbeddingFailedError`
    specifically, never a bare `Exception` — a real programming error inside
    the embed path (anything that is NOT the documented budget/transport
    degrade) must still be loud, or the fix would have traded an honest crash
    for a silent lie."""
    incident_id = _opened_incident(control, store)

    class _ExplodingWorker:
        def ingest_incident(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("not a budget problem, not a transport problem")

    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        knowledge_ingest_factory=lambda metadata: _ExplodingWorker(),  # type: ignore[arg-type,return-value]
    )
    with TestClient(app) as client:
        client.post(
            f"/api/operations/incidents/{incident_id}/acknowledge", json={}, headers=_as(OPERATOR)
        )
        client.post(
            f"/api/operations/incidents/{incident_id}/resolve",
            json={"resolution": "Re-ran validate_input, then retried the batch."},
            headers=_as(OPERATOR),
        )
        with pytest.raises(RuntimeError, match="not a budget problem"):
            client.post(f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR))


def test_a_deployment_with_no_knowledge_pin_still_closes_incidents(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """`client` (unlike `client_with_knowledge`) leaves
    `knowledge_ingest_factory=None` — the same "everything else works with no
    model" shape `ask` degrades to. Closing must still succeed."""
    incident_id = _opened_incident(control, store)
    client.post(
        f"/api/operations/incidents/{incident_id}/acknowledge", json={}, headers=_as(OPERATOR)
    )
    client.post(
        f"/api/operations/incidents/{incident_id}/resolve",
        json={"resolution": "Re-ran validate_input, then retried the batch."},
        headers=_as(OPERATOR),
    )
    closed = client.post(f"/api/operations/incidents/{incident_id}/close", headers=_as(OPERATOR))
    assert closed.status_code == 200, closed.text
    assert closed.json()["state"] == "closed"


def test_a_guide_whose_feed_has_retired_reads_stale_in_the_incident_that_cites_it(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """CF-V1-W1-26 — "a guide whose linked feed has retired is flagged in
    the very alert that cites it." Staleness is computed the moment the
    guide is READ, from the feed's CURRENT lifecycle — never stored on the
    runbook, and never requiring the runbook to be re-versioned over a fact
    about a different object entirely."""
    raw_guide_before = store.get(ObjectType.RUNBOOK, "BH-AF-002")

    _open_batch(control)
    _fail_at_bronze(control)
    fresh = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)).json()
    assert fresh["match"]["stale"] is False
    assert "retired" not in fresh["match"]["explanation"]

    # The feed retires — a SEPARATE governed object's lifecycle moving on,
    # touching nothing about the runbook itself.
    store.save(replace(_feed(ENROLLMENT), version=2, lifecycle_state=LifecycleState.RETIRED))

    retired_read = client.get(
        f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)
    ).json()
    assert retired_read["match"]["stale"] is True
    assert "feed has retired" in retired_read["match"]["explanation"]

    # The runbook's own stored body is byte-identical to before — nothing
    # about IT ever changed; only what a fresh read computes about the feed
    # linked to it did.
    assert store.get(ObjectType.RUNBOOK, "BH-AF-002") == raw_guide_before


def test_the_incident_matches_the_guide_and_shows_its_evidence(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control)
    _fail_at_bronze(control)
    body = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)).json()

    assert body["kind"] == "known"
    assert body["match"]["guide_id"] == "BH-AF-002"
    assert body["match"]["signature"] == body["signature"]
    assert body["match"]["matched_errors"]
    assert body["proposed_remedy"] == "retry"
    assert body["root_cause"]["message"].startswith("evaluate_bronze_load")
    assert len(body["consequences"]) == 2


def test_prior_occurrences_are_counted_from_the_control_tables(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "14 prior occurrences" has to be a query somebody can run, not a number
    in a spreadsheet."""
    for index in range(3):
        prior = f"B-prior-{index}"
        _open_batch(control, batch_id=prior, started=NOW - timedelta(days=index + 1))
        _fail_at_bronze(control, prior)
    _open_batch(control)
    _fail_at_bronze(control)

    body = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)).json()
    assert body["match"]["occurrences"] == 3
    assert len(body["match"]["priors"]) == 3


def test_a_novel_failure_says_so_and_proposes_nothing(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control)
    control.record_error(
        ErrorRecord(
            error_id_hash="novel",
            batch_id=BATCH,
            stage=Layer.IDENTITY,
            category=ErrorCategory.INTEGRATION,
            message="Verato returned an unexpected envelope version",
            occurred_ts=NOW,
        )
    )
    body = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)).json()
    assert body["kind"] == "novel"
    assert body["match"] is None
    assert body["proposed_remedy"] is None
    assert "matches nothing" in body["explanation"]


def test_a_draft_guide_is_never_offered_as_the_known_fix(
    client: TestClient, control: MemStoreControlTables, store: MemMetadataDb
) -> None:
    """Published runbooks only. A draft is one person's account of what worked
    once."""
    store.save(
        replace(
            _guide(),
            object_id="DRAFT-x",
            lifecycle_state=LifecycleState.DRAFT,
            approved_by=None,
            approved_ts=None,
        )
    )
    _open_batch(control)
    control.record_error(
        ErrorRecord(
            error_id_hash="root",
            batch_id=BATCH,
            stage=Layer.SILVER_ODS,
            category=ErrorCategory.SYSTEM,
            message="a failure only the draft guide covers",
            occurred_ts=NOW,
        )
    )
    body = client.get(f"/api/operations/batches/{BATCH}/incident", headers=_as(ENGINEER)).json()
    assert body["match"] is None


# ── production reads the profile, and is not guessed ─────────────────────────
def _production_profile() -> object:
    """Rung 4 — the client's own tenancy, where real member data can exist."""
    from cinqflow.core.model.profile import Mode, Profile

    return Profile(
        source="profiles/client-prod.yaml",
        rung=4.0,
        socket="client_tenant",
        mode=Mode.FULL,
        pins={},
    )


def test_production_is_read_from_the_profile_not_guessed(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """THE ONE THAT MATTERS FOR SAFETY.

    A lookup that silently returned None in production would disable the
    approval-identifier requirement precisely where it exists. So the profile
    is a wired dependency, and this fails if that stops being true.
    """
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        profile=_production_profile(),
    )
    with TestClient(app) as production:
        _open_batch(control, state=BatchState.FAILED)
        refused = production.post(
            f"/api/operations/batches/{BATCH}/actions",
            json={"action": "retry", "reason": "Transient cluster error."},
            headers=_as(OPERATOR),
        )
        assert refused.status_code == 409
        assert "approval identifier" in refused.text

        allowed = production.post(
            f"/api/operations/batches/{BATCH}/actions",
            json={
                "action": "retry",
                "reason": "Transient cluster error.",
                "approval_identifier": "CHG-88421",
            },
            headers=_as(OPERATOR),
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["approval_identifier"] == "CHG-88421"


def test_the_surface_says_which_environment_it_is_in(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        profile=_production_profile(),
    )
    with TestClient(app) as production:
        _open_batch(control, state=BatchState.FAILED)
        body = production.get(
            f"/api/operations/batches/{BATCH}/actions", headers=_as(OPERATOR)
        ).json()
        assert body["environment"] == "production"
        retry_preview = next(p for p in body["previews"] if p["action"] == "retry")
        assert retry_preview["requires_approval_identifier"] is True


# ── CF-V2-E13-03/04 · variance, waiver, and the verdict that derives ─────────
STEWARD = "dev-steward@cinqcare.test"


def _balanced_batch(control: MemStoreControlTables, batch_id: str = BATCH) -> None:
    from cinqflow.ports.control_tables import Reconciliation, RuleResult

    _open_batch(control, batch_id=batch_id, state=BatchState.COMPLETED)
    control.record_reconciliation(
        Reconciliation(
            batch_id=batch_id,
            stage=Layer.SILVER_RAW,
            records_in=200,
            records_out=200,
            quarantined=0,
            attributed_drops=0,
        )
    )
    control.record_rule_result(
        RuleResult(
            batch_id=batch_id,
            feed_id=ENROLLMENT,
            rule_id="DQ-002",
            evaluated=200,
            failed=0,
            excluded=0,
            recorded_ts=NOW,
        )
    )


def _open_count_variance(client: TestClient, *, expected: str, actual: str) -> dict:
    response = client.post(
        f"/api/operations/batches/{BATCH}/variances",
        json={"kind": "count", "expected": expected, "actual": actual, "tolerance": "10"},
        headers=_as(OPERATOR),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_certification_derives_and_there_is_no_route_that_sets_one(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "Derive certification mechanically from the checks — no manual 'mark
    as certified' button exists." The absence is asserted over the OpenAPI
    document, so a route added later cannot arrive unnoticed."""
    _balanced_batch(control)
    body = client.get(
        f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
    ).json()
    assert body["verdict"] == "Certified"
    assert body["publishable"] is True
    kinds = {c["kind"] for c in body["checks"]}
    assert {"balance", "reconciliation", "drop_ledger", "dq_rules", "schema_contract"} <= kinds

    spec = client.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        if "certification" in path:
            assert set(methods) <= {"get"}, f"{path} offers {set(methods)} — certification derives"


def test_a_batch_with_no_evidence_is_pending_not_failed(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.COMPLETED)
    body = client.get(
        f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
    ).json()
    assert body["verdict"] == "Pending"


def test_an_open_variance_blocks_and_a_waiver_certifies_with_the_waiver_named(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """Non-critical count variance: open blocks, waived certifies — as
    CERTIFIED-WITH-WAIVER, a distinct verdict, because a payer must see at a
    glance that something was ACCEPTED rather than passed."""
    _balanced_batch(control)
    opened = _open_count_variance(client, expected="200", actual="150")

    blocked = client.get(
        f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
    ).json()
    assert blocked["verdict"] == "Not Certified"

    waived = client.post(
        f"/api/variances/{opened['variance_id']}/waive",
        json={"reason": "Payer confirmed a one-off enrollment freeze for August."},
        headers=_as(STEWARD),
    )
    assert waived.status_code == 200, waived.text
    assert waived.json()["outcome"] == "waived"

    after = client.get(
        f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
    ).json()
    assert after["verdict"] == "Certified-with-Waiver"

    export = client.get(
        f"/api/operations/batches/{BATCH}/certification/export", headers=_as(OPERATOR)
    )
    assert export.status_code == 200
    assert "WAIVERS" in export.text
    assert "enrollment freeze" in export.text
    again = client.get(
        f"/api/operations/batches/{BATCH}/certification/export", headers=_as(OPERATOR)
    )
    assert again.text == export.text  # byte-identical on re-derivation


def test_a_critical_variance_cannot_be_waived_by_anyone(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """FINANCIAL is always critical: it is corrected or it blocks — the type
    refuses, the route carries the sentence, and the refusal is a row."""
    _balanced_batch(control)
    opened = client.post(
        f"/api/operations/batches/{BATCH}/variances",
        json={"kind": "financial", "expected": "148000", "actual": "100000", "tolerance": "0"},
        headers=_as(OPERATOR),
    ).json()
    assert opened["critical"] is True

    refused = client.post(
        f"/api/variances/{opened['variance_id']}/waive",
        json={"reason": "It is probably fine."},
        headers=_as(STEWARD),
    )
    assert refused.status_code == 409
    assert "cannot be waived" in refused.text

    # Corrected by the STEWARD who verified the reprocess — the operator
    # who opened it cannot also close it: the universal negative bites on
    # variances too.
    corrected = client.post(
        f"/api/variances/{opened['variance_id']}/correct",
        json={"note": "$48,000 recovered via reprocess of the July claims batch."},
        headers=_as(STEWARD),
    )
    assert corrected.status_code == 200
    after = client.get(
        f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
    ).json()
    assert after["verdict"] == "Certified"  # corrected, not forgiven


def test_only_a_steward_holds_the_waiver_pen(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _balanced_batch(control)
    opened = _open_count_variance(client, expected="200", actual="150")
    for subject in (OPERATOR, ENGINEER, READ_ONLY):
        denied = client.post(
            f"/api/variances/{opened['variance_id']}/waive",
            json={"reason": "A perfectly good reason."},
            headers=_as(subject),
        )
        assert denied.status_code == 403, subject


def test_the_export_is_an_act_and_needs_its_own_permission(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """The verdict is VIEW — everyone may know. The export is handing the
    evidence to a payer, which is an act with a name."""
    _balanced_batch(control)
    assert (
        client.get(
            f"/api/operations/batches/{BATCH}/certification", headers=_as(READ_ONLY)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/operations/batches/{BATCH}/certification/export", headers=_as(READ_ONLY)
        ).status_code
        == 403
    )


# ── CF-V2-E12-05 · the reliability score ─────────────────────────────────────
def test_the_score_decomposes_and_an_unmeasured_signal_is_not_a_zero(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "A score no one can decompose is a rumor" — and identity, unmeasured
    until Wave 3, must lower CONFIDENCE, never the score."""
    from cinqflow.ports.control_tables import RuleResult

    _open_batch(control, state=BatchState.COMPLETED)
    for minute, failed in enumerate((0, 0, 20)):
        control.record_rule_result(
            RuleResult(
                batch_id=BATCH,
                feed_id=ENROLLMENT,
                rule_id="DQ-002",
                evaluated=200,
                failed=failed,
                excluded=failed,
                recorded_ts=NOW + timedelta(minutes=minute),
            )
        )

    body = client.get(f"/api/feeds/{ENROLLMENT}/reliability", headers=_as(READ_ONLY)).json()
    assert body["feed_id"] == ENROLLMENT
    parts = {c["signal"]: c for c in body["components"]}
    assert set(parts) == {"dq", "sla", "reconciliation", "schema", "identity", "pipeline"}

    dq = parts["dq"]
    assert dq["measured"] is True
    assert dq["value"] == pytest.approx(100.0 * (600 - 20) / 600, abs=0.1)
    assert "evaluations failed" in dq["evidence"]

    identity = parts["identity"]
    assert identity["measured"] is False
    assert body["confidence"] < 1.0
    assert 0.0 < body["overall"] <= 100.0
    assert body["band"] in {"healthy", "at_risk", "critical"}


def test_a_feed_nobody_registered_has_no_score_to_leak(client: TestClient) -> None:
    assert client.get("/api/feeds/never-was/reliability", headers=_as(READ_ONLY)).status_code == 404


# ── CF-V2-E12-05 · alerts that explain themselves, over the wire ─────────────


def _alert_enrichment_agent(
    metadata: MemMetadataDb, control: MemStoreControlTables
) -> AlertEnrichmentAgent:
    for template in ALERT_ENRICHMENT_TEMPLATES:
        obj = template.as_governed(author=SEED)
        reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=SEED)
        approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=APPROVER)
        published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=APPROVER)
        metadata.save(published)
    gateway = LlmGateway(
        llm=ScriptedLlm(),
        phi_scrub=PatternPhiScrub(),
        metadata_db=metadata,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("1"), per_agent_per_day_usd=Decimal("10")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    tools = ToolContext(
        principal=ToolPrincipal(
            subject="platform@cinqflow",
            display_name="platform",
            scopes=Scopes(feeds=frozenset({"*"})),
        ),
        control=control,
        metadata=metadata,
        agent="alert-enrichment",
        now=NOW,
    )
    return AlertEnrichmentAgent(llm=gateway, tools=tools, runtime=InProcAgentRuntime())


def test_no_agent_configured_answers_service_unavailable(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    app = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as no_agent:
        response = no_agent.get("/api/operations/alerts", headers=_as(OPERATOR))
    assert response.status_code == 503
    assert "not enriched" in response.text


def test_a_breached_feed_comes_back_grouped_and_explained(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """The route reads the DETERMINISTIC alert set (`workers.sla.
    current_alerts`), groups it (`core.sla.grouped`) and enriches each
    group — never writes an `sla_alerts` row and never notifies, unlike the
    clock's own `sweep()`."""
    control.upsert_sla_instance(
        SlaCycleRow(
            feed_id=ENROLLMENT,
            cycle_date=NOW.date(),
            expected_ts=NOW - timedelta(hours=3),
            sla_status="Breached",
        )
    )
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        alert_enrichment_agent=_alert_enrichment_agent(store, control),
    )
    with TestClient(app) as with_agent:
        response = with_agent.get(
            f"/api/operations/alerts?on={NOW.date().isoformat()}", headers=_as(OPERATOR)
        )
    assert response.status_code == 200, response.text
    (group,) = response.json()
    assert group["feed_ids"] == [ENROLLMENT]
    assert group["severity"]
    assert group["facts"]
    assert group["cause"]


def test_alerts_are_scoped_to_what_the_caller_may_see(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """`current_alerts` is fleet-wide by construction (it reads every
    materialised cycle for the day) — the route, not `core.sla`, is what
    must not hand a caller a breach on a feed their scopes do not cover."""
    control.upsert_sla_instance(
        SlaCycleRow(
            feed_id=ENROLLMENT,
            cycle_date=NOW.date(),
            expected_ts=NOW - timedelta(hours=3),
            sla_status="Breached",
        )
    )
    scoped_out = AuthnPrincipal(
        subject="scoped-to-claims@cinqcare.test",
        display_name="Scoped",
        roles=frozenset({AuthnRole.OPERATIONS}),
        scopes=AuthnScopes(
            feeds=frozenset({CLAIMS}), domains=frozenset({"*"}), environments=frozenset({"dev"})
        ),
    )
    app = create_app(
        authn=StaticAuthn(users={scoped_out.subject: scoped_out}),
        metadata_db=store,
        control_tables=control,
        alert_enrichment_agent=_alert_enrichment_agent(store, control),
    )
    with TestClient(app) as with_agent:
        response = with_agent.get(
            f"/api/operations/alerts?on={NOW.date().isoformat()}",
            headers=_as(scoped_out.subject),
        )
    assert response.status_code == 200
    assert response.json() == []


# ── the guardrail ────────────────────────────────────────────────────────────
def test_a_read_only_user_may_watch_and_not_act(
    client: TestClient, control: MemStoreControlTables
) -> None:
    _open_batch(control, state=BatchState.FAILED)
    assert client.get("/api/operations/board", headers=_as(READ_ONLY)).status_code == 200
    assert client.get(f"/api/operations/batches/{BATCH}", headers=_as(READ_ONLY)).status_code == 200
    denied = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "x"},
        headers=_as(READ_ONLY),
    )
    assert denied.status_code == 403


def test_an_engineer_may_watch_but_no_longer_act(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """CF-V2-E12-03 — run/retry moved to the eighth role. The engineer keeps
    full visibility of the batch they built; acting on it now belongs to the
    operator, and the server refuses, not just the menu."""
    _open_batch(control, state=BatchState.FAILED)
    assert client.get(f"/api/operations/batches/{BATCH}", headers=_as(ENGINEER)).status_code == 200
    denied = client.post(
        f"/api/operations/batches/{BATCH}/actions",
        json={"action": "retry", "reason": "Transient cluster error."},
        headers=_as(ENGINEER),
    )
    assert denied.status_code == 403


def test_an_unauthenticated_caller_sees_nothing(client: TestClient) -> None:
    for path in (
        "/api/operations/board",
        f"/api/operations/batches/{BATCH}",
        f"/api/operations/batches/{BATCH}/actions",
        f"/api/operations/batches/{BATCH}/incident",
    ):
        assert client.get(path).status_code == 401
