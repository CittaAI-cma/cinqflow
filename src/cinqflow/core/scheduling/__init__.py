"""CF-V1-E8-03 — dependencies between feeds, and the hold that clears itself.

    "I want the engine to run feeds on their registry schedules, respect
     declared dependencies between feeds and layers, and automatically pause
     downstream processing when something upstream fails, so that the platform
     behaves like one coordinated system rather than a pile of independent
     jobs — bad upstream data never cascades."
    "Release held work automatically when the upstream issue clears."
    — CF-V1-E8-03

A HOLD IS A COMPUTED ANSWER, NOT A STORED FLAG. That single decision is what
makes the story's third acceptance criterion free instead of a feature.

The obvious implementation writes `WAITING_DEPENDENCY` into `batch_control`
when a dependency is unmet, and then needs something — a sweeper, a callback,
an operator — to notice that the upstream recovered and set the row back. Every
one of those is a place where held work can be forgotten, and forgotten held
work is indistinguishable from work that was never scheduled. The incumbent
platform's longest outage was of exactly this shape.

So `decide` is a pure function of the CURRENT state of the control tables. A
feed is held because its upstream is failed right now; when the upstream's
recovery batch completes, the very next evaluation returns RUN, and nobody
cleared anything. There is no release path because there is nothing to release.

WHAT IS STORED IS THE NOTICE, NOT THE HOLD — and that asymmetry is deliberate.
"Operations is notified once (not per held run)" needs memory: a schedule that
fires hourly against a failed upstream must not page anybody twenty-four times.
So the hold is recomputed every evaluation and the NOTIFICATION carries a
`dedupe_key`, which `queue.message` already makes idempotent at the producer
("a repeated dedupe_key returns the existing message"). One mechanism, already
built, already tested — and the key includes the ROOT CAUSE, so a hold whose
reason changes from "enrollment failed" to "enrollment is still loading" is a
second, genuinely different, message.

THE CHAIN IS FIRST-CLASS. "The run history shows the dependency chain that
gated them" is not a log line; a two-hop hold — claims waits on enrollment,
enrollment waits on the reference load — must name the ROOT cause and the path
to it, because an operator told only "waiting on enrollment" will go and look
at a feed that is itself blameless.

WHAT THIS MODULE IS NOT. It does not schedule: `OrchestrationPort` owns cron
and `queue.schedule` owns the registered state. It answers one question —
*may this run start, and if not, why* — and it answers it in `core/`, purely,
so the local plane and the cluster cannot disagree about whether a batch was
allowed to begin.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import BatchState, Layer, StatusWord

#: The feed body key that declares upstreams. Read from the governed body
#: rather than from a private table for the reason `core.impact` reads its
#: edges from bodies: a dependency is APPROVED CONFIGURATION, so it travels the
#: lifecycle, appears in the approval packet's diff, and cannot be changed
#: without somebody signing for it.
DEPENDS_ON_KEY = "depends_on"


class SchedulingError(RuntimeError):
    """A dependency arrangement the engine will not run."""


class CircularDependencyError(SchedulingError):
    """Two feeds that wait for each other.

    Refused when the GRAPH is built, not when a run deadlocks at 3am. A cycle
    is not a rare edge case in a platform whose dependencies are typed by
    humans — it is what happens the first time somebody adds "claims needs
    enrollment" to a feed that enrollment already needed — and the failure it
    causes is the worst kind: both feeds sit in Waiting-on-Upstream forever,
    each one's status page truthfully naming the other.
    """


class DependencyHoldError(SchedulingError):
    """A run was started while its upstream was failed or held.

        "Start a dependent run when its upstream is failed or on hold — no
         overrides outside the governed exception flow." — CF-V1-E8-03's don't

    Raised by `guard_start`, which the runner calls before it opens a batch.
    The don't says "no overrides", so there is no `force` parameter here: an
    exception path is a governed act with an approver, and a keyword argument
    is not that.
    """


# ── why a run is held ────────────────────────────────────────────────────────
@unique
class HoldReason(StrEnum):
    """The whole vocabulary of reasons a scheduled run does not start.

    Six, and each one means something different to the person reading it:
    "the upstream broke" sends an operator to an incident, "the upstream has
    not arrived" sends them to a payer, and "the prior month is still open"
    sends them nowhere at all because it will clear itself. A single
    `BLOCKED` state would send all three to the same place.
    """

    #: The upstream batch for this period reached FAILED.
    UPSTREAM_FAILED = "upstream_failed"
    #: The upstream batch exists and has not finished.
    UPSTREAM_IN_PROGRESS = "upstream_in_progress"
    #: No batch at all for the upstream this period. The file has not come.
    UPSTREAM_NOT_ARRIVED = "upstream_not_arrived"
    #: The upstream is itself held. Reported with the ROOT cause attached.
    UPSTREAM_HELD = "upstream_held"
    #: This feed's own earlier period has not completed. "A month's file never
    #: processes before the prior month completes."
    PRIOR_PERIOD_INCOMPLETE = "prior_period_incomplete"
    #: CF-V1-E3-04's pause — the second axis. Not a dependency, and reported
    #: here anyway, because the operator asking "why has this not run?" wants
    #: one answer rather than two screens.
    FEED_SUSPENDED = "feed_suspended"

    @property
    def clears_itself(self) -> bool:
        """True when normal operation resolves it and nobody need act.

        Drives whether Operations is notified at all. Paging somebody because
        last month's batch is still loading is how a team learns to ignore the
        channel that also carries the failures.
        """
        return self in {
            HoldReason.UPSTREAM_IN_PROGRESS,
            HoldReason.PRIOR_PERIOD_INCOMPLETE,
        }

    @property
    def status_word(self) -> StatusWord:
        """The seven words, and no eighth."""
        if self is HoldReason.UPSTREAM_NOT_ARRIVED:
            return StatusWord.MISSING
        if self.clears_itself:
            return StatusWord.PROCESSING
        return StatusWord.NEEDS_ATTENTION


@dataclass(frozen=True)
class Blocker:
    """One thing standing between a schedule and a run.

    Carries the blocking BATCH, not just the blocking feed. "Operations sees
    the blocking batch linked" is the story's own wording, and a link to a feed
    lands somebody on a page listing two hundred runs.
    """

    reason: HoldReason
    feed_id: str
    business_date: str
    batch_id: str | None = None
    state: BatchState | None = None
    #: Where it stopped. "Given enrollment fails at Silver Raw" — an operator
    #: who knows the layer knows which runbook to open.
    layer: Layer | None = None
    #: The path from the held feed to this blocker, held feed first. Empty for
    #: a direct dependency; populated when the block is inherited.
    chain: tuple[str, ...] = ()

    @property
    def is_root(self) -> bool:
        return self.reason is not HoldReason.UPSTREAM_HELD

    def explain(self) -> str:
        """One sentence an operator can act on without opening anything else."""
        where = f" at {self.layer.value}" if self.layer else ""
        batch = f" (batch {self.batch_id})" if self.batch_id else ""
        via = f" — via {' -> '.join(self.chain)}" if len(self.chain) > 1 else ""
        match self.reason:
            case HoldReason.UPSTREAM_FAILED:
                return (
                    f"{self.feed_id} failed{where} for {self.business_date}{batch}. "
                    f"Nothing downstream runs until it recovers{via}."
                )
            case HoldReason.UPSTREAM_IN_PROGRESS:
                return (
                    f"{self.feed_id} is still processing {self.business_date}{batch}. "
                    f"This will start on its own when that finishes{via}."
                )
            case HoldReason.UPSTREAM_NOT_ARRIVED:
                return (
                    f"{self.feed_id} has no batch for {self.business_date} yet. "
                    f"The upstream file has not arrived{via}."
                )
            case HoldReason.UPSTREAM_HELD:
                return f"{self.feed_id} is itself waiting for {self.business_date}{via}."
            case HoldReason.PRIOR_PERIOD_INCOMPLETE:
                return (
                    f"{self.feed_id}'s earlier period {self.business_date} has not "
                    f"completed{batch}. Order matters on this feed, so this period waits."
                )
            case HoldReason.FEED_SUSPENDED:
                return f"{self.feed_id} is paused. A paused feed accepts no new work."


# ── the decision ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReleaseDecision:
    """May this run start, and if not, why — the whole answer, computed.

    Returned by `decide` and stored NOWHERE. Recomputing it is the release
    mechanism; see the module docstring.
    """

    feed_id: str
    business_date: str
    blockers: tuple[Blocker, ...] = ()

    @property
    def may_run(self) -> bool:
        return not self.blockers

    @property
    def batch_state(self) -> BatchState:
        """What `batch_control.state` would say if this run were opened.

        WAITING_DEPENDENCY and BLOCKED were in the Wave-0 vocabulary before
        this story existed, and they mean different things: waiting is a
        dependency that will clear, blocked is one that needs a person.
        """
        if self.may_run:
            return BatchState.RECEIVED
        if all(b.reason.clears_itself for b in self.blockers):
            return BatchState.WAITING_DEPENDENCY
        return BatchState.BLOCKED

    @property
    def status_word(self) -> StatusWord:
        if self.may_run:
            return StatusWord.PROCESSING
        # The most urgent word wins: an operator scanning a board must not see
        # "Processing" on a feed whose upstream is broken because one of three
        # blockers happens to be benign.
        order = [StatusWord.NEEDS_ATTENTION, StatusWord.MISSING, StatusWord.PROCESSING]
        words = {b.reason.status_word for b in self.blockers}
        return next(word for word in order if word in words)

    @property
    def root_causes(self) -> tuple[Blocker, ...]:
        """The blockers that are somebody's problem, not inherited ones."""
        return tuple(b for b in self.blockers if b.is_root)

    @property
    def chain(self) -> tuple[str, ...]:
        """The dependency path that gated this run, longest first.

        "The run history shows the dependency chain that gated them."
        """
        if not self.blockers:
            return ()
        return max((b.chain for b in self.blockers), key=len)

    @property
    def needs_notification(self) -> bool:
        """True when a person should hear about it. See `HoldReason.clears_itself`."""
        return any(not b.reason.clears_itself for b in self.blockers)

    def explain(self) -> str:
        """The hold, self-explanatory, for the operations screen."""
        if self.may_run:
            return f"{self.feed_id} {self.business_date}: nothing upstream is holding it."
        lines = [f"{self.feed_id} {self.business_date} is held:"]
        lines.extend(f"  - {blocker.explain()}" for blocker in self.blockers)
        return "\n".join(lines)

    @property
    def notice_key(self) -> str:
        """The `queue.message` dedupe key. Notify-once, by the producer.

        Built from the ROOT CAUSES rather than from the feed alone, so twenty
        hourly evaluations of the same broken upstream produce one message,
        and a hold whose reason genuinely changes produces a second.
        """
        causes = ";".join(
            f"{b.reason.value}:{b.feed_id}:{b.business_date}:{b.batch_id or '-'}"
            for b in sorted(self.root_causes, key=lambda b: (b.feed_id, b.reason.value))
        )
        return f"hold:{self.feed_id}:{self.business_date}:{causes}"


