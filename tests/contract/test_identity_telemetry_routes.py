"""CF-V3-E9-04 through the API — daily identity accounting and coverage
telemetry's routes.

The unit suite (`tests/unit/test_identity_telemetry.py`,
`tests/unit/test_identity_worker.py`) proves the arithmetic and the worker
that persists it. This proves the routes serve exactly what was persisted,
in the right order, with the right regression flags — and that a source
nobody has fed yet answers empty rather than crashing.
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
from cinqflow.core.identity.telemetry import CoverageSnapshot, ParityCheckSummary
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

STEWARD = "dev-steward@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
NOW = datetime(2026, 8, 31, tzinfo=UTC)
AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _feed(feed_id: str, source_system: str) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=AUTHOR,
        created_ts=NOW,
        body={"source_system": source_system, "domain": "enrollments", "format": "xlsx"},
    )


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


@pytest.fixture
def metadata() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(control: MemStoreControlTables, metadata: MemMetadataDb) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=metadata, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


def test_telemetry_sources_lists_the_registrys_own_distinct_source_systems(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    metadata.save(_feed("fidelis-downstate-roster", "fidelis"))
    metadata.save(_feed("fidelis-claims", "fidelis"))
    metadata.save(_feed("optum-medicaid-enrollment", "optum"))

    body = client.get("/api/identity/telemetry/sources", headers=_as(STEWARD)).json()

    assert body == ["fidelis", "optum"]


def test_a_source_with_no_registered_feed_answers_an_empty_list(client: TestClient) -> None:
    assert client.get("/api/identity/telemetry/sources", headers=_as(STEWARD)).json() == []


def test_coverage_history_is_newest_first_and_carries_its_own_denominator(
    client: TestClient, control: MemStoreControlTables
) -> None:
    control.record_coverage_snapshot(
        CoverageSnapshot(
            source_system="fidelis",
            business_date="2026-08-29",
            total=1000,
            with_link_id=998,
            with_our_id=998,
            with_both=998,
        )
    )
    control.record_coverage_snapshot(
        CoverageSnapshot(
            source_system="fidelis",
            business_date="2026-08-30",
            total=1000,
            with_link_id=999,
            with_our_id=999,
            with_both=999,
        )
    )

    body = client.get("/api/identity/telemetry/coverage/fidelis", headers=_as(STEWARD)).json()

    assert [row["business_date"] for row in body] == ["2026-08-30", "2026-08-29"]
    assert body[0]["total"] == 1000
    assert body[0]["both_coverage_pct"] == "99.9"


def test_a_four_point_overnight_drop_is_flagged_on_the_wire(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """ "Given a source's match rate drops 4 points overnight, when the
    scorecard computes, then an alert names the source and the drop."""
    control.record_coverage_snapshot(
        CoverageSnapshot(
            source_system="optum",
            business_date="2026-08-30",
            total=1000,
            with_link_id=950,
            with_our_id=950,
            with_both=950,
        )
    )
    control.record_coverage_snapshot(
        CoverageSnapshot(
            source_system="optum",
            business_date="2026-08-31",
            total=1000,
            with_link_id=910,
            with_our_id=910,
            with_both=910,
        )
    )

    body = client.get("/api/identity/telemetry/coverage/optum", headers=_as(STEWARD)).json()

    latest, previous = body
    assert latest["is_regression"] is True
    assert latest["drop_points"] == "4.0"
    assert previous["is_regression"] is False


def test_coverage_history_for_an_unfed_source_is_empty_not_an_error(client: TestClient) -> None:
    response = client.get("/api/identity/telemetry/coverage/nobody-yet", headers=_as(STEWARD))
    assert response.json() == []


def test_parity_history_is_newest_first(client: TestClient, control: MemStoreControlTables) -> None:
    control.record_parity_check(
        ParityCheckSummary(
            source_system="fidelis",
            business_date="2026-08-30",
            checked=100,
            matched=98,
            mismatched=2,
        )
    )
    control.record_parity_check(
        ParityCheckSummary(
            source_system="fidelis",
            business_date="2026-08-31",
            checked=100,
            matched=100,
            mismatched=0,
        )
    )

    body = client.get("/api/identity/telemetry/parity/fidelis", headers=_as(STEWARD)).json()

    assert [row["business_date"] for row in body] == ["2026-08-31", "2026-08-30"]
    assert body[1]["match_rate_pct"] == "98.0"


def test_a_read_only_caller_may_view_the_scorecard(
    client: TestClient, control: MemStoreControlTables
) -> None:
    """This telemetry has no write action at all — VIEW is every permission
    it needs, for every role that holds it."""
    control.record_coverage_snapshot(
        CoverageSnapshot(
            source_system="fidelis",
            business_date="2026-08-31",
            total=10,
            with_link_id=10,
            with_our_id=10,
            with_both=10,
        )
    )
    assert client.get("/api/identity/telemetry/sources", headers=_as(READ_ONLY)).status_code == 200
    assert (
        client.get("/api/identity/telemetry/coverage/fidelis", headers=_as(READ_ONLY)).status_code
        == 200
    )
    assert (
        client.get("/api/identity/telemetry/parity/fidelis", headers=_as(READ_ONLY)).status_code
        == 200
    )
