"""W2-33 · CF-V2-E12-03/E13-03 — the two new Wave-2 destinations, as data.

    "Nine destinations in Wave 0. W1+ destinations are HIDDEN until their wave
     activates — never stubbed, never an empty screen."
    — core/navigation.py's own docstring

`incidents` and `certification` join `navigation.DESTINATIONS` at `wave=2`,
same as `lineage` before them. The don't this file exists to test: raising
`ACTIVE_WAVE` is a decision for a later commit, not a side effect of adding a
destination — so both must exist in the data (reachable by URL, testable) and
both must stay INVISIBLE to `active()` and `for_roles()` while `ACTIVE_WAVE`
is still 0.
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
    """The bump that reveals Wave 2 is a deliberate, separate decision —
    not something adding a destination should ever do quietly."""
    assert ACTIVE_WAVE == 0


def test_incidents_destination_is_declared_correctly() -> None:
    incidents = _destination("incidents")
    assert incidents.route == "/operations/incidents"
    assert incidents.group is NavGroup.OPERATIONS
    assert incidents.wave == 2
    assert incidents.requires is Action.VIEW
    assert Role.OPERATIONS in incidents.prominent_for
    # "a destination nobody can write a one-line answer for is a destination
    # that has not been designed" — Destination.answers's own docstring.
    assert incidents.answers.strip()
    assert incidents.answers != ""


def test_certification_destination_is_declared_correctly() -> None:
    certification = _destination("certification")
    assert certification.route == "/operations/certification"
    assert certification.group is NavGroup.OPERATIONS
    assert certification.wave == 2
    assert certification.requires is Action.VIEW
    assert {Role.OPERATIONS, Role.DATA_STEWARD} <= certification.prominent_for
    assert certification.answers.strip()


def test_both_destinations_stay_hidden_while_active_wave_is_0() -> None:
    """The don't: a Wave-2 screen must not leak into the generated nav before
    its wave activates, for ANY role — this is not a permission question."""
    visible_keys = {d.key for d in active()}
    assert "incidents" not in visible_keys
    assert "certification" not in visible_keys

    everything_permitted = frozenset(Action)
    for role in Role:
        ranked_keys = {d.key for d in for_roles(frozenset({role}), everything_permitted)}
        assert "incidents" not in ranked_keys, role
        assert "certification" not in ranked_keys, role


def test_both_destinations_exist_in_the_data_even_though_hidden() -> None:
    """Reachable by URL and testable, per the slab's own instructions — an
    absent nav entry is not the same thing as an absent page."""
    keys = {d.key for d in DESTINATIONS}
    assert {"incidents", "certification"} <= keys
