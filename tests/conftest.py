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

CREDENTIAL_ENV_VARS = (
    "CINQFLOW_LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "CINQFLOW_LLM_ENDPOINT",
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


@pytest.fixture
def lane3_endpoint() -> str:
    """The only door to a real model. Skips — never silently passes — when shut."""
    endpoint = os.environ.get("CINQFLOW_LLM_ENDPOINT")
    if not endpoint:
        pytest.skip(
            "Lane 3 requires a real endpoint. No threshold may be claimed from "
            "Lane 1 (mock) or Lane 2 (replay), so this test skips rather than "
            "passing against a stand-in."
        )
    return endpoint
