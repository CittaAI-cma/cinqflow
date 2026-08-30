"""Read a connection profile from disk, or refuse it with a reason.

The SHAPE and the credential rule live in `core/model/profile.py`, so an
adapter can be handed a profile without importing the installer. What stays
here is what only the installer can do: read a file, and check the profile
against the twenty-one pins the platform actually has.

    "all environment difference lives in the connection profile, nowhere else"
    — docs/architecture/INVARIANTS.md, chip discipline

Refusals are deliberately noisy. A profile is the ONE place environment
difference lives, so a malformed one is not a config nit — it is the platform
not knowing what environment it is in.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.core.model.secrets import is_reference
from cinqflow.core.model.vocabulary import Mode
from cinqflow.ports import PORTS

__all__ = ["Profile", "ProfileError", "load"]

# Anything shaped like a secret must be a `secret://name` reference instead.
# This detector lives HERE rather than in core: it is a regex that matches
# credentials, which core's purity lint cannot distinguish from a credential —
# and the right answer to that is to put it above the line, not to teach the
# lint an exemption somebody else can reuse.
CREDENTIAL_SHAPED = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|xox[baprs]-|AKIA[0-9A-Z]{12,}|AccountKey=|"
    r"password=|://[^/\s:]+:[^/\s@]+@)",
    re.IGNORECASE,
)


def load(path: str | Path) -> Profile:
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
            f"{profile_path}: {', '.join(sorted(unknown))} are not pins. The twenty-one pins "
            "are declared on docs/architecture/plates/04-pin-out-map.md."
        )

    missing = set(PORTS) - set(pins)
    if missing:
        raise ProfileError(
            f"{profile_path}: no configuration for {', '.join(sorted(missing))}. Every pin "
            "must be addressed — an unmentioned pin is a pin nobody decided about."
        )

    _refuse_embedded_credentials(str(profile_path), raw)

    try:
        mode = Mode(raw["mode"])
    except ValueError:
        raise ProfileError(
            f"{profile_path}: mode must be one of {', '.join(m.value for m in Mode)}"
        ) from None

    return Profile(
        source=str(profile_path),
        rung=float(raw["rung"]),
        socket=str(raw["socket"]),
        mode=mode,
        pins=pins,
        landing=raw.get("landing", {}),
        agents=raw.get("agents", {}),
        reliability=raw.get("reliability", {}),
    )


def _refuse_embedded_credentials(source: str, node: object, trail: str = "") -> None:
    """Walk the profile and refuse any literal credential.

    Everything secret is a `secret://name` REFERENCE. Naming a secret is not
    holding one, and this is the check that keeps the difference real rather
    than aspirational.
    """
    match node:
        case dict():
            for key, value in node.items():
                _refuse_embedded_credentials(source, value, f"{trail}.{key}" if trail else str(key))
        case list():
            for index, value in enumerate(node):
                _refuse_embedded_credentials(source, value, f"{trail}[{index}]")
        case str() if not is_reference(node) and CREDENTIAL_SHAPED.search(node):
            raise ProfileError(
                f"{source}: {trail} looks like a credential. Profiles carry `secret://name` "
                "references; the secrets adapter resolves them — dotenv at rungs 0.5-1, "
                "Key Vault at rung 3."
            )
        case _:
            return
