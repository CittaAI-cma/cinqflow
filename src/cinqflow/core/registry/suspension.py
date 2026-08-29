"""CF-V1-E3-04 — pausing a feed, without pretending it is a lifecycle state.

    "Feed status lifecycle (Draft→Active→Paused→Retired) + version history +
     linked specs/runbooks"
    — CF-V1-E3-04

    "illegal transitions refused · side-by-side version diff · pause stops new
     work, in-flight finishes safely"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

THE DESIGN DECISION THIS MODULE EXISTS TO MAKE, and it is the one place this
story could have gone wrong:

    PAUSED IS NOT A LIFECYCLE STATE. IT IS A SECOND AXIS.

The story says Draft -> Active -> Paused -> Retired, and the obvious reading is
to add PAUSED to `LifecycleState`. ADR-0006 says there is ONE state machine and
no object type may opt out of it — and adding a state to it is exactly what
that rule is for. But the deeper reason is what it would cost:

  • Un-pausing would be a lifecycle transition, and every lifecycle transition
    into an executable state needs a named approver. So resuming a feed at 3am
    during an incident would require finding a steward.
  • A paused feed would stop being PUBLISHED, so "which version was live in
    March" would answer "none" for every week anybody paused anything.
  • The audit trail would record a pause as a governance act — indistinguishable
    from an approval being withdrawn, which is a completely different event.

So PUBLISHED is the story's "Active": the lifecycle fact that this
configuration is approved and executable. A pause is an OPERATIONAL fact — do
not start new work — carried alongside it. An operator can set and lift it
without re-approval, and every set and lift is a row.

APPEND-ONLY, LIKE THE AUDIT. There is no `update_suspension` verb anywhere:
pausing appends a row and resuming appends another, and the current state is
the newest row. "Was this feed paused on the 3rd?" is then the same kind of
question as "which version was live in March", answerable from what is stored
rather than from what somebody remembers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType


class SuspensionError(RuntimeError):
    """A pause or resume the platform will not record."""


@unique
class SuspensionAction(StrEnum):
    """Two acts, and the ledger holds both. `RESUMED` is a row rather than a
    deletion, because a feed that was paused for six days and a feed that was
    never paused must not look identical afterwards."""

    PAUSED = "paused"
    RESUMED = "resumed"


@dataclass(frozen=True)
class SuspensionEvent:
    """One pause or resume. Append-only; there is no update path.

    `resumes_after` is optional but strongly preferred, and the reason is
    operational rather than technical: a pause with an end lifts itself, and a
    pause without one is lifted when somebody remembers. The incumbent
    platform's longest outage was a feed paused "for an hour" during a payer
    migration and unpaused eleven days later by somebody looking for something
    else.
    """

    feed_id: str
    action: SuspensionAction
    actor: Actor
    occurred_ts: datetime
    reason: str = ""
    resumes_after: datetime | None = None

    def __post_init__(self) -> None:
        if not self.feed_id.strip():
            raise SuspensionError("a suspension without a feed is about nothing")
        if self.action is SuspensionAction.PAUSED and not self.reason.strip():
            raise SuspensionError(
                f"{self.feed_id}: pausing a feed needs a reason. Somebody will find this "
                "paused next week and have to decide whether to lift it — and an "
                "unexplained pause is one nobody dares touch."
            )
        if self.actor.actor_type is not ActorType.HUMAN:
            raise SuspensionError(
                f"{self.actor.subject} is a {self.actor.actor_type.value} actor. Stopping "
                "or restarting a feed is an operator's decision with a name on it — "
                "agents propose, humans dispose."
            )
        if (
            self.resumes_after is not None
            and self.action is SuspensionAction.PAUSED
            and self.resumes_after <= self.occurred_ts
        ):
            raise SuspensionError(
                f"{self.feed_id}: a pause that has already expired pauses nothing"
            )


@dataclass(frozen=True)
class Suspension:
    """Whether a feed is paused RIGHT NOW, computed from the newest event.

    A value object rather than a stored flag: the ledger is the truth, and
    a second stored field saying "is_paused" is a second thing to keep in
    step with it.
    """

    feed_id: str
    is_paused: bool = False
    reason: str = ""
    paused_by: Actor | None = None
    paused_ts: datetime | None = None
    resumes_after: datetime | None = None
    #: Declared rather than left implicit, because it is the half of the
    #: acceptance criterion that is easy to get wrong. A pause that killed
    #: in-flight batches would leave half-loaded silver tables and a
    #: reconciliation that cannot balance.
    affects_work_already_running: bool = False

    def is_active_at(self, now: datetime) -> bool:
        """Whether the pause still applies.

        A timed pause LIFTS ITSELF — computed here rather than by a background
        job, so a feed paused until Monday resumes on Monday even if nothing
        was running over the weekend to notice. A job that has to run for a
        pause to end is a job whose failure silently extends an outage.
        """
        if not self.is_paused:
            return False
        return self.resumes_after is None or now < self.resumes_after

    def may_start_new_work(self, now: datetime) -> bool:
        return not self.is_active_at(now)

    def explain(self, now: datetime) -> str:
        """What an operator reads when the platform declines to start a run."""
        if not self.is_active_at(now):
            return f"{self.feed_id} is not paused."
        who = (
            self.paused_by.display_name or self.paused_by.subject if self.paused_by else "somebody"
        )
        until = (
            f" until {self.resumes_after.isoformat()}"
            if self.resumes_after is not None
            else " with no end date"
        )
        return (
            f"{self.feed_id} was paused by {who}{until}: {self.reason}\n"
            "No new batch will start. Any batch already running will finish — pausing "
            "stops new work, it does not abandon work in progress."
        )


def current(feed_id: str, events: tuple[SuspensionEvent, ...]) -> Suspension:
    """Fold the ledger into the present. Newest event wins.

    Takes the whole ledger rather than "the latest row" so the function is
    total: an empty ledger is a feed that has never been paused, which is a
    perfectly ordinary answer and not a missing one.
    """
    for event in sorted(events, key=lambda e: e.occurred_ts, reverse=True):
        if event.feed_id != feed_id:
            continue
        if event.action is SuspensionAction.RESUMED:
            return Suspension(feed_id=feed_id, is_paused=False)
        return Suspension(
            feed_id=feed_id,
            is_paused=True,
            reason=event.reason,
            paused_by=event.actor,
            paused_ts=event.occurred_ts,
            resumes_after=event.resumes_after,
        )
    return Suspension(feed_id=feed_id)


def pause(
    feed_id: str,
    *,
    actor: Actor,
    reason: str,
    now: datetime,
    resumes_after: datetime | None = None,
) -> SuspensionEvent:
    return SuspensionEvent(
        feed_id=feed_id,
        action=SuspensionAction.PAUSED,
        actor=actor,
        occurred_ts=now,
        reason=reason,
        resumes_after=resumes_after,
    )


def resume(feed_id: str, *, actor: Actor, now: datetime, reason: str = "") -> SuspensionEvent:
    """Resuming needs no reason and no approver.

    Deliberately asymmetric with `pause`. Requiring a justification to START a
    feed again would make the safe direction the expensive one — and an
    operator at 3am with a payer on the phone should be able to turn the tap
    back on and explain afterwards. The row records who did it, which is what
    accountability actually needs.
    """
    return SuspensionEvent(
        feed_id=feed_id,
        action=SuspensionAction.RESUMED,
        actor=actor,
        occurred_ts=now,
        reason=reason,
    )
