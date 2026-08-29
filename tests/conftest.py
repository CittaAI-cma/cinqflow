"""Shared fixtures.

Two rules this file exists to enforce mechanically:

  "Lanes 1 and 2 hold no live credentials, so a misclassified test fails loudly"
  "no machinery test may require Lane 3 (real API)"
  — docs/architecture/INVARIANTS.md, testing

A Lane-1 or Lane-2 test that can see a real endpoint is not a mock test that
happens to pass; it is a quality claim made from the wrong lane. So the lane
markers scrub the credentials out of the environment rather than trusting the
test to behave.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from cinqflow.installer.profile import Profile, load

REPO = Path(__file__).parent.parent

# The dotenv adapter's convention is `secret://llm-key` -> CINQFLOW_SECRET_LLM_KEY,
# so these are the real variable names, not a parallel list that can drift.
CREDENTIAL_ENV_VARS = (
    "CINQFLOW_SECRET_LLM_KEY",
    "CINQFLOW_SECRET_LLM_ENDPOINT",
    "CINQFLOW_SECRET_LLM_MODEL_SMALL",
    "CINQFLOW_SECRET_LLM_MODEL_LARGE",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _lanes_1_and_2_hold_no_credentials(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Lanes 1 and 2 run credential-free, by removal rather than by convention."""
    if request.node.get_closest_marker("lane3"):
        yield
        return
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


#: What a Lane-3 run needs before it may claim anything.
LANE_3_REQUIREMENTS = (
    "CINQFLOW_SECRET_LLM_ENDPOINT",
    "CINQFLOW_SECRET_LLM_KEY",
    "CINQFLOW_SECRET_LLM_MODEL_SMALL",
    "CINQFLOW_SECRET_LLM_MODEL_LARGE",
)


@pytest.fixture
def lane3_llm() -> Iterator[object]:
    """The only door to a real model. SKIPS — never silently passes — when shut.

    Skipping is the honest outcome, and the message says exactly what is
    missing. A Lane-3 test that quietly fell back to the mock would report a
    green threshold measured against a stand-in, which is the single most
    misleading thing this repository could do.
    """
    absent = [name for name in LANE_3_REQUIREMENTS if not os.environ.get(name)]
    if absent:
        pytest.skip(
            "Lane 3 is not configured: "
            + ", ".join(absent)
            + " unset. No evaluation threshold may be claimed from Lane 1 (mock) or "
            "Lane 2 (replay), so this test skips rather than passing against a "
            "stand-in. Set them in .env (see .env.example) to run it."
        )

    from cinqflow.adapters.local.secrets import DotenvSecrets
    from cinqflow.intelligence.wiring import llm_from

    yield llm_from(load("profiles/local.yaml"), DotenvSecrets())


# ── the rung-0.5 Postgres plane — shared by every suite that needs it ────────
#
# Moved here (from tests/pipeline/conftest.py) so ANY contract suite — not
# only the pipeline suite — can run its Postgres-backed adapter through the
# SAME tests as its mock: "one contract suite, every adapter" applies to
# metadata_db and control_tables exactly as it applies to every other pin.


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

    No cleanup code anywhere in the pipeline or contract suites is a direct
    consequence of this fixture, and it is why they can be run with `-n auto`.
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
