"""The `metadata_db` pin — persist governed objects and audit.

    verb: persist_registry_audit   mock: sqlite   dev: postgres
    target: azure_pg_flexible
    — docs/architecture/plates/04-pin-out-map.md

Swap cost: ZERO. Same protocol, same driver, same SQL — Azure Database for
PostgreSQL Flexible Server is a connection string away from the Postgres on a
laptop. This is the cheapest pin on the map and it is not an accident: it is
what choosing a protocol over a brand buys.

The port speaks in GOVERNED OBJECTS, not tables. Every governed object shares
one lifecycle (ADR-0006) and carries the same columns — object_id, version,
lifecycle_state, created_by, approved_by, approved_ts — so the repository does
not need a method per type, and no object type can opt out of the state
machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from cinqflow.core.model.governed import AuditEntry, GovernedObject, ObjectType


class MetadataError(RuntimeError):
    """A registry or governance write that could not be honoured."""


class ObjectNotFoundError(MetadataError):
    """Distinct from a store failure — and from 'you may not see it'.

    An out-of-scope object must not be reported as missing, and a missing
    object must not be reported as forbidden. Conflating them either leaks the
    existence of objects a caller may not see, or sends someone hunting for a
    row that is simply not theirs.
    """


class ConcurrentVersionError(MetadataError):
    """Two authors versioned the same object from the same base.

    Refused rather than merged: silently taking the last write is how an
    approved configuration becomes something nobody approved.
    """


@runtime_checkable
class MetadataDbPort(Protocol):
    def save(self, obj: GovernedObject) -> GovernedObject:
        """Persist a NEW VERSION. Never an in-place edit.

            "All changes versioned, audited, and role-controlled."

        Returns the stored object so the caller sees the version it actually
        got, rather than the one it assumed.
        """
        ...

    def get(
        self, object_type: ObjectType, object_id: str, version: int | None = None
    ) -> GovernedObject:
        """A specific version, or the latest. `version=None` means latest."""
        ...

    def list(self, object_type: ObjectType, **filters: Any) -> Sequence[GovernedObject]: ...

    def history(self, object_type: ObjectType, object_id: str) -> Sequence[GovernedObject]:
        """Every version, oldest first. This is what makes "the engine always
        states which feed version a run used" answerable after the fact."""
        ...

    def append_audit(self, entry: AuditEntry) -> None:
        """Append-only. There is deliberately no update_audit and no
        delete_audit verb on this port — for anyone, including administrators.

            "audit is append-only; no deletion path exists for anyone"
            — docs/architecture/INVARIANTS.md, governance

        A port that cannot express deletion is a stronger guarantee than a
        permission check that could be misconfigured.
        """
        ...

    def read_audit(
        self, *, object_id: str | None = None, limit: int = 100
    ) -> Sequence[AuditEntry]: ...
