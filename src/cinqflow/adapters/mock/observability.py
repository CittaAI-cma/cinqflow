"""noop — records signals in memory so tests can assert on them."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cinqflow.ports import port


@port("observability", "mock")
class NoopObservability:
    """Not quite a no-op: it REMEMBERS.

    A true no-op would make "the attempt is logged" — which appears in almost
    every guardrail in the programme — untestable at rung 0. So the mock keeps
    what it was told, and the negative tests assert against it.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.metrics: list[tuple[str, float, dict[str, str]]] = []
        self.spans: list[str] = []

    def log(self, event: str, /, **fields: Any) -> None:
        self.events.append((event, fields))

    def metric(self, name: str, value: float, /, **labels: str) -> None:
        self.metrics.append((name, value, labels))

    @contextmanager
    def span(self, name: str, /, **attributes: Any) -> Iterator[None]:
        _ = attributes
        self.spans.append(name)
        yield
