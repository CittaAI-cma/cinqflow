"""CF-V3-E10-02 — the model contract browser joins `navigation.DESTINATIONS`
at `wave=3`, same discipline as `identity-coverage` before it (CF-V3-E9-04):
declared and reachable by URL, invisible to `active()`/`for_roles()` until a
later, deliberate bump of `ACTIVE_WAVE`.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.identity import Role
from cinqflow.core.navigation import ACTIVE_WAVE, DESTINATIONS, NavGroup, active, for_roles
from cinqflow.core.security import Action

pytestmark = pytest.mark.unit


def _destination(key: str):
    return next(d for d in DESTINATIONS if d.key == key)


def test_active_wave_was_not_moved_by_this_story() -> None:
    assert ACTIVE_WAVE == 0


def test_ods_model_destination_is_declared_correctly() -> None:
    destination = _destination("ods-model")
    assert destination.route == "/data/ods-model"
    assert destination.group is NavGroup.DATA
    assert destination.wave == 3
    assert destination.requires is Action.VIEW
    assert {Role.BUSINESS_ANALYST, Role.DATA_STEWARD} <= destination.prominent_for
    assert destination.answers.strip()


def test_ods_model_destination_stays_hidden_while_active_wave_is_0() -> None:
    assert "ods-model" not in {d.key for d in active()}

    everything_permitted = frozenset(Action)
    for role in Role:
        ranked_keys = {d.key for d in for_roles(frozenset({role}), everything_permitted)}
        assert "ods-model" not in ranked_keys, role


def test_ods_model_destination_exists_in_the_data_even_though_hidden() -> None:
    assert "ods-model" in {d.key for d in DESTINATIONS}
