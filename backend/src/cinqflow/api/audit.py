"""The append-only ledger, written from exactly one place.

    "audit is append-only; no deletion path exists for anyone"
    — docs/architecture/INVARIANTS.md, governance

    "Given any action, when it is recorded, then the audit entry states whether
     the actor was human, system or AI — never inferred."
    — CF-V0-E2-01

Two things this module makes structural rather than diligent:

  • Every audit row goes through `record()`, so `actor_type` is always the
    caller's real type. An AI action that reads as human defeats the entire
    trail, and the way that happens is a second write path.
  • DENIALS are audited on the same path as successes. A ledger that only holds
    what was permitted cannot answer "did anyone try?", which is the question
    an access review actually asks.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from cinqflow.core.model.governed import Actor, AuditEntry, ObjectType
from cinqflow.core.security import Action, Decision
from cinqflow.ports.metadata_db import MetadataDbPort

# A denial is not tied to a version — the caller never got far enough to name
# one. Zero says "not version-specific" rather than lying with 1.
NOT_VERSION_SPECIFIC = 0


class AuditLog:
    """A thin, deliberate front on the `metadata_db` pin.

    Deliberately offers no `delete` and no `amend`. The port has no such verb
    either, so removal is not a permission that could be misconfigured — it is a
    sentence nobody can write.
    """

    def __init__(self, store: MetadataDbPort) -> None:
        self._store = store

    def record(
        self,
        *,
        object_type: ObjectType,
        object_id: str,
        action: str,
        actor: Actor,
        version: int = NOT_VERSION_SPECIFIC,
        detail: str = "",
    ) -> AuditEntry:
        entry = AuditEntry(
            object_type=object_type,
            object_id=object_id,
            version=version,
            action=action,
            actor=actor,
            occurred_ts=datetime.now(UTC),
            detail=detail,
        )
        self._store.append_audit(entry)
        return entry

    def record_denial(
        self,
        *,
        actor: Actor,
        action: Action,
        decision: Decision,
        object_type: ObjectType,
        object_id: str,
    ) -> AuditEntry:
        """The attempt, recorded — including the reason it was refused.

        This is the second half of the story's guardrail. "Denied at the server"
        without a row is a control nobody can review.
        """
        return self.record(
            object_type=object_type,
            object_id=object_id,
            action=f"denied:{action.value}",
            actor=actor,
            detail=decision.reason,
        )

    def read(self, *, object_id: str | None = None, limit: int = 100) -> Sequence[AuditEntry]:
        return self._store.read_audit(object_id=object_id, limit=limit)
