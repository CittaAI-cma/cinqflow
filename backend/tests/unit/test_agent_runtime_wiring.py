"""`agent_runtime_from` — the profile actually controls which runtime executes.

Before this function existed, `agent_runtime: {adapter: langgraph}` in a
profile had no effect: `intelligence.plane` constructed `InProcAgentRuntime()`
by hardcoded import at every call site. This certifies the fix the same way
`phi_scrub_from`/`vector_from` are already certified — one function, one
profile in, one adapter out, an unknown adapter a named refusal.
"""

from __future__ import annotations

from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.core.model.profile import ProfileError
from cinqflow.installer.profile import load
from cinqflow.intelligence.plane import agent_runtime_from

PROFILES = Path(__file__).parent.parent.parent / "profiles"
_LANGGRAPH_INSTALLED = find_spec("langgraph") is not None

pytestmark = pytest.mark.unit


def _with_agent_runtime(adapter: str):
    profile = load(PROFILES / "local.yaml")
    return replace(profile, pins={**profile.pins, "agent_runtime": {"adapter": adapter}})


def test_inproc_is_what_every_shipped_profile_fits() -> None:
    assert isinstance(agent_runtime_from(load(PROFILES / "local.yaml")), InProcAgentRuntime)


@pytest.mark.skipif(not _LANGGRAPH_INSTALLED, reason="langgraph extra not installed")
def test_langgraph_is_reachable_once_a_profile_actually_names_it() -> None:
    from cinqflow.adapters.langgraph import LangGraphAgentRuntime

    assert isinstance(agent_runtime_from(_with_agent_runtime("langgraph")), LangGraphAgentRuntime)


@pytest.mark.skipif(_LANGGRAPH_INSTALLED, reason="only meaningful when the extra is ABSENT")
def test_langgraph_without_the_extra_installed_is_a_named_refusal() -> None:
    with pytest.raises(ProfileError, match="requirements/agents.txt"):
        agent_runtime_from(_with_agent_runtime("langgraph"))


def test_an_unknown_adapter_is_a_named_refusal() -> None:
    with pytest.raises(ProfileError, match="agent_runtime"):
        agent_runtime_from(_with_agent_runtime("nonsense"))
