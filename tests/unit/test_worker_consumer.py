"""The queue consumer nothing in the estate has needed until now.

`ports.queue` was declared in Wave 0 and nothing has ever called `.claim()` —
`pg_orchestration.due()`'s own docstring names the gap: "the worker turns each
into `queue.enqueue(...)`", and there has never been a worker on the other
end. `workers/sla.py` is the first consumer, and this is the small, generic
piece it and every consumer after it rides: register a handler per topic,
claim one message, dispatch, let the queue's own context manager decide
ack-or-return.

WHY THIS IS A SEPARATE MODULE FROM `workers/sla.py`. `SlaWorker.materialise`
and `.sweep` are plain synchronous methods, exactly like `PipelineRunner.run`
— testable and callable with no queue in sight. The consumer is the thing
that lets them ALSO be reached by "something enqueued a tick", which is a
distinct concern from "what a tick does". Keeping them apart is what makes the
consumer reusable by the next worker rather than copied.
"""

from __future__ import annotations

import pytest

from cinqflow.adapters.mock.queue import MemQueue
from cinqflow.workers.consumer import Consumer, NoHandlerError

pytestmark = pytest.mark.unit


def test_a_claimed_message_is_dispatched_to_its_registered_handler() -> None:
    queue = MemQueue()
    queue.enqueue("sla.materialise", {"business_date": "2026-08-30"})
    seen: list[dict[str, object]] = []

    consumer = Consumer(queue)
    consumer.register("sla.materialise", seen.append)

    assert consumer.run_once("sla.materialise") is True
    assert seen == [{"business_date": "2026-08-30"}]


def test_an_empty_topic_processes_nothing_and_says_so() -> None:
    queue = MemQueue()
    consumer = Consumer(queue)
    consumer.register("sla.materialise", lambda payload: None)
    assert consumer.run_once("sla.materialise") is False


def test_a_topic_with_no_registered_handler_is_refused_before_it_is_claimed() -> None:
    """A message claimed for a handler that does not exist would be silently
    dropped or silently retried forever — refusing before the claim means the
    message stays pending and visible rather than vanishing."""
    queue = MemQueue()
    queue.enqueue("no.such.handler", {})
    consumer = Consumer(queue)
    with pytest.raises(NoHandlerError):
        consumer.run_once("no.such.handler")
    assert queue.stats().pending == 1


def test_a_handler_that_raises_returns_its_message_to_the_queue() -> None:
    """The queue's own claim contract — a crashed worker strands nothing —
    and the consumer must not swallow that guarantee on the way through."""
    queue = MemQueue()
    queue.enqueue("sla.materialise", {"business_date": "2026-08-30"})

    def failing(payload: dict[str, object]) -> None:
        raise RuntimeError("the control plane is unreachable")

    consumer = Consumer(queue)
    consumer.register("sla.materialise", failing)

    with pytest.raises(RuntimeError):
        consumer.run_once("sla.materialise")

    stats = queue.stats()
    assert stats.pending == 1
    (retried,) = queue.drain("sla.materialise")
    assert retried.attempts == 1


def test_draining_a_topic_processes_every_pending_message_once() -> None:
    queue = MemQueue()
    for day in ("2026-08-28", "2026-08-29", "2026-08-30"):
        queue.enqueue("sla.materialise", {"business_date": day})
    seen: list[dict[str, object]] = []

    consumer = Consumer(queue)
    consumer.register("sla.materialise", seen.append)

    assert consumer.drain_topic("sla.materialise") == 3
    assert [p["business_date"] for p in seen] == ["2026-08-28", "2026-08-29", "2026-08-30"]
    assert queue.stats().pending == 0


def test_draining_an_empty_topic_processes_zero_and_does_not_raise() -> None:
    queue = MemQueue()
    consumer = Consumer(queue)
    consumer.register("sla.materialise", lambda payload: None)
    assert consumer.drain_topic("sla.materialise") == 0


def test_two_topics_route_to_two_different_handlers() -> None:
    """The whole point of registering by topic: one consumer, many chips
    plugged in, none of them aware of the others."""
    queue = MemQueue()
    queue.enqueue("sla.materialise", {"kind": "materialise"})
    queue.enqueue("sla.sweep", {"kind": "sweep"})
    seen: dict[str, list[object]] = {"materialise": [], "sweep": []}

    consumer = Consumer(queue)
    consumer.register("sla.materialise", lambda p: seen["materialise"].append(p))
    consumer.register("sla.sweep", lambda p: seen["sweep"].append(p))

    consumer.run_once("sla.materialise")
    consumer.run_once("sla.sweep")

    assert seen["materialise"] == [{"kind": "materialise"}]
    assert seen["sweep"] == [{"kind": "sweep"}]
