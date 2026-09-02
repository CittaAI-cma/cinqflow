"""CF-V1-E8-03 completed — `tick`, then drain, on a schedule, forever.

`cinqflow tick` and `cinqflow work` are each correct and each ONE-SHOT: open a
connection, do the one thing, return. Nothing has ever called them
repeatedly — no compose service, no cron entry, no sidecar loop existed
anywhere, so a feed's published schedule fires and nothing runs it unless a
person types both commands by hand, every cycle, forever.

This module is the missing repetition, and nothing else: `run_once` is the
same "tick, then drain" pairing `installer.cli.serve_worker` builds real
adapters around, kept here as a plain function so a test can call it with
mock ports and assert on what it did, the same Archetype-B shape
`SchedulerWorker.tick` itself already is ("callable from a test, a CLI
command or a cron entry, holding no dispatch mechanism of its own").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cinqflow.workers.consumer import Consumer
from cinqflow.workers.scheduler import SchedulerWorker, TickReport


@dataclass(frozen=True)
class LoopIteration:
    """One pass: what the tick found, and how many queued messages drained
    across every topic. Returned rather than only logged, so a test can
    assert on it directly without capturing stdout."""

    tick: TickReport
    processed: int


def run_once(scheduler: SchedulerWorker, consumer: Consumer, *, topics: Sequence[str]) -> LoopIteration:
    """Tick once, then drain every named topic once. `topics` is a parameter
    rather than a module constant so this stays decoupled from which
    handlers a caller happens to have registered — today that is
    `pipeline.run_feed`; a caller wiring `agent.run` alongside it passes both,
    and this function need not change to know either name."""
    tick_report = scheduler.tick()
    processed = sum(consumer.drain_topic(topic) for topic in topics)
    return LoopIteration(tick=tick_report, processed=processed)
