"""sqlite_memory — governed objects in memory, with real versioning semantics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from cinqflow.core.model.agent_action import AgentAction
from cinqflow.core.model.governed import AuditEntry, GovernedObject, ObjectType
from cinqflow.core.proposals import Proposal, ProposalState
from cinqflow.ports import port
from cinqflow.ports.metadata_db import (
    ConcurrentVersionError,
    FileProfileRecord,
    ObjectNotFoundError,
)


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
        self._agent_actions: list[AgentAction] = []
        self._profiles: dict[tuple[str, str], FileProfileRecord] = {}
        self._proposals: dict[str, Proposal] = {}

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

    def record_transition(self, obj: GovernedObject, entry: AuditEntry) -> GovernedObject:
        versions = self._objects.get((obj.object_type, obj.object_id), [])
        for index, stored in enumerate(versions):
            if stored.version == obj.version:
                # State and approver move; the body is the STORED one — a
                # transition that could smuggle a body edit would let an
                # amendment skip versioning.
                versions[index] = replace(
                    stored,
                    lifecycle_state=obj.lifecycle_state,
                    approved_by=obj.approved_by,
                    approved_ts=obj.approved_ts,
                )
                self._audit.append(entry)
                return versions[index]
        raise ObjectNotFoundError(
            f"{obj.object_type}:{obj.object_id}@v{obj.version} was never saved — "
            "a state change to a phantom row is a lost approval"
        )

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

    def append_agent_action(self, action: AgentAction) -> None:
        self._agent_actions.append(action)

    def read_agent_actions(
        self, *, run_id: str | None = None, agent: str | None = None, limit: int = 100
    ) -> Sequence[AgentAction]:
        found = [
            a
            for a in self._agent_actions
            if (run_id is None or a.run_id == run_id) and (agent is None or a.agent == agent)
        ]
        # Oldest first: an agent's actions are a NARRATIVE, and reading a
        # trace backwards is how a reviewer misattributes a refusal.
        return tuple(found[:limit])

    # ── computed evidence · CF-V1-E5-01 ──────────────────────────────────────
    def record_profile(self, record: FileProfileRecord) -> FileProfileRecord:
        """First write wins, exactly as the Postgres adapter's ON CONFLICT DO
        NOTHING does. A mock that overwrote would let the real store's
        idempotence go untested by every unit test above it."""
        key = (record.profile_id, record.feed_id)
        return self._profiles.setdefault(key, record)

    def get_profile(self, profile_id: str, feed_id: str) -> FileProfileRecord:
        found = self._profiles.get((profile_id, feed_id))
        if found is None:
            raise ObjectNotFoundError(f"no profile {profile_id!r} for feed {feed_id!r}")
        return found

    def list_profiles(
        self,
        *,
        feed_id: str | None = None,
        profile_id: str | None = None,
        source_fingerprint: str | None = None,
        limit: int = 50,
    ) -> Sequence[FileProfileRecord]:
        found = [
            record
            for record in self._profiles.values()
            if (feed_id is None or record.feed_id == feed_id)
            and (profile_id is None or record.profile_id == profile_id)
            and (
                source_fingerprint is None
                or record.profile.source_fingerprint == source_fingerprint
            )
        ]
        found.sort(key=lambda r: (r.profiled_ts, r.profile_id), reverse=True)
        return tuple(found[:limit])

    # ── the HITL object · CF-V1-E5-02 ────────────────────────────────────────
    def record_proposal(self, proposal: Proposal) -> Proposal:
        """Insert or replace by id, with the PAYLOAD pinned to the first write.

        The Postgres adapter leaves `payload` out of its UPDATE; keeping the
        original here too means a test that accidentally rewrote a payload
        fails at the mock rather than passing everywhere except production.
        """
        existing = self._proposals.get(proposal.proposal_id)
        if existing is not None and proposal.payload != existing.payload:
            proposal = replace(proposal, payload=existing.payload)
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        found = self._proposals.get(proposal_id)
        if found is None:
            raise ObjectNotFoundError(f"no proposal {proposal_id!r}")
        return found

    def list_proposals(
        self,
        *,
        feed_id: str | None = None,
        agent: str | None = None,
        state: ProposalState | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        found = [
            p
            for p in self._proposals.values()
            if (feed_id is None or p.feed_id == feed_id)
            and (agent is None or p.agent == agent)
            and (state is None or p.state is state)
        ]
        found.sort(key=lambda p: (p.created_ts, p.proposal_id), reverse=True)
        return tuple(found[:limit])
