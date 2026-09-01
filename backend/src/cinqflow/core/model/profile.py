"""The connection profile, as a SHAPE. Reading one from disk is not core's job.

    "all environment difference lives in the connection profile, nowhere else"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

Law 3 makes the profile a first-class domain concept, so its SHAPE belongs
here, where an adapter can depend on it without depending on the installer.

`installer/profile.py` keeps `load()` — which reads a file, validates against
the twenty-one pins, and REFUSES any literal credential. All three are things only
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
    #: CF-V2-E12-05 / ADR-0015: the score's weighting is CONFIGURABLE, and
    #: configurable means per-environment, which means here and nowhere else.
    #: Absent keys fall back to `core.reliability.Weights`' defaults.
    reliability: dict[str, Any] = field(default_factory=dict)
    #: THE DATA PLANE, ADDRESSED SEPARATELY FROM THE PLATFORM.
    #:
    #: The platform is a chip. `metadata_db` is the socket it keeps its OWN
    #: state in — registry rows, control tables, the queue, the vector store —
    #: and that socket is the platform's, at every rung. THIS is the socket the
    #: client's data lives in: bronze/silver/gold, read by `catalog` and
    #: `sql_query`, and it is not the same thing. Conflating them is what makes
    #: a platform un-plug-and-play: it can then only ever read the warehouse it
    #: happens to store itself in.
    #:
    #: An ABSENT section means "the data plane is the platform's own database" —
    #: which is exactly rung 0.5's single-Postgres shape, so every existing
    #: profile keeps its current behaviour with no edit.
    data_plane: dict[str, Any] = field(default_factory=dict)

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

    @property
    def data_plane_dsn(self) -> str:
        """Where the CLIENT's data lives — bronze, silver, gold.

        Falls back to the platform's own DSN, because "no data plane declared"
        means "they are the same database", not "there is no data". That
        fallback is what keeps `profiles/local.yaml` a one-Postgres profile
        while `profiles/dev.yaml` points the same code at a warehouse that has
        never heard of the control tables.
        """
        return str(self.data_plane.get("dsn", "")) or self.dsn

    @property
    def data_plane_is_separate(self) -> bool:
        """True when the data plane is a DIFFERENT socket from the platform.

        Callers use this to decide whether a second connection is warranted.
        Comparing the resolved references (not the resolved DSNs) is deliberate:
        core resolves no secrets, and two profiles naming the same reference
        name the same plane by construction.
        """
        declared = str(self.data_plane.get("dsn", ""))
        return bool(declared) and declared != self.dsn
