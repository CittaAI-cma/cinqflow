"""CF-V1-E8-03 — the tick. The worker `pg_orchestration.due()` was written for.

    "I want the engine to run feeds on their registry schedules, respect
     declared dependencies between feeds and layers, and automatically pause
     downstream processing when something upstream fails, so that the platform
     behaves like one coordinated system rather than a pile of independent
     jobs"
    — CF-V1-E8-03

THE GAP THIS CLOSES WAS NOT A MISSING DECISION — IT WAS A MISSING CALLER.
`core.scheduling` has decided since Wave 1: `decide()` computes the four
gates, `guard_start()` refuses a held run, `notice_for()` says who to tell
once, `recovered()` says when a hold cleared. `OrchestrationPort.due()` has
listed what is owed since the pin was fitted, and its own docstring names the
missing piece verbatim — "the worker turns each due run into
`queue.enqueue(dedupe_key=feed/business_date)`". Nothing was on the receiving
end. `due()` had exactly two callers in the whole repository, and both were
tests. A schedule nothing ticks is a cron expression in a database column.

ARCHETYPE B — a plain synchronous method, callable from a test, a CLI command
or a cron entry, holding no dispatch mechanism of its own. The same shape
`workers.incidents.IncidentWorker` and `workers.sla.SlaWorker` already are,
and for the same reason: binding a tick to a scheduler here would make the
tick untestable without one.

IT ENQUEUES; IT DOES NOT RUN. The pipeline is `workers.pipeline`'s business
and reaches a layer through `compute_job`; this worker's whole job is to
decide WHETHER a run may start and to put it on the queue if so. Two
consequences worth the separation: a tick that fires while the plane is busy
costs one queue insert, and the thing that actually opens a batch calls
`guard_start()` itself — so a message that sat in the queue while its upstream
broke is refused at the point of execution, not merely at the point of
scheduling.

NOTIFY ONCE, AND THE PRODUCER OWNS IT. "Operations is notified once (not per
held run)" is the story's own words, and `ReleaseDecision.notice_key` is what
makes it structural rather than disciplined: the key is built from the ROOT
CAUSES, so twenty hourly evaluations of one broken upstream dedupe to one
message, and a hold whose reason genuinely changes produces a second. The
queue's `enqueue(dedupe_key=...)` is what enforces it — this module does not
keep a set of "already told", because a worker restart would empty it.

A HOLD THAT CLEARS ITSELF TELLS NOBODY. `notice_for()` returns `None` for
those, and that asymmetry is deliberate: a team paged because last month's
batch is still loading learns to ignore the channel that also carries the
failures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cinqflow.core import scheduling
from cinqflow.core.model.governed import ObjectType
from cinqflow.core.model.vocabulary import BatchState
from cinqflow.ports.control_tables import BatchControl, ControlTablesPort, StageStatus
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.notification import Alert, NotificationPort, Severity
from cinqflow.ports.orchestration import OrchestrationPort, ScheduledRun
from cinqflow.ports.queue import QueuePort

__all__ = ["RUN_FEED_TOPIC", "SchedulerWorker", "TickReport"]

#: The topic a released run is enqueued under. One topic, not one per feed —
#: `workers.consumer.Consumer` routes a topic to a handler, and a topic per
#: feed would reintroduce per-feed wiring in the one place this platform has
#: spent Wave 0 removing it.
RUN_FEED_TOPIC = "pipeline.run_feed"


@dataclass(frozen=True)
class Held:
    """One run that did not start, and the whole reason it did not."""

    feed_id: str
    business_date: str
    decision: scheduling.ReleaseDecision
    notified: bool

    @property
    def explanation(self) -> str:
        return self.decision.explain()


@dataclass(frozen=True)
class TickReport:
    """What one tick did. Returned rather than logged, so a test can read it
    and an operator's CLI can print it."""

    as_of: datetime
    released: tuple[str, ...] = ()
    held: tuple[Held, ...] = ()
    recovered: tuple[str, ...] = ()

    @property
    def due_count(self) -> int:
        return len(self.released) + len(self.held)

    def explain(self) -> str:
        if self.due_count == 0:
            return f"{self.as_of.isoformat()}: nothing due."
        lines = [f"{self.as_of.isoformat()}: {len(self.released)} released, {len(self.held)} held."]
        lines.extend(f"  released {message_id}" for message_id in self.released)
        lines.extend(f"  held {hold.feed_id} {hold.business_date}" for hold in self.held)
        lines.extend(f"  recovered {feed_id}" for feed_id in self.recovered)
        return "\n".join(lines)


