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

import pytest

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
    from cinqflow.installer.profile import load
    from cinqflow.intelligence.wiring import llm_from

    yield llm_from(load("profiles/local.yaml"), DotenvSecrets())
