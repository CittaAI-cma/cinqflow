"""The connection profile, as a SHAPE. Reading one from disk is not core's job.

    "all environment difference lives in the connection profile, nowhere else"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

Law 3 makes the profile a first-class domain concept, so its SHAPE belongs
here, where an adapter can depend on it without depending on the installer.

`installer/profile.py` keeps `load()` — which reads a file, validates against
the twenty pins, and REFUSES any literal credential. All three are things only
the loader does, and the last is why the credential-shaped detector lives
there: a regex that matches credentials is not a credential, but core's purity
lint cannot tell them apart, and the right answer to that is to put the
detector above the line rather than teach the lint an exemption anybody could
reuse later.

Note `source` is a string, not a `Path`. Core imports no path type — Law 1 —
and a profile only ever needs to SAY where it came from, in a message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinqflow.core.model.vocabulary import Mode


class ProfileError(ValueError):
    """A profile that cannot be trusted to describe an environment."""


@dataclass(frozen=True)
class Profile:
    """One socket, fully described."""

    source: str
    rung: float
    socket: str
    mode: Mode
    pins: dict[str, dict[str, Any]]
    landing: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The profile's filename, for messages and manifests."""
        return self.source.rsplit("/", 1)[-1]

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
