"""`seed-fidelis-feeds` — Milestone 2, Part G, against mock ports only.

Exercises `_publish_golden_roster`/`_through_the_lifecycle` directly, in
memory: no Postgres, no risk to the populated dev plane. `installer.cli`'s
own module docstring says the demo IS the test run, and a "does this write
real rows" question about a seeder is exactly the one this file answers
before anyone points it at a real database.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.orchestration import InProcOrchestration
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry import golden_fidelis
from cinqflow.core.registry.fidelis_claims import all_claims_feeds
from cinqflow.core.registry.operations import FeedOperations
from cinqflow.installer.cli import _publish_golden_roster

pytestmark = pytest.mark.unit

APPROVER = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steward")


def test_all_26_claims_feeds_are_distinct_and_construct() -> None:
    feeds = all_claims_feeds()
    assert len(feeds) == 26
    assert len({f.feed_id for f in feeds}) == 26


def test_publishing_the_golden_roster_sets_its_endpoint_ref() -> None:
    store = MemMetadataDb()
    orchestration = InProcOrchestration()

    published = _publish_golden_roster(store, orchestration, approver=APPROVER)

    assert published is True
    feed = store.get(ObjectType.FEED, golden_fidelis.FEED.feed_id)
    assert feed.lifecycle_state is LifecycleState.PUBLISHED
    endpoint_ref = FeedOperations.from_body(feed.body.get("operations")).endpoint_ref
    assert endpoint_ref == "fidelis-sftp"


def test_publishing_the_golden_roster_also_publishes_its_contract_and_rules() -> None:
    store = MemMetadataDb()
    _publish_golden_roster(store, InProcOrchestration(), approver=APPROVER)

    contract = store.get(ObjectType.CONTRACT, golden_fidelis.FEED.feed_id)
    assert contract.lifecycle_state is LifecycleState.PUBLISHED
    rules = store.get(ObjectType.DQ_RULE, golden_fidelis.FEED.feed_id)
    assert rules.lifecycle_state is LifecycleState.PUBLISHED


def test_publishing_the_golden_roster_registers_its_schedule() -> None:
    store = MemMetadataDb()
    orchestration = InProcOrchestration()

    _publish_golden_roster(store, orchestration, approver=APPROVER)

    due = orchestration.due(datetime.now(UTC))
    assert any(run.feed_id == golden_fidelis.FEED.feed_id for run in due)


def test_publishing_twice_is_a_no_op_the_second_time() -> None:
    store = MemMetadataDb()
    orchestration = InProcOrchestration()

    first = _publish_golden_roster(store, orchestration, approver=APPROVER)
    second = _publish_golden_roster(store, orchestration, approver=APPROVER)

    assert first is True
    assert second is False
    # Still exactly one Published version — a re-run must not fork a second one.
    assert store.get(ObjectType.FEED, golden_fidelis.FEED.feed_id).version == golden_fidelis.FEED_VERSION
