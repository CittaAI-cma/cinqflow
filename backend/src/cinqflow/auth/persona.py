"""Persona and capabilities, derived from roles - the one mapping, owned here.

Two ideas, kept deliberately apart (docs/blueprints/Analyst_worflow_and_DAGs/
04_validation_and_implementation_plan.md §14):

- **Persona is emphasis.** It picks defaults - which reading mode a review opens
  in, which proposal filter, which home page. Two personas: the Data Analyst
  (onboards a file and signs the gates) and the Data Platform (keeps the
  workflow running and re-runs what failed). Strictly role-derived, no switcher
  (decision D2).
- **Capability is authority.** Whether a caller may *decide a gate* or *re-run a
  step* is a role predicate the API enforces (`require_capability`), independent
  of persona. An administrator who also holds `approver` can approve; a
  `data_steward` can review but not decide - and the screen says so.

Precedence for a user holding roles from both sides: any platform role wins. A
user with no roles at all is a Data Analyst with no capabilities - view only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

Persona = Literal["data_analyst", "data_platform"]

#: Decision D1. Everything not listed here is an analyst role (or unknown, which
#: also reads as analyst with no capabilities - the safe default).
PLATFORM_ROLES: frozenset[str] = frozenset({"data_engineer", "operations", "administrator"})
ANALYST_ROLES: frozenset[str] = frozenset(
    {"business_analyst", "approver", "data_steward", "read_only"}
)

#: May decide G1 and G2. `data_steward` owns the knowledge plane, not the gate;
#: `administrator` runs the system, not the data - neither signs a gate unless it
#: also holds one of these (forward-flow-adoption.md §7.1).
GATE_ROLES: frozenset[str] = frozenset({"business_analyst", "approver"})

#: May retry / re-run a workflow step (`/retry` today; `/steps/{step}/rerun` from
#: PR-3). Re-running is an operational act on the platform, hence the platform set.
RERUN_ROLES: frozenset[str] = PLATFORM_ROLES


class Capabilities(BaseModel):
    """What this caller may *do*. Rendered by the UI as affordances, enforced by
    the API as 403s - the UI mirrors, the API decides."""

    can_decide_gates: bool
    can_rerun_steps: bool
    can_manage_users: bool


def persona_for(roles: Iterable[str]) -> Persona:
    held = set(roles)
    return "data_platform" if held & PLATFORM_ROLES else "data_analyst"


def capabilities_for(roles: Iterable[str]) -> Capabilities:
    held = set(roles)
    return Capabilities(
        can_decide_gates=bool(held & GATE_ROLES),
        can_rerun_steps=bool(held & RERUN_ROLES),
        can_manage_users="administrator" in held,
    )
