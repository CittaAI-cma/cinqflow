"""Load and validate a connection profile.

    "all environment difference lives in the connection profile, nowhere else"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

This module is the enforcement point for the second half of that rule. It is
easy to write a profile schema; the useful part is REFUSING a profile that
carries a value instead of a reference, because that is how a credential ends
up in git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cinqflow.core.model.vocabulary import Mode
from cinqflow.ports import PORTS
from cinqflow.ports.secrets import is_reference

# Anything shaped like a secret must be a `secret://name` reference instead.
_CREDENTIAL_SHAPED = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|xox[baprs]-|AKIA[0-9A-Z]{12,}|AccountKey=|"
    r"password=|://[^/\s:]+:[^/\s@]+@)",
    re.IGNORECASE,
)


class ProfileError(ValueError):
    """A profile that cannot be trusted to describe an environment."""


@dataclass(frozen=True)
class Profile:
    """One socket, fully described."""

    path: Path
    rung: float
    socket: str
    mode: Mode
    pins: dict[str, dict[str, Any]]
    landing: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)

    def adapter_for(self, pin: str) -> str:
        """Which adapter this profile fits to a pin.

        `metadata_db` is the one pin addressed by DSN rather than adapter name,
        because Postgres is Postgres at every rung — that is the ZERO-cost swap
        the pin-out map claims, and a profile that had to name an adapter for it
        would be pretending there was a choice.
        """
        config = self.pins.get(pin, {})
        return str(config.get("adapter", "postgres" if pin == "metadata_db" else "none"))

    @property
    def dsn(self) -> str:
        return str(self.pins.get("metadata_db", {}).get("dsn", ""))


def load(path: str | Path) -> Profile:
    """Read a profile, or refuse it with a reason.

    Refusals are deliberately noisy. A profile is the ONE place environment
    difference lives, so a malformed one is not a config nit — it is the
    platform not knowing what environment it is in.
    """
    profile_path = Path(path)
    if not profile_path.exists():
        raise ProfileError(f"no profile at {profile_path}")

    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ProfileError(f"{profile_path}: a profile is a mapping")

    for required in ("profile_version", "rung", "socket", "mode", "pins"):
        if required not in raw:
            raise ProfileError(f"{profile_path}: missing {required!r}")

    pins = raw["pins"]
    if not isinstance(pins, dict):
        raise ProfileError(f"{profile_path}: `pins` is a mapping of pin -> config")

    unknown = set(pins) - set(PORTS)
    if unknown:
        raise ProfileError(
            f"{profile_path}: {', '.join(sorted(unknown))} are not pins. The twenty pins "
            "are declared on docs/architecture/plates/04-pin-out-map.md."
        )

    missing = set(PORTS) - set(pins)
    if missing:
        raise ProfileError(
            f"{profile_path}: no configuration for {', '.join(sorted(missing))}. Every pin "
            "must be addressed — an unmentioned pin is a pin nobody decided about."
        )

    _refuse_embedded_credentials(profile_path, raw)

    try:
        mode = Mode(raw["mode"])
    except ValueError:
        raise ProfileError(
            f"{profile_path}: mode must be one of {', '.join(m.value for m in Mode)}"
        ) from None

    return Profile(
        path=profile_path,
        rung=float(raw["rung"]),
        socket=str(raw["socket"]),
        mode=mode,
        pins=pins,
        landing=raw.get("landing", {}),
        agents=raw.get("agents", {}),
    )


def _refuse_embedded_credentials(path: Path, node: Any, trail: str = "") -> None:
    """Walk the profile and refuse any literal credential.

    Everything secret is a `secret://name` REFERENCE. Naming a secret is not
    holding one, and this is the check that keeps the difference real rather
    than aspirational.
    """
    match node:
        case dict():
            for key, value in node.items():
                _refuse_embedded_credentials(path, value, f"{trail}.{key}" if trail else str(key))
        case list():
            for index, value in enumerate(node):
                _refuse_embedded_credentials(path, value, f"{trail}[{index}]")
        case str() if not is_reference(node) and _CREDENTIAL_SHAPED.search(node):
            raise ProfileError(
                f"{path}: {trail} looks like a credential. Profiles carry `secret://name` "
                "references; the secrets adapter resolves them — dotenv at rungs 0.5-1, "
                "Key Vault at rung 3."
            )
        case _:
            return
