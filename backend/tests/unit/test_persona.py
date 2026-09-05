"""`auth/persona.py`: the one role -> persona / capability mapping (plan §14, D1)."""

from __future__ import annotations

import pytest

from cinqflow.auth.ddl import ROLES
from cinqflow.auth.persona import (
    ANALYST_ROLES,
    GATE_ROLES,
    PLATFORM_ROLES,
    capabilities_for,
    persona_for,
)


def test_every_seeded_role_is_assigned_to_exactly_one_side():
    seeded = {name for name, _ in ROLES}
    assert seeded == PLATFORM_ROLES | ANALYST_ROLES
    assert not (PLATFORM_ROLES & ANALYST_ROLES)


@pytest.mark.parametrize(
    ("roles", "persona"),
    [
        (["business_analyst"], "data_analyst"),
        (["approver"], "data_analyst"),
        (["data_steward"], "data_analyst"),
        (["read_only"], "data_analyst"),
        (["data_engineer"], "data_platform"),
        (["operations"], "data_platform"),
        (["administrator"], "data_platform"),
        # Precedence: any platform role wins.
        (["approver", "data_engineer"], "data_platform"),
        (["business_analyst", "administrator"], "data_platform"),
        # No roles, or only unknown ones: view-only analyst - the safe default.
        ([], "data_analyst"),
        (["not_a_role"], "data_analyst"),
    ],
)
def test_persona_for(roles, persona):
    assert persona_for(roles) == persona


def test_gate_roles_are_a_subset_of_analyst_roles():
    assert GATE_ROLES <= ANALYST_ROLES


@pytest.mark.parametrize(
    ("roles", "decide", "rerun", "manage"),
    [
        (["approver"], True, False, False),
        (["business_analyst"], True, False, False),
        (["data_steward"], False, False, False),
        (["read_only"], False, False, False),
        (["data_engineer"], False, True, False),
        (["operations"], False, True, False),
        # An administrator runs the system, not the data: no gate on its own.
        (["administrator"], False, True, True),
        # ...unless it also holds a gate role.
        (["administrator", "approver"], True, True, True),
        ([], False, False, False),
    ],
)
def test_capabilities_for(roles, decide, rerun, manage):
    caps = capabilities_for(roles)
    assert caps.can_decide_gates is decide
    assert caps.can_rerun_steps is rerun
    assert caps.can_manage_users is manage