def guard_start(decision: ReleaseDecision) -> None:
    """Refuse to open a batch that is held. Called by the runner, before
    anything is written.

    No `force`. The don't says "no overrides outside the governed exception
    flow", and a keyword argument is not a governed exception flow.
    """
    if decision.may_run:
        return
    raise DependencyHoldError(decision.explain())


# ── the dependency graph ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class DependencyGraph:
    """Which feeds wait for which. Data, and acyclic by construction.

    Built from PUBLISHED feed objects only. A draft feed's declared dependency
    is not approved configuration, and letting one gate production would mean a
    person could stop a live feed by saving a draft — the exact inversion of
    "the engine reads published metadata and nothing else".
    """

    upstreams: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for feed_id, parents in self.upstreams.items():
            if feed_id in parents:
                raise CircularDependencyError(
                    f"{feed_id} declares itself as its own upstream. It would wait for a "
                    "batch it is not allowed to start."
                )
        if cycle := self._find_cycle():
            raise CircularDependencyError(
                "these feeds wait for each other and none of them could ever start: "
                + " -> ".join(cycle)
            )

    @classmethod
    def from_feeds(cls, objects: Iterable[GovernedObject]) -> DependencyGraph:
        """Read the edges off the registry. Published feeds only.

        An upstream that is named but not published is KEPT as an edge, not
        dropped: `decide` will report it as `UPSTREAM_NOT_ARRIVED`, which is
        true and actionable, whereas silently deleting the edge would start a
        dependent feed as though it had no dependency at all.
        """
        edges: dict[str, tuple[str, ...]] = {}
        for obj in objects:
            if obj.object_type is not ObjectType.FEED:
                continue
            if obj.lifecycle_state is not LifecycleState.PUBLISHED:
                continue
            declared = obj.body.get(DEPENDS_ON_KEY) or ()
            names = tuple(
                str(name).strip()
                for name in declared
                if isinstance(name, str) and str(name).strip()
            )
            edges[obj.object_id] = _deduped(names)
        return cls(upstreams=edges)

    def upstream_of(self, feed_id: str) -> tuple[str, ...]:
        return tuple(self.upstreams.get(feed_id, ()))

    def downstream_of(self, feed_id: str) -> tuple[str, ...]:
        """Everything that waits for this feed, directly. The blast radius of a
        failure, one hop at a time."""
        return tuple(
            sorted(child for child, parents in self.upstreams.items() if feed_id in parents)
        )

    def blast_radius(self, feed_id: str) -> tuple[str, ...]:
        """Everything a failure here would eventually stop, transitively.

        What Operations needs the moment a batch fails: not "one feed is down"
        but "these four will not run tonight". The same reading `core.impact`
        gives a configuration change, over the schedule's edges instead of the
        reference graph's.
        """
        reached: list[str] = []
        seen = {feed_id}
        frontier = [feed_id]
        while frontier:
            current = frontier.pop(0)
            for child in self.downstream_of(current):
                if child in seen:
                    continue
                seen.add(child)
                reached.append(child)
                frontier.append(child)
        return tuple(reached)

    def order(self) -> tuple[str, ...]:
        """Every known feed, upstreams first. Deterministic — ties break
        alphabetically, so a run plan is reproducible and diffable."""
        known = sorted(set(self.upstreams) | {p for ps in self.upstreams.values() for p in ps})
        placed: list[str] = []
        done: set[str] = set()
        while len(placed) < len(known):
            ready = [
                feed
                for feed in known
                if feed not in done and all(p in done for p in self.upstream_of(feed))
            ]
            if not ready:  # pragma: no cover - __post_init__ already refused cycles
                raise CircularDependencyError("the graph has a cycle")
            for feed in ready:
                placed.append(feed)
                done.add(feed)
        return tuple(placed)

    def _find_cycle(self) -> tuple[str, ...] | None:
        colour: dict[str, int] = {}
        path: list[str] = []

        def walk(node: str) -> tuple[str, ...] | None:
            colour[node] = 1
            path.append(node)
            for parent in self.upstream_of(node):
                shade = colour.get(parent, 0)
                if shade == 1:
                    return (*path[path.index(parent) :], parent)
                if shade == 0 and (found := walk(parent)):
                    return found
            path.pop()
            colour[node] = 2
            return None

        for feed in sorted(self.upstreams):
            if colour.get(feed, 0) == 0 and (found := walk(feed)):
                return found
        return None


