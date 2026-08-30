"""CF-V2-E12-01/02/03/04 through the API — the Control Room's routes.

The unit suites prove the semantics. This proves the routes cannot be talked
past them: that the board's counters carry their provenance, that a batch
answers in ONE response, that the action surface refuses exactly what it
declines to offer, and that fingerprinting calls no model.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
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
from cinqflow.ports.control_tables import BatchControl, ErrorRecord, StageStatus

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
