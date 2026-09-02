"""`poll_deliveries` — the caller `DeliveryWorker.deliver_available` never had.

Built entirely from mock ports: a `ScriptedConnector` stands in for a real
SFTP source (`test_sftp_connector.py` already proves the real one speaks the
real protocol), and the question this file answers is different — does a
Published feed's connector get FOUND and POLLED at all, and does an
unreachable or unrouted feed get skipped rather than blowing up the pass.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.registry.operations import FeedOperations
from cinqflow.workers.delivery_poll import poll_deliveries

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
AUTHOR = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
APPROVER = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steward")

FEED = FeedRecord(
    feed_id="fidelis-downstate-roster",
    domain="enrollments",
    source_system="fidelis",
    file_format="csv",
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.csv",
    schedule_cron="0 3 1 * *",
    sample_filename="_CINQDOWNSTATE_Member_Roster_202608.csv",
)


def _published(feed: FeedRecord, *, endpoint_ref: str = "") -> object:
    draft = feed.as_governed(author=AUTHOR, created_ts=NOW)
    if endpoint_ref:
        draft = replace(
            draft, body={**draft.body, "operations": FeedOperations(endpoint_ref=endpoint_ref).as_body()}
        )
    return replace(draft, lifecycle_state=LifecycleState.PUBLISHED, approved_by=APPROVER, approved_ts=NOW)


def test_a_published_feed_with_a_resolvable_route_is_polled_and_delivered() -> None:
    metadata = MemMetadataDb()
    metadata.save(_published(FEED, endpoint_ref="fidelis-sftp"))
    storage = MemFsStorage()
    connector = ScriptedConnector(storage, source="fidelis-sftp")
    connector.offer("_CINQDOWNSTATE_Member_Roster_202609.csv", b"MemberID\nMBR1\n", modified_ts=NOW)

    outcomes = poll_deliveries(
        metadata, storage, MemStoreControlTables(), {"fidelis-sftp": connector}, business_date="2026-09-01"
    )

    assert len(outcomes) == 1
    assert outcomes[0].feed_id == "fidelis-downstate-roster"
    assert len(outcomes[0].delivered) == 1
    assert outcomes[0].error is None


def test_a_feed_with_no_endpoint_ref_is_skipped_not_an_error() -> None:
    metadata = MemMetadataDb()
    metadata.save(_published(FEED))  # no operations at all
    storage = MemFsStorage()

    outcomes = poll_deliveries(metadata, storage, MemStoreControlTables(), {}, business_date="2026-09-01")

    assert outcomes == ()


def test_a_feed_whose_route_is_not_fitted_is_skipped_not_an_error() -> None:
    metadata = MemMetadataDb()
    metadata.save(_published(FEED, endpoint_ref="fidelis-sftp"))
    storage = MemFsStorage()

    # `connectors` names no "fidelis-sftp" route at all — same as a deployment
    # whose profile fits nothing for it.
    outcomes = poll_deliveries(metadata, storage, MemStoreControlTables(), {}, business_date="2026-09-01")

    assert outcomes == ()


def test_a_draft_feed_is_never_polled() -> None:
    metadata = MemMetadataDb()
    metadata.save(FEED.as_governed(author=AUTHOR, created_ts=NOW))  # stays Draft
    storage = MemFsStorage()
    connector = ScriptedConnector(storage, source="fidelis-sftp")
    connector.offer("_CINQDOWNSTATE_Member_Roster_202609.csv", b"data", modified_ts=NOW)

    outcomes = poll_deliveries(
        metadata, storage, MemStoreControlTables(), {"fidelis-sftp": connector}, business_date="2026-09-01"
    )

    assert outcomes == ()


def test_an_unreachable_connector_is_reported_but_does_not_raise() -> None:
    metadata = MemMetadataDb()
    metadata.save(_published(FEED, endpoint_ref="fidelis-sftp"))
    storage = MemFsStorage()
    connector = ScriptedConnector(storage, source="fidelis-sftp", reachable=False)

    outcomes = poll_deliveries(
        metadata, storage, MemStoreControlTables(), {"fidelis-sftp": connector}, business_date="2026-09-01"
    )

    assert len(outcomes) == 1
    assert outcomes[0].feed_id == "fidelis-downstate-roster"
    assert outcomes[0].delivered == ()
    assert outcomes[0].error


def test_nothing_offered_reports_nothing_not_an_empty_error() -> None:
    metadata = MemMetadataDb()
    metadata.save(_published(FEED, endpoint_ref="fidelis-sftp"))
    storage = MemFsStorage()
    connector = ScriptedConnector(storage, source="fidelis-sftp")  # nothing offered

    outcomes = poll_deliveries(
        metadata, storage, MemStoreControlTables(), {"fidelis-sftp": connector}, business_date="2026-09-01"
    )

    assert outcomes == ()