def _deduped(names: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    return tuple(seen)


# ── what the decision reads ──────────────────────────────────────────────────
#
# STRUCTURAL, NOT IMPORTED. `cinqflow.ports.control_tables` imports FROM core,
# so core importing it back would invert the dependency the whole chip rests
# on — `core` is what the pins are written against, never the other way round.
#
# The alternative to importing is not copying: these Protocols state the four
# fields the decision actually reads, and the port's real `BatchLike` and
# `StageLike` satisfy them structurally with no adapter, no mapping step and
# nothing to keep in step. The engine passes the rows it wrote; core never
# learns there is a table.


@runtime_checkable
class BatchLike(Protocol):
    """One `batch_control` row, as far as scheduling is concerned."""

    @property
    def batch_id(self) -> str: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def business_date(self) -> str: ...
    @property
    def state(self) -> BatchState: ...
    @property
    def started_ts(self) -> datetime: ...


@runtime_checkable
class StageLike(Protocol):
    """One `batch_stage_status` row — only the two fields that name a failure's
    layer, because that is all the explanation needs."""

    @property
    def stage(self) -> Layer: ...
    @property
    def state(self) -> BatchState: ...


#: Batch states that mean "this period is finished and downstream may proceed".
_SETTLED: frozenset[BatchState] = frozenset({BatchState.COMPLETED})

#: Batch states that mean "this period is broken and a person must act".
_BROKEN: frozenset[BatchState] = frozenset({BatchState.FAILED, BatchState.BLOCKED})


def decide(
    *,
    feed_id: str,
    business_date: str,
    graph: DependencyGraph,
    batches: Sequence[BatchLike],
    stages: Mapping[str, Sequence[StageLike]] | None = None,
    suspended: frozenset[str] = frozenset(),
    sequential: bool = True,
) -> ReleaseDecision:
    """May `feed_id` run `business_date`? Pure, total, and recomputed each time.

    THE FOUR GATES, in the order an operator would ask them:

      1. is this feed paused (CF-V1-E3-04's second axis);
      2. has this feed's own earlier period finished — "a month's file never
         processes before the prior month completes";
      3. is every declared upstream complete FOR THIS PERIOD;
      4. and where an upstream is not complete, is it broken, late, still
         loading, or itself waiting on something further up.

    `stages` is optional and supplies the LAYER an upstream failed at. The
    decision does not depend on it — a failure is a failure — but the sentence
    an operator reads does, and "enrollment failed at Silver Raw" sends them to
    a different runbook than "enrollment failed at Landing".
    """
    index = _index(batches)
    blockers: list[Blocker] = []

    if feed_id in suspended:
        blockers.append(
            Blocker(
                reason=HoldReason.FEED_SUSPENDED,
                feed_id=feed_id,
                business_date=business_date,
                chain=(feed_id,),
            )
        )

    if sequential:
        blockers.extend(_prior_period_blockers(feed_id, business_date, index))

    blockers.extend(
        _upstream_blockers(
            feed_id=feed_id,
            business_date=business_date,
            graph=graph,
            index=index,
            stages=stages or {},
            suspended=suspended,
            path=(feed_id,),
            visited={feed_id},
        )
    )
    return ReleaseDecision(
        feed_id=feed_id,
        business_date=business_date,
        blockers=tuple(blockers),
    )


def _index(batches: Sequence[BatchLike]) -> dict[str, dict[str, BatchLike]]:
    """Batches by feed, then by business date — the LATEST attempt per period.

    Latest by `started_ts`, so a period that failed and was restarted is judged
    on the restart. Judging on the first attempt would hold every downstream
    feed behind a batch that has already been fixed, which is precisely the
    "held work nobody released" failure this module exists to avoid.
    """
    found: dict[str, dict[str, BatchLike]] = {}
    for batch in batches:
        periods = found.setdefault(batch.feed_id, {})
        current = periods.get(batch.business_date)
        if current is None or batch.started_ts >= current.started_ts:
            periods[batch.business_date] = batch
    return found


def _prior_period_blockers(
    feed_id: str, business_date: str, index: dict[str, dict[str, BatchLike]]
) -> list[Blocker]:
    """Every earlier period of THIS feed that has not completed.

    Business dates are ISO (`2026-08` or `2026-08-31`), so string order is
    chronological order and no date parsing is needed — which matters because
    a feed's period granularity is its own business (monthly rosters, daily
    census) and this must not have an opinion about which.

    Reports EVERY unfinished earlier period, not just the newest. A feed three
    months behind should say so; naming only the most recent gap would let an
    operator fix one month and be surprised twice more.
    """
    periods = index.get(feed_id, {})
    return [
        Blocker(
            reason=HoldReason.PRIOR_PERIOD_INCOMPLETE,
            feed_id=feed_id,
            business_date=period,
            batch_id=batch.batch_id,
            state=batch.state,
            chain=(feed_id,),
        )
        for period, batch in sorted(periods.items())
        if period < business_date and batch.state not in _SETTLED
    ]


def _upstream_blockers(
    *,
    feed_id: str,
    business_date: str,
    graph: DependencyGraph,
    index: dict[str, dict[str, BatchLike]],
    stages: Mapping[str, Sequence[StageLike]],
    suspended: frozenset[str],
    path: tuple[str, ...],
    visited: set[str],
) -> list[Blocker]:
    """Walk upstream until each branch settles or names a root cause.

    RECURSION, WITH THE PATH CARRIED. Claims waits on enrollment; enrollment
    waits on the reference load. Reporting only "waiting on enrollment" sends
    an operator to a feed that is itself blameless, so the blocker returned
    names the reference load and carries the path that reached it.
    """
    blockers: list[Blocker] = []
    for parent in graph.upstream_of(feed_id):
        if parent in visited:  # pragma: no cover - the graph is acyclic
            continue
        here = (*path, parent)
        batch = index.get(parent, {}).get(business_date)

        if parent in suspended:
            blockers.append(
                Blocker(
                    reason=HoldReason.UPSTREAM_HELD,
                    feed_id=parent,
                    business_date=business_date,
                    chain=here,
                )
            )
            continue

        if batch is None:
            # Nothing has arrived for the upstream. Before calling that a
            # missing file, ask whether the upstream is ITSELF waiting on
            # something — "claims is waiting because enrollment is waiting
            # because reference data failed" is one sentence, and the operator
            # needs the last clause.
            inherited = _upstream_blockers(
                feed_id=parent,
                business_date=business_date,
                graph=graph,
                index=index,
                stages=stages,
                suspended=suspended,
                path=here,
                visited=visited | {parent},
            )
            blockers.extend(
                inherited
                or [
                    Blocker(
                        reason=HoldReason.UPSTREAM_NOT_ARRIVED,
                        feed_id=parent,
                        business_date=business_date,
                        chain=here,
                    )
                ]
            )
            continue

        if batch.state in _SETTLED:
            continue

        blockers.append(
            Blocker(
                reason=(
                    HoldReason.UPSTREAM_FAILED
                    if batch.state in _BROKEN
                    else HoldReason.UPSTREAM_IN_PROGRESS
                ),
                feed_id=parent,
                business_date=business_date,
                batch_id=batch.batch_id,
                state=batch.state,
                layer=_failed_layer(stages.get(batch.batch_id, ())),
                chain=here,
            )
        )
    return blockers


def _failed_layer(stages: Sequence[StageLike]) -> Layer | None:
    """Where a batch stopped. The FIRST failing stage, in spine order.

    First rather than last: a failure at Bronze leaves Silver Raw untouched,
    and reporting the furthest stage recorded would name a layer the data never
    reached.
    """
    failed = [s for s in stages if s.state in _BROKEN]
    if not failed:
        return None
    spine = list(Layer)
    return min(failed, key=lambda s: spine.index(s.stage)).stage


# ── notifying once ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class HoldNotice:
    """One message about one hold, ready for the notification pin.

        "Operations is notified once (not per held run)." — CF-V1-E8-03

    THE IDEMPOTENCY IS THE PRODUCER'S, and it is already built. `queue.message`
    carries a unique `dedupe_key` and "a repeated dedupe_key returns the
    existing message" — so a schedule that fires hourly against the same broken
    upstream enqueues the same key twenty-four times and one message exists.

    That is deliberately not a "have we alerted yet?" flag on the feed. A flag
    needs clearing, and the thing that clears it is the thing that gets
    forgotten; a dedupe key needs nothing, because when the hold's root cause
    genuinely changes the key changes with it and the second message is the one
    an operator should get.
    """

    feed_id: str
    business_date: str
    dedupe_key: str
    subject: str
    body: str
    status_word: StatusWord
    blocking_batch_ids: tuple[str, ...] = ()

    @property
    def topic(self) -> str:
        return "scheduling.hold"


def notice_for(decision: ReleaseDecision) -> HoldNotice | None:
    """The message a held run should send, or None when nobody needs telling.

    Returns None for a hold that clears itself. A team paged because last
    month's batch is still loading learns to ignore the channel that also
    carries the failures, and then the platform has spent its one alerting
    budget on noise.
    """
    if decision.may_run or not decision.needs_notification:
        return None
    roots = decision.root_causes
    return HoldNotice(
        feed_id=decision.feed_id,
        business_date=decision.business_date,
        dedupe_key=decision.notice_key,
        subject=(
            f"{decision.feed_id} is holding {decision.business_date} — "
            f"{roots[0].feed_id} {roots[0].reason.value.replace('_', ' ')}"
        ),
        body=decision.explain(),
        status_word=decision.status_word,
        blocking_batch_ids=tuple(b.batch_id for b in roots if b.batch_id),
    )


def recovered(previous: ReleaseDecision, current: ReleaseDecision) -> bool:
    """Did a hold clear between two evaluations?

    Not needed to RELEASE anything — recomputation does that on its own — but
    needed to say so. "Everything resumes when enrollment recovers" is only
    visible to an operator who was told the hold existed if they are also told
    it ended, and a channel that reports problems and never their resolution
    trains people to go and check manually anyway.
    """
    return not previous.may_run and current.may_run


# ── the dependency picture, for the operations screens ───────────────────────
@dataclass(frozen=True)
class PictureNode:
    """One feed in the picture, with the state of the period being explained."""

    feed_id: str
    business_date: str
    batch_id: str | None
    state: BatchState | None
    status_word: StatusWord
    is_subject: bool = False
    is_root_cause: bool = False


@dataclass(frozen=True)
class DependencyPicture:
    """What Operations sees beside a held run.

        "Show the dependency picture on the operations screens so a hold is
         self-explanatory." — CF-V1-E8-03

    A structure, not a rendering. `core/` says which feeds are in the picture,
    which one is the subject and which one is to blame; the UI decides whether
    that is a graph, a list or a breadcrumb. The same split
    `core.persona` makes for home slots, for the same reason: what is drawn is
    a product fact, how it is drawn is not.
    """

    subject: str
    business_date: str
    nodes: tuple[PictureNode, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()
    decision: ReleaseDecision | None = None
    #: Everything that would stop if the subject failed. Shown even when the
    #: subject is healthy — an engineer editing a feed's schedule needs to know
    #: what waits on it BEFORE they change it, not afterwards.
    blast_radius: tuple[str, ...] = ()

    @property
    def is_self_explanatory(self) -> bool:
        """Every held picture names at least one root cause node.

        Asserted by a test rather than trusted: a picture that shows a hold and
        no cause is the screen that sends an operator to ask an engineer, which
        is the workflow this whole epic exists to end.
        """
        if self.decision is None or self.decision.may_run:
            return True
        return any(node.is_root_cause for node in self.nodes)


def picture(
    *,
    feed_id: str,
    business_date: str,
    graph: DependencyGraph,
    batches: Sequence[BatchLike],
    decision: ReleaseDecision | None = None,
    stages: Mapping[str, Sequence[StageLike]] | None = None,
    suspended: frozenset[str] = frozenset(),
) -> DependencyPicture:
    """Assemble the picture for one feed and one period."""
    resolved = decision or decide(
        feed_id=feed_id,
        business_date=business_date,
        graph=graph,
        batches=batches,
        stages=stages,
        suspended=suspended,
    )
    index = _index(batches)
    causes = {b.feed_id for b in resolved.root_causes}

    involved: list[str] = [feed_id]
    for blocker in resolved.blockers:
        for step in blocker.chain:
            if step not in involved:
                involved.append(step)
    for parent in graph.upstream_of(feed_id):
        if parent not in involved:
            involved.append(parent)

    nodes = tuple(
        _node(
            name,
            business_date,
            index.get(name, {}).get(business_date),
            is_subject=name == feed_id,
            is_root_cause=name in causes,
        )
        for name in involved
    )
    edges = tuple(
        (parent, child)
        for child in involved
        for parent in graph.upstream_of(child)
        if parent in involved
    )
    return DependencyPicture(
        subject=feed_id,
        business_date=business_date,
        nodes=nodes,
        edges=edges,
        decision=resolved,
        blast_radius=graph.blast_radius(feed_id),
    )


def _node(
    feed_id: str,
    business_date: str,
    batch: BatchLike | None,
    *,
    is_subject: bool,
    is_root_cause: bool,
) -> PictureNode:
    """One node, whether or not a batch exists for the period.

    A feed with NO batch is `Expected`, not absent from the picture: an
    operator looking at a hold needs to see the upstream that never arrived,
    and a node that vanishes when it matters most is worse than no picture.
    """
    return PictureNode(
        feed_id=feed_id,
        business_date=business_date,
        batch_id=batch.batch_id if batch else None,
        state=batch.state if batch else None,
        status_word=batch.state.status_word if batch else StatusWord.EXPECTED,
        is_subject=is_subject,
        is_root_cause=is_root_cause,
    )
