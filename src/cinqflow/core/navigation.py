"""The wave-activation manifest — the navigation, as data.

    "Eight destinations in Wave 0. W1+ destinations are HIDDEN until their wave
     activates — never stubbed, never an empty screen. Nav is generated from a
     wave-activation manifest so this cannot drift."
    — the Wave-0 plan, §2

    "Persona shapes the home and the ranking. It never shapes the vocabulary or
     the depth."
    — ADR-0020, the merge rule

Why this lives in core: which destinations exist in a wave is a PRODUCT fact,
not a frontend detail. Putting it here means the UI, the API and the tests read
one list — and a Wave-1 screen cannot appear early because someone added a
route file.

An empty stub is worse than a hidden destination. A stub teaches a user that
the platform is broken; a hidden destination teaches them nothing at all, which
is correct, because there is nothing to learn yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.model.identity import Role
from cinqflow.core.security import Action

#: Which wave this build has activated. Raising it reveals destinations; it
#: does not make them work. One line, one place.
ACTIVE_WAVE = 0


@unique
class NavGroup(StrEnum):
    OVERVIEW = "Overview"
    DATA = "Data"
    OPERATIONS = "Operations"
    AI = "AI"
    ADMIN = "Admin"


@dataclass(frozen=True)
class Destination:
    """One place a person can go, and what it answers.

    `answers` is not decoration — it is the subtitle the nav renders, and a
    destination nobody can write a one-line answer for is a destination that
    has not been designed.
    """

    key: str
    label: str
    route: str
    group: NavGroup
    answers: str
    wave: int = 0
    requires: Action = Action.VIEW
    #: Roles this destination is RANKED FIRST for. Ranking, never gating —
    #: everyone with the permission sees the same destinations in the same
    #: words. Persona shapes the home, not the vocabulary.
    prominent_for: frozenset[Role] = frozenset()

    @property
    def active(self) -> bool:
        return self.wave <= ACTIVE_WAVE


DESTINATIONS: tuple[Destination, ...] = (
    Destination(
        key="home",
        label="Home",
        route="/",
        group=NavGroup.OVERVIEW,
        answers="What needs me, ranked by downstream harm.",
        prominent_for=frozenset(Role),
    ),
    Destination(
        key="intake",
        label="Data Intake",
        route="/data/intake",
        group=NavGroup.DATA,
        answers="What feeds exist, what should arrive, what arrived, what is Missing.",
        prominent_for=frozenset({Role.ENGINEER}),
    ),
    Destination(
        key="explorer",
        label="Data Explorer",
        route="/data/explorer",
        group=NavGroup.DATA,
        answers="What data do we have, and where is it.",
        prominent_for=frozenset({Role.READ_ONLY}),
    ),
    Destination(
        key="mapping",
        label="Mapping & Rules",
        route="/data/mapping",
        group=NavGroup.DATA,
        answers="How source columns become the canonical model.",
        wave=1,
    ),
    Destination(
        key="quality",
        label="Data Quality",
        route="/data/quality",
        group=NavGroup.DATA,
        answers="Which rules fire, how often, and what they cost.",
        wave=1,
    ),
    Destination(
        key="monitor",
        label="Monitor",
        route="/operations/monitor",
        group=NavGroup.OPERATIONS,
        answers="Expected versus actual, per feed, per cycle.",
        prominent_for=frozenset({Role.ENGINEER, Role.READ_ONLY}),
    ),
    Destination(
        key="control",
        label="Control Operations",
        route="/operations/control",
        group=NavGroup.OPERATIONS,
        answers="Stages, inputs, errors, quarantine and reconciliation for a run.",
        prominent_for=frozenset({Role.ENGINEER}),
    ),
    Destination(
        key="work-queue",
        label="Work Queue",
        route="/operations/queue",
        group=NavGroup.OPERATIONS,
        answers="What a person must decide, oldest first.",
        wave=1,
    ),
    Destination(
        key="lineage",
        label="Data Lineage",
        route="/operations/lineage",
        group=NavGroup.OPERATIONS,
        answers="Where a column came from and what depends on it.",
        wave=2,
    ),
    Destination(
        key="ask",
        label="Ask CINQFLOW",
        route="/ai/ask",
        group=NavGroup.AI,
        answers="Explain this feed, this plan, this run — every claim cited.",
        requires=Action.ASK_AGENT,
        prominent_for=frozenset({Role.READ_ONLY}),
    ),
    Destination(
        key="llm-observability",
        label="LLM Observability",
        route="/ai/observability",
        group=NavGroup.AI,
        answers="Every agent run, its cost against cap, its grounding, and its refusals.",
    ),
    Destination(
        key="users",
        label="Users & Roles",
        route="/admin/users",
        group=NavGroup.ADMIN,
        answers="Who can do what.",
        requires=Action.MANAGE_USERS,
        prominent_for=frozenset({Role.ADMINISTRATOR}),
    ),
    Destination(
        key="audit",
        label="Audit Trail",
        route="/admin/audit",
        group=NavGroup.ADMIN,
        answers="Who changed what, and who was refused.",
        prominent_for=frozenset({Role.ADMINISTRATOR}),
    ),
)


def active() -> tuple[Destination, ...]:
    return tuple(d for d in DESTINATIONS if d.active)


def for_roles(roles: frozenset[Role], permitted: frozenset[Action]) -> tuple[Destination, ...]:
    """What this person sees, ranked for them.

    Ranking is stable within a group: prominence lifts a destination inside its
    group, it does not reshuffle the information architecture. Two people
    looking at the same screen must be able to give each other directions.
    """
    visible = [d for d in active() if d.requires in permitted]
    return tuple(
        sorted(
            visible,
            key=lambda d: (
                list(NavGroup).index(d.group),
                0 if d.prominent_for & roles else 1,
                d.label,
            ),
        )
    )
