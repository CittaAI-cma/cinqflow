"""sqlite_memory — governed objects in memory, with real versioning semantics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from cinqflow.core.identity.exceptions import (
    ExceptionState,
    IdentityException,
    IdentityExceptionEvent,
    fold,
)
from cinqflow.core.model.agent_action import AgentAction
from cinqflow.core.model.governed import AuditEntry, GovernedObject, ObjectType
from cinqflow.core.operations.fingerprint import IncidentEvent, IncidentState
from cinqflow.core.proposals import Proposal, ProposalState
from cinqflow.core.registry.suspension import Suspension, SuspensionEvent, current
from cinqflow.core.variance import Variance
from cinqflow.ports import port
from cinqflow.ports.metadata_db import (
    ActionRecordRow,
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
        self._suspensions: list[SuspensionEvent] = []
        self._action_events: list[ActionRecordRow] = []
        self._incident_events: list[IncidentEvent] = []
        self._variance_events: list[tuple[Variance, str, object]] = []
        self._identity_exception_events: list[IdentityExceptionEvent] = []

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

    # ── ops.feed_suspension · CF-V1-E3-04 ────────────────────────────────────
    def record_suspension(self, event: SuspensionEvent) -> SuspensionEvent:
        """Append-only, enforced by there being no removal path here at all —
        the same way the audit list is."""
        self._suspensions.append(event)
        return event

    def current_suspension(self, feed_id: str) -> Suspension:
        return current(feed_id, tuple(self._suspensions))

    def list_suspensions(
        self, *, feed_id: str | None = None, limit: int = 50
    ) -> Sequence[SuspensionEvent]:
        found = [e for e in self._suspensions if feed_id is None or e.feed_id == feed_id]
        return tuple(sorted(found, key=lambda e: e.occurred_ts, reverse=True)[:limit])

    # ── ops.action_record · CF-V2-E12-03 / E8-04 ─────────────────────────────
    def record_action_event(self, row: ActionRecordRow) -> ActionRecordRow:
        """Append-only, one row per phase — no removal path, like the audit
        list and the suspension ledger."""
        self._action_events.append(row)
        return row

    def get_action_record(self, record_id: str) -> ActionRecordRow:
        phases = [row for row in self._action_events if row.record_id == record_id]
        if not phases:
            raise ObjectNotFoundError(f"action record {record_id} was never written")
        # Ties on occurred_ts go to the LATER APPEND — in an append-only
        # ledger the newest row is the current one even when two writes share
        # a timestamp. reversed() + max() would keep the earlier; this keeps
        # insertion order as the tiebreak.
        current_row = phases[0]
        for row in phases[1:]:
            if row.occurred_ts >= current_row.occurred_ts:
                current_row = row
        return current_row

    def list_action_records(
        self, *, batch_id: str | None = None, feed_id: str | None = None, limit: int = 50
    ) -> Sequence[ActionRecordRow]:
        newest: dict[str, ActionRecordRow] = {}
        for row in self._action_events:
            if batch_id is not None and row.record.target != batch_id:
                continue
            if feed_id is not None and row.feed_id != feed_id:
                continue
            held = newest.get(row.record_id)
            if held is None or row.occurred_ts >= held.occurred_ts:
                newest[row.record_id] = row
        current_rows = sorted(newest.values(), key=lambda row: row.occurred_ts, reverse=True)
        return tuple(current_rows[:limit])

    # ── ops.incident_event · CF-V2-E12-04 ────────────────────────────────────
    def record_incident_event(self, event: IncidentEvent) -> IncidentEvent:
        """Append-only — a transition is a new row, never an overwrite."""
        self._incident_events.append(event)
        return event

    def get_incident_event(self, incident_id: str) -> IncidentEvent:
        events = [e for e in self._incident_events if e.incident_id == incident_id]
        if not events:
            raise ObjectNotFoundError(f"incident {incident_id} has no events")
        # Same tiebreak as get_action_record: the later append wins.
        current_event = events[0]
        for event in events[1:]:
            if event.occurred_ts >= current_event.occurred_ts:
                current_event = event
        return current_event

    def list_incident_events(
        self,
        *,
        batch_id: str | None = None,
        feed_id: str | None = None,
        state: IncidentState | None = None,
        limit: int = 50,
    ) -> Sequence[IncidentEvent]:
        newest: dict[str, IncidentEvent] = {}
        for event in self._incident_events:
            if batch_id is not None and event.batch_id != batch_id:
                continue
            if feed_id is not None and event.feed_id != feed_id:
                continue
            held = newest.get(event.incident_id)
            if held is None or event.occurred_ts >= held.occurred_ts:
                newest[event.incident_id] = event
        # The state filter applies to the CURRENT state, after folding — an
        # incident that was open and is now closed is not an open incident.
        current_events = [e for e in newest.values() if state is None or e.state is state]
        current_events.sort(key=lambda e: e.occurred_ts, reverse=True)
        return tuple(current_events[:limit])

    # ── ops.variance_event · CF-V2-E13-03 ────────────────────────────────────
    def record_variance_event(
        self, variance: Variance, *, actor_subject: str, occurred_ts: object
    ) -> Variance:
        self._variance_events.append((variance, actor_subject, occurred_ts))
        return variance

    def get_variance(self, variance_id: str) -> Variance:
        held: Variance | None = None
        for variance, _, _ in self._variance_events:
            if variance.variance_id == variance_id:
                held = variance  # later append wins, like every ledger here
        if held is None:
            raise ObjectNotFoundError(f"variance {variance_id} was never recorded")
        return held

    def list_variances(
        self, *, batch_id: str | None = None, feed_id: str | None = None, limit: int = 50
    ) -> Sequence[Variance]:
        newest_variance: dict[str, Variance] = {}
        order: list[str] = []
        for variance, _, _ in self._variance_events:
            if batch_id is not None and variance.batch_id != batch_id:
                continue
            if feed_id is not None and variance.feed_id != feed_id:
                continue
            if variance.variance_id not in newest_variance:
                order.append(variance.variance_id)
            newest_variance[variance.variance_id] = variance
        current = [newest_variance[v] for v in reversed(order)]
        return tuple(current[:limit])

    # ── identity_exception / identity_exception_event · CF-V3-E9-01/E9-02 ────
    def record_identity_exception_event(
        self, event: IdentityExceptionEvent
    ) -> IdentityExceptionEvent:
        """Append-only. Unlike every other ledger here, there is nothing to
        fold ON WRITE — `fold()` runs at read time, from every event this key
        has ever had, which is what lets `occurrences` keep growing without a
        stored value anywhere disagreeing with the ledger that grew it."""
        self._identity_exception_events.append(event)
        return event

    def get_identity_exception(self, exception_key: str) -> IdentityException:
        events = [e for e in self._identity_exception_events if e.exception_key == exception_key]
        if not events:
            raise ObjectNotFoundError(f"identity exception {exception_key} has no events")
        return fold(events)

    def list_identity_exceptions(
        self,
        *,
        source_system: str | None = None,
        state: ExceptionState | None = None,
        limit: int = 50,
    ) -> Sequence[IdentityException]:
        by_key: dict[str, list[IdentityExceptionEvent]] = {}
        for event in self._identity_exception_events:
            by_key.setdefault(event.exception_key, []).append(event)
        current_exceptions = [fold(events) for events in by_key.values()]
        current_exceptions = [
            exc
            for exc in current_exceptions
            if (source_system is None or exc.source_system == source_system)
            and (state is None or exc.state is state)
        ]
        current_exceptions.sort(key=lambda exc: exc.opened_ts)
        return tuple(current_exceptions[:limit])
