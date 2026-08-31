"""W1-35 (F6) · CF-V1-E6-02 — the "agent-acceptance" destination, as data.

    "Nine destinations in Wave 0. W1+ destinations are HIDDEN until their wave
     activates — never stubbed, never an empty screen."
    — core/navigation.py's own docstring

Same discipline `test_navigation_wave2_destinations.py` already holds
`incidents` and `certification` to: the destination exists in the data —
reachable by URL, testable — at `wave=1`, alongside `mapping`, `quality` and
`work-queue`, and it stays invisible to `active()` and `for_roles()` while
`ACTIVE_WAVE` is still 0. Adding a destination is never the commit that bumps
the wave.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.identity import Role
from cinqflow.core.navigation import ACTIVE_WAVE, DESTINATIONS, NavGroup, active, for_roles
from cinqflow.core.security import Action

pytestmark = pytest.mark.unit


def _destination(key: str):
    return next(d for d in DESTINATIONS if d.key == key)


def test_active_wave_was_not_moved_by_this_slab() -> None:
    """The bump that reveals Wave 1 is a deliberate, separate decision — not
    something adding one more destination should ever do quietly."""
    assert ACTIVE_WAVE == 0


def test_agent_acceptance_destination_is_declared_correctly() -> None:
    destination = _destination("agent-acceptance")
    assert destination.route == "/ai/acceptance"
    assert destination.group is NavGroup.AI
    assert destination.wave == 1
    assert destination.requires is Action.VIEW
    assert {Role.BUSINESS_ANALYST, Role.DATA_STEWARD} <= destination.prominent_for
    # "a destination nobody can write a one-line answer for is a destination
    # that has not been designed" — Destination.answers's own docstring.
    assert destination.answers.strip()


def test_the_destination_stays_hidden_while_active_wave_is_0() -> None:
    """The don't: a Wave-1 screen must not leak into the generated nav before
    its wave activates, for ANY role — this is not a permission question."""
    assert "agent-acceptance" not in {d.key for d in active()}

    everything_permitted = frozenset(Action)
    for role in Role:
        ranked_keys = {d.key for d in for_roles(frozenset({role}), everything_permitted)}
        assert "agent-acceptance" not in ranked_keys, role


def test_the_destination_exists_in_the_data_even_though_hidden() -> None:
    """Reachable by URL and testable — an absent nav entry is not the same
    thing as an absent page."""
    assert "agent-acceptance" in {d.key for d in DESTINATIONS}
