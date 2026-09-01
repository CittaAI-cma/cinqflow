"""The generic queue consumer — claim, dispatch, let the queue decide the rest.

`ports.queue` was declared in Wave 0, and until now nothing has ever called
`.claim()` on it. `adapters/local/pg_orchestration.py`'s own docstring names
the gap: "the worker turns each due run into `queue.enqueue(...)`", and there
has never been a worker on the receiving end.

THE ONE THING THIS MODULE OWNS: routing a topic to the handler registered for
it. It does not decide what a handler does with a payload, does not retry
beyond what the queue's own `claim()` context manager already guarantees, and
holds no state about WHY a message exists — that is the enqueuing worker's
business, not the consumer's. A dispatcher that starts making decisions about
payload contents is a dispatcher that has started owning what it moves.

WHY HANDLERS ARE REGISTERED RATHER THAN LOOKED UP BY CONVENTION. A topic
string with no handler behind it is a message that will sit claimed-and-
failed forever if the consumer guesses, or silently drop if it shrugs.
Refusing BEFORE the claim — `NoHandlerError`, raised without touching the
queue — means the message stays pending and visible, which is what "a
control table nothing writes is a façade" already argues for the SLA tables
this consumer exists to serve.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cinqflow.ports.queue import QueuePort

Handler = Callable[[dict[str, Any]], None]


class ConsumerError(RuntimeError):
    """A message this consumer could not dispatch."""


class NoHandlerError(ConsumerError):
    """A topic claimed with nothing registered to handle it."""


class Consumer:
    """One queue, many topics, each topic its own chip.

    Holds no reference to any worker's internals — `register` takes a plain
    callable, so `SlaWorker.materialise` and `SlaWorker.sweep` plug in without
    this module importing `workers.sla` or knowing it exists. Adding the next
    worker is a `register()` call, not a change here.
    """

    def __init__(self, queue: QueuePort) -> None:
        self._queue = queue
        self._handlers: dict[str, Handler] = {}

    def register(self, topic: str, handler: Handler) -> None:
        self._handlers[topic] = handler

    def run_once(self, topic: str) -> bool:
        """Claim and dispatch one message. `False` means the topic was empty
        — not an error, and not something worth raising over.

        The handler's exception, if any, propagates UNCAUGHT: the queue's own
        `claim()` context manager is what returns the message to `pending`
        with its attempt counted, and catching the exception here would hide
        that from the caller while silently defeating it.
        """
        handler = self._handlers.get(topic)
        if handler is None:
            raise NoHandlerError(
                f"{topic!r} has no registered handler — refused before the claim, so the "
                "message stays pending rather than vanishing into a dispatcher that guessed"
            )
        with self._queue.claim(topic) as message:
            if message is None:
                return False
            handler(message.payload)
        return True

    def drain_topic(self, topic: str) -> int:
        """Process every message currently pending on `topic`, once each.

        Bounded by what was pending WHEN THIS CALL STARTED reading `run_once`
        results, not by a fixed count — a topic that keeps receiving new work
        while this drains would otherwise never return, which is the wrong
        shape for a method a CLI command calls and expects to finish.
        """
        processed = 0
        while self.run_once(topic):
            processed += 1
        return processed
