"""The installation manifest — a record of every object the installer created.

    installer: idempotent + reversible, writes an installation manifest of
               every object created
    — docs/architecture/plates/15-deployment-topology.md

Why a manifest rather than "DROP SCHEMA CASCADE": uninstall must remove exactly
what the installer created and nothing else. In the client's tenant the
platform is ADDITIVE to an existing estate (ADR-0013), and a cascade drop that
guessed would be a production incident with our name on it.

So the manifest is the uninstall plan, written at install time, when we still
know what we did.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class CreatedObject:
    kind: str  # schema · table · index · function · trigger · extension
    identifier: str
    created_ts: str

    @classmethod
    def now(cls, kind: str, identifier: str) -> CreatedObject:
        return cls(kind=kind, identifier=identifier, created_ts=datetime.now(UTC).isoformat())


@dataclass
class InstallationManifest:
    profile: str
    rung: float
    socket: str
    spec_fingerprints: dict[str, str] = field(default_factory=dict)
    objects: list[CreatedObject] = field(default_factory=list)
    installed_ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    manifest_version: int = MANIFEST_VERSION

    def record(self, kind: str, identifier: str) -> None:
        if any(o.kind == kind and o.identifier == identifier for o in self.objects):
            return  # idempotent: installing twice records once
        self.objects.append(CreatedObject.now(kind, identifier))

    def write(self, path: Path) -> Path:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> InstallationManifest:
        raw = json.loads(path.read_text(encoding="utf-8"))
        objects = [CreatedObject(**o) for o in raw.pop("objects", [])]
        return cls(objects=objects, **raw)

    def uninstall_order(self) -> list[CreatedObject]:
        """Reverse creation order, so dependants go before their dependencies.

        Triggers before functions, indexes before tables, tables before schemas
        — which falls out of reversing the order they were made in, rather than
        needing a dependency graph nobody would maintain.
        """
        return list(reversed(self.objects))
