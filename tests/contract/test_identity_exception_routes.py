"""CF-V3-E9-02 through the API — the identity exception queue's routes.

The unit suite (`tests/unit/test_identity_exceptions.py`) proves the fold and
the state machine. This proves the routes cannot be talked past them: that
the queue lists oldest-first, that assign/resolve go through the real core
functions and land in the real ledger, that an unknown key is a 404 not a
crash, and that a Read-Only caller is refused before anything is revealed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.identity import MatchOutcome
from cinqflow.core.identity.exceptions import (
    ExceptionEventAction,
    IdentityExceptionEvent,
    exception_key,
)

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

STEWARD = "dev-steward@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
NOW = datetime(2026, 8, 31, 3, 14, tzinfo=UTC)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.fixture
def metadata() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(metadata: MemMetadataDb) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as test_client:
        yield test_client


def _occurrence_event(
    *, source_member_id: str = "M-1", batch_id: str = "B-1", occurred_ts: datetime = NOW
) -> IdentityExceptionEvent:
    return IdentityExceptionEvent(
        event_id=f"EVT-{source_member_id}-{batch_id}",
        exception_key=exception_key("fidelis", source_member_id),
        action=ExceptionEventAction.OCCURRENCE,
        source_system="fidelis",
        source_member_id=source_member_id,
        occurred_ts=occurred_ts,
        batch_id=batch_id,
        outcome=MatchOutcome.UNRESOLVED,
    )


def test_the_queue_lists_oldest_opened_first(client: TestClient, metadata: MemMetadataDb) -> None:
    metadata.record_identity_exception_event(
        _occurrence_event(source_member_id="M-newer", occurred_ts=NOW + timedelta(days=1))
    )
    metadata.record_identity_exception_event(
        _occurrence_event(source_member_id="M-older", occurred_ts=NOW)
    )

    body = client.get("/api/identity/exceptions", headers=_as(STEWARD)).json()

    assert [row["source_member_id"] for row in body] == ["M-older", "M-newer"]


def test_a_second_occurrence_is_one_item_with_two_occurrences_on_the_wire(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event(batch_id="B-1"))
    metadata.record_identity_exception_event(
        _occurrence_event(batch_id="B-2", occurred_ts=NOW + timedelta(days=1))
    )

    body = client.get("/api/identity/exceptions", headers=_as(STEWARD)).json()

    assert len(body) == 1
    assert body[0]["occurrence_count"] == 2
    assert {o["batch_id"] for o in body[0]["occurrences"]} == {"B-1", "B-2"}


def test_the_queue_filters_by_source_system_and_state(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event(source_member_id="M-fidelis"))
    metadata.record_identity_exception_event(
        IdentityExceptionEvent(
            event_id="EVT-optum",
            exception_key=exception_key("optum", "M-optum"),
            action=ExceptionEventAction.OCCURRENCE,
            source_system="optum",
            source_member_id="M-optum",
            occurred_ts=NOW,
            batch_id="B-9",
            outcome=MatchOutcome.UNRESOLVED,
        )
    )

    only_optum = client.get(
        "/api/identity/exceptions?source_system=optum", headers=_as(STEWARD)
    ).json()
    assert [row["source_member_id"] for row in only_optum] == ["M-optum"]

    only_open = client.get("/api/identity/exceptions?state=open", headers=_as(STEWARD)).json()
    assert len(only_open) == 2


def test_an_unknown_state_filter_is_a_400_not_a_silent_empty_list(client: TestClient) -> None:
    response = client.get("/api/identity/exceptions?state=nonsense", headers=_as(STEWARD))
    assert response.status_code == 400


def test_a_single_exception_is_fetchable_by_its_key(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event())
    key = exception_key("fidelis", "M-1")

    body = client.get(f"/api/identity/exceptions/{key}", headers=_as(STEWARD)).json()

    assert body["key"] == key
    assert body["state"] == "open"


def test_an_unknown_key_is_a_404(client: TestClient) -> None:
    response = client.get(
        f"/api/identity/exceptions/{exception_key('fidelis', 'nobody')}", headers=_as(STEWARD)
    )
    assert response.status_code == 404


def test_assigning_moves_the_state_and_names_the_assignee(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event())
    key = exception_key("fidelis", "M-1")

    response = client.post(
        f"/api/identity/exceptions/{key}/assign",
        json={"assigned_to": "enrollment-steward@cinqcare.test"},
        headers=_as(STEWARD),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "assigned"
    assert body["assigned_to"] == "enrollment-steward@cinqcare.test"
    # Persisted, not just echoed — a fresh read shows the same state.
    refetched = client.get(f"/api/identity/exceptions/{key}", headers=_as(STEWARD)).json()
    assert refetched["state"] == "assigned"


def test_assigning_an_empty_name_is_refused_before_it_reaches_core(client: TestClient) -> None:
    response = client.post(
        f"/api/identity/exceptions/{exception_key('fidelis', 'M-1')}/assign",
        json={"assigned_to": ""},
        headers=_as(STEWARD),
    )
    assert response.status_code == 422


def test_resolving_a_resolved_exception_a_second_time_still_succeeds(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    """`resolve()` has no refusal path — resolving twice is idempotent on the
    STATE (still RESOLVED), even though the ledger gains a second event."""
    metadata.record_identity_exception_event(_occurrence_event())
    key = exception_key("fidelis", "M-1")

    first = client.post(
        f"/api/identity/exceptions/{key}/resolve",
        json={"note": "fixed upstream"},
        headers=_as(STEWARD),
    )
    second = client.post(f"/api/identity/exceptions/{key}/resolve", json={}, headers=_as(STEWARD))

    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == second.json()["state"] == "resolved"


def test_assigning_an_unknown_exception_is_a_404_not_a_bug(client: TestClient) -> None:
    response = client.post(
        f"/api/identity/exceptions/{exception_key('fidelis', 'ghost')}/assign",
        json={"assigned_to": "someone@cinqcare.test"},
        headers=_as(STEWARD),
    )
    assert response.status_code == 404


def test_queue_health_is_reported_per_source_never_rolled_up(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event(source_member_id="M-fidelis-1"))
    metadata.record_identity_exception_event(
        IdentityExceptionEvent(
            event_id="EVT-optum-health",
            exception_key=exception_key("optum", "M-optum-1"),
            action=ExceptionEventAction.OCCURRENCE,
            source_system="optum",
            source_member_id="M-optum-1",
            occurred_ts=NOW,
            batch_id="B-9",
            outcome=MatchOutcome.UNRESOLVED,
        )
    )

    health = client.get("/api/identity/exceptions/health", headers=_as(STEWARD)).json()

    by_source = {row["source_system"]: row for row in health}
    assert by_source["fidelis"]["open_count"] == 1
    assert by_source["optum"]["open_count"] == 1


def test_a_read_only_caller_may_view_but_not_change(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.record_identity_exception_event(_occurrence_event())
    key = exception_key("fidelis", "M-1")

    assert client.get("/api/identity/exceptions", headers=_as(READ_ONLY)).status_code == 200
    assert client.get(f"/api/identity/exceptions/{key}", headers=_as(READ_ONLY)).status_code == 200

    assign_attempt = client.post(
        f"/api/identity/exceptions/{key}/assign",
        json={"assigned_to": "someone@cinqcare.test"},
        headers=_as(READ_ONLY),
    )
    assert assign_attempt.status_code == 403

    resolve_attempt = client.post(
        f"/api/identity/exceptions/{key}/resolve", json={}, headers=_as(READ_ONLY)
    )
    assert resolve_attempt.status_code == 403

    # Refused, and nothing changed underneath the refusal.
    still_open = client.get(f"/api/identity/exceptions/{key}", headers=_as(STEWARD)).json()
    assert still_open["state"] == "open"


def test_the_refusal_itself_is_logged(client: TestClient, metadata: MemMetadataDb) -> None:
    metadata.record_identity_exception_event(_occurrence_event())
    key = exception_key("fidelis", "M-1")
    client.post(
        f"/api/identity/exceptions/{key}/assign",
        json={"assigned_to": "someone@cinqcare.test"},
        headers=_as(READ_ONLY),
    )
    denials = [e for e in metadata.read_audit() if e.action == "denied:assign"]
    assert denials, "a refused attempt on this queue must be logged, not silent"
