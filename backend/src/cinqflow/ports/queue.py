"""The `queue` pin — enqueue and consume work.

    verb: enqueue/consume   mock: memq   dev: pg_skip_locked
    target: pg_skip_locked
    — docs/architecture/plates/04-pin-out-map.md

Note the ladder: dev and target are IDENTICAL. `SELECT ... FOR UPDATE SKIP
LOCKED` on the Postgres already running is the implementation at rung 0.5 and
at rung 4 alike (ADR-0014). Volume is thousands of messages a day, and a
proprietary broker SDK would silently weld the platform to one cloud for
throughput nobody needs.

The seat for a broker exists. It stays empty until measurement demands it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    message_id: str
    topic: str
    payload: dict[str, Any]
    enqueued_ts: datetime
    attempts: int = 0
    # Idempotency key: the same work claimed twice must not run twice.
    dedupe_key: str | None = None


@dataclass(frozen=True)
class QueueStats:
    pending: int = 0
    in_flight: int = 0
    failed: int = 0
    by_topic: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class QueuePort(Protocol):
    def enqueue(self, topic: str, payload: dict[str, Any], dedupe_key: str | None = None) -> str:
        """Returns the message id. A repeated dedupe_key returns the EXISTING
        id rather than enqueuing twice — replay safety starts here, not at the
        consumer."""
        ...

    def claim(self, topic: str, *, timeout_s: int = 300) -> AbstractContextManager[Message | None]:
        """Claim one message for exclusive processing.

        A context manager on purpose: leaving the block returns the message to
        the queue if the body raised, and acknowledges it if the body
        succeeded. A worker that crashes cannot strand work, and cannot
        half-acknowledge it either.
        """
        ...

    def drain(self, topic: str) -> Iterator[Message]:
        """Every pending message, for tests and for operational inspection."""
        ...

    def stats(self) -> QueueStats: ...
