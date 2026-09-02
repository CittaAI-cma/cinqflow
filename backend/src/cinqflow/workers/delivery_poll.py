"""Milestone 2, Part H — the caller `DeliveryWorker.deliver_available` never had.

`workers.delivery`'s poll loop has been built and contract-tested since
CF-V1-E3-05: list, fetch, deliver, with retries and a named failure for
whatever could not be fetched. Nothing in the running app ever called it —
no CLI command, no scheduler, no route. This module is that missing caller:
for every PUBLISHED feed whose `operations.endpoint_ref` resolves to a
fitted connector, poll it once.

A feed with no `endpoint_ref`, or one naming a route nothing is fitted to, is
SKIPPED, not an error — most feeds today are still Draft, or arrive by
manual upload, and neither is a poller's business. A connector that is
fitted but unreachable this cycle is also skipped, not fatal: one payer's
SFTP being briefly down must not stop every other feed's poll in the same
pass, the same reasoning `DeliveryWorker.deliver_available` itself already
applies one remote file at a time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cinqflow.core.model.governed import GovernedObject, ObjectType
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.operations import FeedOperations
from cinqflow.ports.connector import ConnectorError, ConnectorPort
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.storage import StoragePort
from cinqflow.workers.delivery import DeliveryOutcome, DeliveryWorker, PollFailedError

__all__ = ["PollOutcome", "poll_deliveries"]


@dataclass(frozen=True)
class PollOutcome:
    """What happened polling one feed's connector, once."""

    feed_id: str
    delivered: tuple[DeliveryOutcome, ...] = ()
    error: str | None = None


def _published_feed(metadata: MetadataDbPort, feed_id: str) -> GovernedObject | None:
    """The highest EXECUTABLE version, or `None`. Same walk `workers.run_feed
    ._published` does — a feed that cannot yet run is skipped here, not
    raised, since a poll pass covers every feed, most of which are Draft."""
    executable = [obj for obj in metadata.history(ObjectType.FEED, feed_id) if obj.is_executable]
    if not executable:
        return None
    return max(executable, key=lambda obj: obj.version)


def poll_deliveries(
    metadata: MetadataDbPort,
    storage: StoragePort,
    control: ControlTablesPort,
    connectors: dict[str, ConnectorPort],
    *,
    business_date: str,
) -> tuple[PollOutcome, ...]:
    """One poll pass over every Published feed with a resolvable connector.

    `business_date` is supplied by the caller as "the cycle we are polling
    for" — the same plain string `DeliveryWorker.deliver_available` already
    takes, never derived from a remote file's own name or timestamp.
    """
    results: list[PollOutcome] = []
    for candidate in metadata.list(ObjectType.FEED):
        feed_obj = _published_feed(metadata, candidate.object_id)
        if feed_obj is None:
            continue
        endpoint_ref = FeedOperations.from_body(feed_obj.body.get("operations")).endpoint_ref
        connector = connectors.get(endpoint_ref) if endpoint_ref else None
        if connector is None:
            continue
        feed = feed_registry.from_governed(feed_obj)
        worker = DeliveryWorker(connector=connector, storage=storage, control=control, metadata=metadata)
        try:
            outcomes = worker.deliver_available(
                feed=feed, feed_version=feed_obj.version, business_date=business_date
            )
        except PollFailedError as failure:
            results.append(PollOutcome(feed_id=feed.feed_id, delivered=failure.landed, error=str(failure)))
            continue
        except ConnectorError as failure:
            results.append(PollOutcome(feed_id=feed.feed_id, error=str(failure)))
            continue
        if outcomes:
            results.append(PollOutcome(feed_id=feed.feed_id, delivered=outcomes))
    return tuple(results)
