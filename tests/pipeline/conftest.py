"""The rung-0.5 test harness — every test inside a rolled-back transaction.

    "Let every pipeline test run inside a rolled-back transaction, so thousands
     of tests finish in minutes with no cleanup code."
    — CF-V0-E8-07

    "Why Postgres makes the tests BETTER, not just cheaper: transactions give
     perfect isolation with no cleanup code; assertions are SQL row-level
     diffs; the balance equation is one query per stage; and a failing test
     leaves a database you can open and query."
    — memory/03-directives/02-testing-pyramid.md

The alternative — truncating between tests — is slower, order-dependent, and
destroys the evidence at exactly the moment someone needs it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from cinqflow.installer.profile import Profile, load

REPO = Path(__file__).parent.parent.parent


def _dsn_is_reachable(profile: Profile) -> str | None:
    from cinqflow.adapters.local.pg_control import resolve_dsn

    try:
        return resolve_dsn(profile)
    except KeyError:
        return None


@pytest.fixture(scope="session")
def pg_profile() -> Profile:
    """The rung-0.5 profile.

    Skips — never silently passes — when the plane is not provisioned. A
    pipeline test that quietly no-ops is worse than one that fails: it reports
    green for a platform that never processed a row.
    """
    profile = load(REPO / "profiles" / "local.yaml")
    if _dsn_is_reachable(profile) is None:
        pytest.skip(
            "rung 0.5 is not configured: set CINQFLOW_SECRET_PG_DSN (see .env.example), "
            "then `cinqflow install --profile profiles/local.yaml`"
        )
    return profile


@pytest.fixture
def plane(pg_profile: Profile) -> Iterator[object]:
    """A connection whose work is ALWAYS rolled back.

    No cleanup code anywhere in the pipeline suite is a direct consequence of
    this fixture, and it is why the suite can be run with `-n auto`.
    """
    import psycopg

    from cinqflow.adapters.local.pg_control import transaction

    try:
        with transaction(pg_profile) as connection:
            yield connection
    except psycopg.OperationalError as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"rung 0.5 unreachable: {exc}")


@pytest.fixture(scope="session", autouse=True)
def _load_dotenv() -> None:
    """Resolve `secret://name` references from .env at rungs 0.5 and 1.

    Key Vault resolves the SAME references at rung 3 — the reference format
    never changes, which is the whole reason that swap is cheap.
    """
    env_file = REPO / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
