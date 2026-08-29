"""memq — an in-memory queue with the same claim semantics as SKIP LOCKED."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.queue import Message, QueueStats


@port("queue", "mock")
class MemQueue:
    """Claim-with-return-on-failure, matching the Postgres adapter's contract.

    The context-manager shape is the important part to mirror: a worker that
    raises must return its message to the queue, and a worker that succeeds
    must acknowledge exactly once. Getting that wrong in the mock would let a
    whole class of double-processing bug pass CI.
    """

    def __init__(self) -> None:
        self._pending: list[Message] = []
        self._in_flight: dict[str, Message] = {}
        self._dedupe: dict[str, str] = {}
        self._failed: list[Message] = []

    def enqueue(self, topic: str, payload: dict[str, Any], dedupe_key: str | None = None) -> str:
        if dedupe_key and dedupe_key in self._dedupe:
            return self._dedupe[dedupe_key]  # replay safety starts here
        message = Message(
            message_id=str(uuid.uuid4()),
            topic=topic,
            payload=payload,
            enqueued_ts=datetime.now(UTC),
            dedupe_key=dedupe_key,
        )
        self._pending.append(message)
        if dedupe_key:
            self._dedupe[dedupe_key] = message.message_id
        return message.message_id

    @contextmanager
    def claim(self, topic: str, *, timeout_s: int = 300) -> Iterator[Message | None]:
        _ = timeout_s
        message: Message | None = next((m for m in self._pending if m.topic == topic), None)
        if message is None:
            yield None
            return
        self._pending.remove(message)
        self._in_flight[message.message_id] = message
        try:
            yield message
        except Exception:
            # Returned to the queue, attempt counted. A crashed worker strands
            # nothing, and a poison message is visible rather than invisible.
            self._in_flight.pop(message.message_id, None)
            retried = Message(
                message_id=message.message_id,
                topic=message.topic,
                payload=message.payload,
                enqueued_ts=message.enqueued_ts,
                attempts=message.attempts + 1,
                dedupe_key=message.dedupe_key,
            )
            self._pending.append(retried)
            raise
        else:
            self._in_flight.pop(message.message_id, None)

    def drain(self, topic: str) -> Iterator[Message]:
        yield from (m for m in list(self._pending) if m.topic == topic)

    def stats(self) -> QueueStats:
        by_topic: dict[str, int] = {}
        for message in self._pending:
            by_topic[message.topic] = by_topic.get(message.topic, 0) + 1
        return QueueStats(
            pending=len(self._pending),
            in_flight=len(self._in_flight),
            failed=len(self._failed),
            by_topic=by_topic,
        )
