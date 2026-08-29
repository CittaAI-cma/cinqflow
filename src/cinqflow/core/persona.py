"""What each persona sees first, and in what order — the home, as data.

    "Persona shapes the home and the ranking. It never shapes the vocabulary
     or the depth."
    — ADR-0020, the merge rule

Why this lives in core, next to `navigation.py`: which cards a Data Engineer
sees before a Read-Only analyst is a PRODUCT fact, not a frontend detail. Put
it in the UI and it becomes a `role === "engineer"` ternary that nobody can
audit, that no test covers, and that quietly grows a fourth branch nobody
remembers adding. That ternary is precisely what this module replaces.

The merge rule is a constraint on WHERE persona is allowed to appear, and this
module is what makes violating it hard rather than merely discouraged. Persona
may change three things and no fourth:

  · RANK        — which slots, in what order (here)
  · PROMINENCE  — which destination lifts inside its nav group (navigation.py)
  · AFFORDANCE  — which buttons exist, and the server refuses the same action
                  (core/security)

It may never change the words, the objects, or the depth. Everyone gets the
same seven status words and the same drawer.

WAVE 0 HAS THREE ROLES. Engineer, Read-Only and the administrator who assigns
them — `core/model/identity.Role`. The seven-role matrix (Business Analyst,
Data Steward, Operations, Approver) is CF-V4-E2-02. This table is deliberately
shaped so that story WIDENS it rather than replacing it, exactly as
`core/security._PERMITTED` is shaped. Inventing those four roles here to make
the table look complete would put personas in the UI that the platform cannot
authenticate, authorize or test.

An absent slot renders NOTHING. Never a stub, never a placeholder, never an
empty card that teaches a user the platform is broken — the same rule
`navigation.py` applies to Wave-1 destinations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.model.identity import Role
from cinqflow.core.navigation import ACTIVE_WAVE
from cinqflow.core.security import Action


@unique
class HomeSlot(StrEnum):
    """One card on a home screen, named for the QUESTION it answers.

    Named for the question rather than the widget, for the same reason
    `Action.RUN_PIPELINE` is not `POST /batches`: "what needs me" survives a
    redesign, "harm-ranked batch table" does not.
    """

    NEEDS_YOU = "needs-you"
    ARRIVED = "arrived"
    RUNS = "runs"
    FEEDS = "feeds"
    ASK_SHORTCUT = "ask-shortcut"
    REFUSALS_TODAY = "refusals-today"
    ACCESS_CHANGES = "access-changes"

    # ---- Wave 1+. Declared, not rendered. ----
    PHASE1_OUTPUTS = "phase1-outputs"
    # Present so the slot vocabulary is reviewable as a whole, and so adding
    # them later is a wave bump rather than a home-screen rewrite.
    TRUST_TODAY = "trust-today"
    ANALYST_COST = "analyst-cost"
    AUTONOMY_NOW = "autonomy-now"
    AWAITING_DECISION = "awaiting-decision"


@dataclass(frozen=True)
class Slot:
    """A card, what it answers, and what it costs to show.

    `answers` is not decoration — a slot nobody can write a one-line answer for
    is a slot that has not been designed. Same discipline as
    `navigation.Destination.answers`.
    """

    key: HomeSlot
    answers: str
    wave: int = 0
    requires: Action = Action.VIEW

    @property
    def active(self) -> bool:
        return self.wave <= ACTIVE_WAVE


SLOTS: dict[HomeSlot, Slot] = {
    s.key: s
    for s in (
        Slot(HomeSlot.NEEDS_YOU, "What needs me, ranked by downstream harm."),
        Slot(HomeSlot.ARRIVED, "What arrived, most recent first."),
        Slot(HomeSlot.RUNS, "Every run in view, and its status."),
        Slot(HomeSlot.FEEDS, "What feeds exist and what state they are in."),
        Slot(HomeSlot.ASK_SHORTCUT, "Ask in my own words, without an engineer.",
             requires=Action.ASK_AGENT),
        Slot(HomeSlot.REFUSALS_TODAY, "What the platform refused, and why."),
        Slot(HomeSlot.ACCESS_CHANGES, "Who changed what, and who was denied."),
        # Wave 1+ — declared, inert until their wave activates.
        # The four executive outputs the programme owes the business: Acute
        # Event Command · Transition of Care · High Utilization · DQ &
        # Reconciliation. The charter is blunt about why this is a slot and
        # not a nice-to-have — "plumbing that never renders these has not
        # delivered" — and it cannot be built until the definitions behind it
        # have named stewards (open questions Q12, Q13).
        Slot(HomeSlot.PHASE1_OUTPUTS, "The four Phase-1 outputs I own, and their freshness.", wave=1),
        Slot(HomeSlot.TRUST_TODAY, "Can I trust today's data, by domain.", wave=1),
        Slot(HomeSlot.ANALYST_COST, "What this incident costs the analyst side.", wave=1),
        Slot(HomeSlot.AUTONOMY_NOW, "What the agent may do right now, and what it earned.", wave=1),
        Slot(HomeSlot.AWAITING_DECISION, "What a person must decide, oldest first.", wave=1),
    )
}


#: Rank per role. ORDER IS THE WHOLE POINT — this is the ranking half of the
#: merge rule, so the list is ordered, not a set.
#:
#: CF-V4-E2-02 adds BUSINESS_ANALYST, DATA_STEWARD, OPERATIONS and APPROVER
#: rows here. It does not change the shape, and it does not touch the UI.
_HOME: dict[Role, tuple[HomeSlot, ...]] = {
    # The engineer opens this screen to find the most expensive thing to
    # ignore. Harm first, inventory afterwards.
    Role.ENGINEER: (
        HomeSlot.NEEDS_YOU,
        HomeSlot.RUNS,
        HomeSlot.FEEDS,
        HomeSlot.ANALYST_COST,
        HomeSlot.AUTONOMY_NOW,
    ),
    # Read-Only cannot act on harm, so ranking by it would be a list of things
    # this person must go and ask somebody else about. They get what happened,
    # and the fastest route to a question.
    Role.READ_ONLY: (
        # When PHASE1_OUTPUTS activates it leads, and the page title moves
        # with it — the analyst's screen opens on the four outputs she owns,
        # which is what the ValueBridge DA Home concept shows. Wave-0 tests
        # asserting "What arrived" as her h1 are asserting a Wave-0 fact.
        HomeSlot.PHASE1_OUTPUTS,
        HomeSlot.ARRIVED,
        HomeSlot.ASK_SHORTCUT,
        HomeSlot.RUNS,
        HomeSlot.FEEDS,
        HomeSlot.TRUST_TODAY,
    ),
    # An administrator manages ACCESS, and cannot approve or operate. Their
    # home is governance evidence: what was refused, and what changed.
    Role.ADMINISTRATOR: (
        HomeSlot.REFUSALS_TODAY,
        HomeSlot.ACCESS_CHANGES,
        HomeSlot.RUNS,
    ),
}

#: Somebody with a role we have no home for still gets a working screen.
_FALLBACK: tuple[HomeSlot, ...] = (HomeSlot.RUNS, HomeSlot.FEEDS)


def home_for(roles: frozenset[Role], permitted: frozenset[Action]) -> tuple[Slot, ...]:
    """The ordered slots this person's home renders.

    Multi-role users get the union, in the order of their highest-ranked role,
    de-duplicated — never the same card twice, and never a card whose action
    the server would refuse.
    """
    ordered: list[HomeSlot] = []
    for role in sorted(roles, key=lambda r: r.value):
        for key in _HOME.get(role, ()):
            if key not in ordered:
                ordered.append(key)
    if not ordered:
        ordered = list(_FALLBACK)

    return tuple(
        SLOTS[key]
        for key in ordered
        if SLOTS[key].active and SLOTS[key].requires in permitted
    )
