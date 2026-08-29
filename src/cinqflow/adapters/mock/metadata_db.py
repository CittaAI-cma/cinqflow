"""sqlite_memory — governed objects in memory, with real versioning semantics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cinqflow.core.model.governed import AuditEntry, GovernedObject, ObjectType
from cinqflow.ports import port
from cinqflow.ports.metadata_db import ConcurrentVersionError, ObjectNotFoundError


@port("metadata_db", "mock")
class MemMetadataDb:
    """Versioned storage plus an append-only audit list.

    "Append-only" is enforced by there being no removal path here at all — not
    a permission check that could be misconfigured, and not a convention. The
    audit list is only ever appended to, in one method.
    """

    def __init__(self) -> None:
        self._objects: dict[tuple[ObjectType, str], list[GovernedObject]] = {}
        self._audit: list[AuditEntry] = []

    def save(self, obj: GovernedObject) -> GovernedObject:
        versions = self._objects.setdefault((obj.object_type, obj.object_id), [])
        if any(v.version == obj.version for v in versions):
            raise ConcurrentVersionError(
                f"{obj.object_type}:{obj.object_id}@v{obj.version} already exists — two authors "
                "versioned from the same base. Taking the last write would publish something "
                "nobody approved."
            )
        versions.append(obj)
        versions.sort(key=lambda v: v.version)
        return obj

    def get(
        self, object_type: ObjectType, object_id: str, version: int | None = None
    ) -> GovernedObject:
        versions = self._objects.get((object_type, object_id))
        if not versions:
            raise ObjectNotFoundError(f"{object_type}:{object_id}")
        if version is None:
            return versions[-1]
        for candidate in versions:
            if candidate.version == version:
                return candidate
        raise ObjectNotFoundError(f"{object_type}:{object_id}@v{version}")

    def list(self, object_type: ObjectType, **filters: Any) -> Sequence[GovernedObject]:
        latest = [
            versions[-1]
            for (kind, _), versions in self._objects.items()
            if kind is object_type and versions
        ]
        for key, value in filters.items():
            latest = [o for o in latest if o.body.get(key) == value]
        return tuple(sorted(latest, key=lambda o: o.object_id))

    def history(self, object_type: ObjectType, object_id: str) -> Sequence[GovernedObject]:
        return tuple(self._objects.get((object_type, object_id), ()))

    def append_audit(self, entry: AuditEntry) -> None:
        self._audit.append(entry)

    def read_audit(self, *, object_id: str | None = None, limit: int = 100) -> Sequence[AuditEntry]:
        found = [e for e in self._audit if object_id is None or e.object_id == object_id]
        return tuple(sorted(found, key=lambda e: e.occurred_ts, reverse=True)[:limit])
