"""Connection profiles — the one place environment difference lives."""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.core.model.vocabulary import Mode
from cinqflow.installer.profile import ProfileError, load

PROFILES = Path(__file__).parent.parent.parent / "profiles"

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["mock.yaml", "local.yaml", "ci.yaml"])
def test_every_shipped_profile_loads(name: str) -> None:
    profile = load(PROFILES / name)
    assert profile.mode in set(Mode)
    assert profile.socket


def test_every_profile_addresses_all_twenty_pins() -> None:
    """ "An unmentioned pin is a pin nobody decided about."

    Silence in a profile reads as a default, and a default that nobody chose is
    how an environment ends up with a capability no one authorised.
    """
    from cinqflow.ports import PORTS

    for name in ("mock.yaml", "local.yaml", "ci.yaml"):
        assert set(load(PROFILES / name).pins) == set(PORTS), name


def test_the_mock_profile_declares_no_reachable_endpoint() -> None:
    """Lanes 1 and 2 hold no live credentials, so rung 0 must not be able to
    reach anything at all — the LLM pin names the registered "mock" adapter
    (ScriptedLlm), never "openai-compatible" or "cassette"."""
    profile = load(PROFILES / "mock.yaml")
    assert profile.adapter_for("llm") == "mock"
    assert profile.rung == 0


def test_the_wave_0_agent_holds_no_write_tool_in_any_profile() -> None:
    """ "Hold any write tool on its whitelist, in any environment, at any
    confidence" is a documented don't for CF-V0-E16-10."""
    for name in ("mock.yaml", "local.yaml", "ci.yaml"):
        agent = load(PROFILES / name).agents["pipeline_insight"]
        assert agent["risk_class"] == "R0"
        assert agent["tool_whitelist"] == "read_only"


def test_an_unknown_pin_is_refused(tmp_path: Path) -> None:
    """A 21st pin is a plate change, not a profile edit."""
    written = _write(tmp_path, extra_pin="telepathy")
    with pytest.raises(ProfileError, match="not pins"):
        load(written)


def test_a_missing_pin_is_refused(tmp_path: Path) -> None:
    written = _write(tmp_path, drop_pin="storage")
    with pytest.raises(ProfileError, match="no configuration for storage"):
        load(written)


@pytest.mark.parametrize(
    "credential",
    [
        "sk-live-a9f3c2e18b7d4a6f90c15e2b3d8a7f41",
        "postgresql://cinqflow:hunter2@db.internal:5432/cinqflow",
        "AccountKey=abc123def456==",
    ],
)
def test_a_literal_credential_in_a_profile_is_refused(tmp_path: Path, credential: str) -> None:
    """This is the check that stops a key reaching git.

    Everything secret is a `secret://name` reference; naming a secret is not
    holding one.
    """
    written = _write(tmp_path, api_key=credential)
    with pytest.raises(ProfileError, match="looks like a credential"):
        load(written)


def test_a_secret_reference_is_accepted(tmp_path: Path) -> None:
    assert load(_write(tmp_path, api_key="secret://llm-key")).rung == 0.5


def test_an_invalid_mode_is_refused(tmp_path: Path) -> None:
    """ "partial permission is a mode, not a failure" — but only these three."""
    written = _write(tmp_path, mode="mostly_on")
    with pytest.raises(ProfileError, match="mode must be one of"):
        load(written)


def _write(
    tmp_path: Path,
    *,
    api_key: str = "secret://llm-key",
    mode: str = "full",
    extra_pin: str | None = None,
    drop_pin: str | None = None,
) -> Path:
    from cinqflow.ports import PORTS

    pins = {name: {"adapter": "mock"} for name in PORTS}
    pins["llm"] = {"adapter": "scripted", "api_key": api_key}
    if drop_pin:
        pins.pop(drop_pin)
    if extra_pin:
        pins[extra_pin] = {"adapter": "mock"}

    import yaml

    path = tmp_path / "probe.yaml"
    path.write_text(
        yaml.safe_dump(
            {"profile_version": 1, "rung": 0.5, "socket": "probe", "mode": mode, "pins": pins}
        )
    )
    return path