@dataclass
class SchedulerWorker:
    """Read what is due, decide, enqueue or hold. Once per tick.

    `notify` is optional: a deployment with no notification pin still
    schedules correctly and simply cannot tell anybody about a hold, which is
    the honest degrade — the same shape `create_app` uses for the same pin.
    """

    orchestration: OrchestrationPort
    metadata: MetadataDbPort
    control: ControlTablesPort
    queue: QueuePort
    notify: NotificationPort | None = None
    #: The previous tick's decisions, keyed by feed and period. Held IN MEMORY
    #: and ONLY to answer "did this clear since last time" — never to decide
    #: whether a run may start, which is recomputed every tick from the
    #: control rows as they are now. A worker restart loses the recovery
    #: notices and no releases, which is the correct thing to lose.
    _previous: dict[tuple[str, str], scheduling.ReleaseDecision] = field(
        default_factory=dict, repr=False
    )

    def tick(self, as_of: datetime | None = None) -> TickReport:
        stamp = as_of or datetime.now(UTC)
        due = self.orchestration.due(stamp)
        if not due:
            return TickReport(as_of=stamp)

        graph = scheduling.DependencyGraph.from_feeds(self.metadata.list(ObjectType.FEED))
        suspended = self._suspended(graph)

        released: list[str] = []
        held: list[Held] = []
        recovered: list[str] = []

        for run in due:
            period = _period_of(run)
            decision = scheduling.decide(
                feed_id=run.feed_id,
                business_date=period,
                graph=graph,
                batches=self._batches(run.feed_id, graph),
                stages=self._stages(run.feed_id, graph),
                suspended=suspended,
            )
            previous = self._previous.get((run.feed_id, period))
            if previous is not None and scheduling.recovered(previous, decision):
                recovered.append(run.feed_id)
                self._announce_recovery(run.feed_id, period)
            self._previous[(run.feed_id, period)] = decision

            if decision.may_run:
                released.append(self._release(run, period))
                continue
            held.append(Held(run.feed_id, period, decision, self._hold(decision)))

        return TickReport(
            as_of=stamp,
            released=tuple(released),
            held=tuple(held),
            recovered=tuple(recovered),
        )

    # ── release ──────────────────────────────────────────────────────────────

    def _release(self, run: ScheduledRun, period: str) -> str:
        """Enqueue, and let `trigger` record that the occurrence was taken.

        DEDUPED ON `feed/business_date`, exactly as `pg_orchestration.due()`'s
        docstring specifies — "a tick that fires twice enqueues once". The
        queue returns the EXISTING message id for a repeated key, so a second
        tick in the same minute is a no-op rather than a duplicate batch.

        `trigger` is called AFTER the enqueue, not before. A crash between the
        two leaves an occurrence the next tick will offer again, and the
        dedupe key makes that second offer harmless; the reverse order would
        mark an occurrence taken for work nobody queued, and the run would
        simply never happen.
        """
        message_id = self.queue.enqueue(
            RUN_FEED_TOPIC,
            {"feed_id": run.feed_id, "business_date": period},
            dedupe_key=f"{RUN_FEED_TOPIC}:{run.feed_id}:{period}",
        )
        self.orchestration.trigger(run.feed_id, business_date=period)
        return message_id

    # ── hold ─────────────────────────────────────────────────────────────────

    def _hold(self, decision: scheduling.ReleaseDecision) -> bool:
        """Tell somebody, once — or nobody, when the hold clears itself."""
        notice = scheduling.notice_for(decision)
        if notice is None or self.notify is None:
            return False
        # The dedupe lives on the QUEUE, so notify-once survives a restart of
        # this worker. Enqueuing the notice rather than alerting directly is
        # what buys that: an in-process "already told" set would forget on
        # every deploy and re-page the team.
        seen = self.queue.enqueue(
            "notification.hold",
            {"feed_id": notice.feed_id, "business_date": notice.business_date},
            dedupe_key=notice.dedupe_key,
        )
        if seen in self._announced:
            return False
        self._announced.add(seen)
        self.notify.alert(
            Alert(
                severity=(
                    Severity.CRITICAL
                    if decision.batch_state is BatchState.BLOCKED
                    else Severity.WARNING
                ),
                summary=notice.subject,
                detail=notice.body,
            )
        )
        return True

    def _announce_recovery(self, feed_id: str, period: str) -> None:
        """ "Everything resumes when enrollment recovers" is only visible to an
        operator who was told the hold existed if they are also told it
        lifted."""
        if self.notify is None:
            return
        self.notify.alert(
            Alert(
                severity=Severity.INFO,
                summary=f"{feed_id} {period} is no longer held",
                detail=(
                    f"The upstream that was blocking {feed_id} for {period} has cleared. "
                    "Nothing was released by hand — the hold is recomputed every tick, so "
                    "held work resumes on its own."
                ),
            )
        )

    _announced: set[str] = field(default_factory=set, repr=False)

    # ── reading the plane ────────────────────────────────────────────────────

    def _suspended(self, graph: scheduling.DependencyGraph) -> frozenset[str]:
        now = datetime.now(UTC)
        return frozenset(
            feed_id
            for feed_id in graph.order()
            if self.metadata.current_suspension(feed_id).is_active_at(now)
        )

    def _related(self, feed_id: str, graph: scheduling.DependencyGraph) -> tuple[str, ...]:
        """This feed and everything it declares upstream, transitively — the
        same walk `api.app._related_feeds` does for the dependency picture.

        UPSTREAMS ONLY. A hold is computed from what this feed waits for;
        pulling downstream feeds' control rows would be reading rows to decide
        nothing.
        """
        reached = [feed_id]
        frontier = [feed_id]
        while frontier:
            current = frontier.pop(0)
            for parent in graph.upstream_of(current):
                if parent not in reached:
                    reached.append(parent)
                    frontier.append(parent)
        return tuple(reached)

    def _batches(self, feed_id: str, graph: scheduling.DependencyGraph) -> Sequence[BatchControl]:
        return [
            batch
            for name in self._related(feed_id, graph)
            for batch in self.control.list_batches(name)
        ]

    def _stages(
        self, feed_id: str, graph: scheduling.DependencyGraph
    ) -> dict[str, Sequence[StageStatus]]:
        """The LAYER an upstream failed at — which does not change the
        decision, only the sentence an operator reads. "Enrollment failed at
        Silver Raw" sends them to a different runbook than "failed at
        Landing"."""
        return {
            batch.batch_id: self.control.get_stages(batch.batch_id)
            for batch in self._batches(feed_id, graph)
        }


def _period_of(run: ScheduledRun) -> str:
    """A business date is a DATE, and the occurrence is what names it.

    Taken from `scheduled_for` rather than from "today": a monthly feed whose
    tick fires at 03:00 on the 1st is running the occurrence the cron named,
    and a late tick must still process the period it was owed rather than the
    period it woke up in.
    """
    return run.scheduled_for.date().isoformat()
