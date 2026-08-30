"""registry.governed_object, governance.audit_ledger, audit.agent_action — on
Postgres. Rung 0.5's real registry, governance and agent-audit plane.

    "Every governed object shares one lifecycle (ADR-0006)"
    "audit is append-only; no deletion path exists for anyone"
    "100% of model calls carry prompt hash, model version, cost and caller
     identity in the audit log"
    — docs/architecture/INVARIANTS.md, governance / intelligence

The SAME contract suite that runs against `MemMetadataDb` runs against this —
a second adapter is a certification, not a migration. What the mock keeps as
dict semantics (versions never collide, audit only ever appended to) this one
keeps as a primary key and the absence of any UPDATE/DELETE statement in the
whole file.

`metadata_db` is the one pin a profile addresses by DSN rather than adapter
name (`core/model/profile.py:adapter_for`) — Postgres is Postgres at every
rung, so this registers under the name the profile already assumes: "postgres".
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.citations import parse as parse_citation
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.vocabulary import ActorType, BatchState, RiskClass
from cinqflow.core.operations.actions import ActionPhase, ActionRecord, OpsAction
from cinqflow.core.operations.fingerprint import IncidentEvent, IncidentState
from cinqflow.core.profiling import FileProfile
from cinqflow.core.proposals import Proposal, ProposalBody, ProposalState
from cinqflow.core.registry.suspension import (
    Suspension,
    SuspensionAction,
    SuspensionEvent,
    current,
)
from cinqflow.ports import port
from cinqflow.ports.metadata_db import (
    ActionRecordRow,
    ConcurrentVersionError,
    FileProfileRecord,
    ObjectNotFoundError,
)


def _actor(subject: str, actor_type: str, name: str | None) -> Actor:
    return Actor(subject=subject, actor_type=ActorType(actor_type), display_name=name or "")


def _governed_object(row: tuple[Any, ...]) -> GovernedObject:
    (
        object_type,
        object_id,
        version,
        lifecycle_state,
        body,
        created_by_subject,
        created_by_type,
        created_by_name,
        created_ts,
        approved_by_subject,
        approved_by_type,
        approved_by_name,
        approved_ts,
    ) = row
    return GovernedObject(
        object_type=ObjectType(object_type),
        object_id=object_id,
        version=version,
        lifecycle_state=LifecycleState(lifecycle_state),
        created_by=_actor(created_by_subject, created_by_type, created_by_name),
        created_ts=created_ts,
        body=body,
        approved_by=(
            _actor(approved_by_subject, approved_by_type, approved_by_name)
            if approved_by_subject
            else None
        ),
        approved_ts=approved_ts,
    )


def _audit_entry(row: tuple[Any, ...]) -> AuditEntry:
    (
        object_type,
        object_id,
        version,
        action,
        actor_subject,
        actor_type,
        actor_name,
        occurred_ts,
        from_state,
        to_state,
        detail,
    ) = row
    return AuditEntry(
        object_type=ObjectType(object_type),
        object_id=object_id,
        version=version,
        action=action,
        actor=_actor(actor_subject, actor_type, actor_name),
        occurred_ts=occurred_ts,
        from_state=LifecycleState(from_state) if from_state else None,
        to_state=LifecycleState(to_state) if to_state else None,
        detail=detail or "",
    )


def _profile_record(row: tuple[Any, ...]) -> FileProfileRecord:
    """Rebuild from the stored JSONB.

    The facts column is the whole profile, so what comes back is what went in
    — including the value-bearing fields, which is why the API redacts on the
    way OUT by role rather than on the way in. Storing a redacted profile would
    make the evidence unrecoverable for the role that is allowed to see it.
    """
    feed_id, facts, profiled_by, profiled_ts = row
    return FileProfileRecord(
        feed_id=feed_id,
        profile=FileProfile.from_dict(facts if isinstance(facts, dict) else json.loads(facts)),
        profiled_by=profiled_by,
        profiled_ts=profiled_ts,
    )


# The proposal statements below spell their column list out LITERALLY in every
# statement rather than sharing a constant. The duplication is deliberate: a
# query assembled from a variable is a query the SQL-injection lint cannot
# check and a reviewer has to run to read, and this file's whole safety
# argument is that every statement is legible where it sits.


def _proposal(row: tuple[Any, ...]) -> Proposal:
    """Rebuild one proposal from its row.

    `payload` and `corrections` both live in the JSONB `payload` column:
    keeping the agent's output and the human's changes in one document is what
    makes the eval set recoverable with a single read rather than a join
    somebody has to remember to write.
    """
    (
        proposal_id,
        agent,
        capability,
        risk_class,
        feed_id,
        run_id,
        state,
        payload,
        confidence,
        grounding_citations,
        prompt_hash,
        created_by_subject,
        created_by_type,
        created_ts,
        decided_by_subject,
        decision_comment,
        decided_ts,
        applied_object_type,
        applied_object_id,
        applied_version,
    ) = row
    document = payload if isinstance(payload, dict) else json.loads(payload)
    citations = grounding_citations or []
    if isinstance(citations, str):
        citations = json.loads(citations)
    return Proposal(
        proposal_id=str(proposal_id),
        agent=agent,
        capability=capability,
        risk_class=RiskClass[risk_class],
        run_id=run_id,
        feed_id=feed_id,
        state=ProposalState(state),
        payload=document.get("payload", {}),
        confidence=float(confidence) if confidence is not None else None,
        grounding_citations=tuple(parse_citation(c) for c in citations),
        prompt_hash=prompt_hash or "",
        created_by=_actor(created_by_subject, created_by_type, None),
        created_ts=created_ts,
        decided_by=(
            _actor(decided_by_subject, ActorType.HUMAN.value, None) if decided_by_subject else None
        ),
        decision_comment=decision_comment or "",
        decided_ts=decided_ts,
        applied_object_type=ObjectType(applied_object_type) if applied_object_type else None,
        applied_object_id=applied_object_id,
        applied_version=applied_version,
        corrections=ProposalBody.corrections_from(document),
    )


def _suspension(row: tuple[Any, ...]) -> SuspensionEvent:
    return SuspensionEvent(
        feed_id=row[0],
        action=SuspensionAction(row[1]),
        reason=row[2],
        actor=_actor(row[3], ActorType.HUMAN.value, row[4]),
        occurred_ts=row[5],
        resumes_after=row[6],
    )


def _incident_event(row: tuple[Any, ...]) -> IncidentEvent:
    return IncidentEvent(
        incident_id=row[0],
        batch_id=row[1],
        feed_id=row[2],
        signature=row[3],
        state=IncidentState(row[4]),
        actor_subject=row[5],
        acknowledged_by=row[6] or "",
        assigned_to=row[7] or "",
        resolution=row[8] or "",
        opened_ts=row[9],
        resolved_ts=row[10],
        occurred_ts=row[11],
    )


# The mapper below reads columns by POSITION, in the order every SELECT in
# this file names them: record_id, feed_id, batch_id, action, phase,
# actor_subject, actor_name, reason, approval_identifier, outcome,
# observed_state, requested_ts, verified_ts.
def _action_row(row: tuple[Any, ...]) -> ActionRecordRow:
    return ActionRecordRow(
        record_id=row[0],
        feed_id=row[1],
        record=ActionRecord(
            action=OpsAction(row[3]),
            target=row[2],
            actor=_actor(row[5], ActorType.HUMAN.value, row[6]),
            reason=row[7] or "",
            approval_identifier=row[8] or "",
            phase=ActionPhase(row[4]),
            outcome=row[9] or "",
            observed_state=BatchState(row[10]) if row[10] else None,
            requested_ts=row[11],
            verified_ts=row[12],
        ),
    )


def _agent_action(row: tuple[Any, ...]) -> AgentAction:
    (
        run_id,
        agent,
        action,
        outcome,
        actor_subject,
        actor_type,
        actor_name,
        occurred_ts,
        risk_class,
        prompt_ref,
        prompt_hash,
        model,
        model_version,
        prompt_tokens,
        completion_tokens,
        cost_usd,
        latency_ms,
        detail,
    ) = row
    return AgentAction(
        run_id=run_id,
        agent=agent,
        action=action,
        outcome=ActionOutcome(outcome),
        actor=_actor(actor_subject, actor_type, actor_name),
        occurred_ts=occurred_ts,
        risk_class=risk_class,
        prompt_ref=prompt_ref or "",
        prompt_hash=prompt_hash or "",
        model=model or "",
        model_version=model_version or "",
        prompt_tokens=prompt_tokens or 0,
        completion_tokens=completion_tokens or 0,
        cost_usd=cost_usd,
        latency_ms=latency_ms or 0,
        detail=detail or "",
    )


@port("metadata_db", "postgres")
class PostgresMetadataDb:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    # ── registry.governed_object ──────────────────────────────────────────────
    def save(self, obj: GovernedObject) -> GovernedObject:
        existing = self._db.fetch_one(
            "SELECT 1 FROM registry.governed_object WHERE object_type = %s "
            "AND object_id = %s AND version = %s",
            (obj.object_type.value, obj.object_id, obj.version),
        )
        if existing is not None:
            raise ConcurrentVersionError(
                f"{obj.object_type}:{obj.object_id}@v{obj.version} already exists — two authors "
                "versioned from the same base. Taking the last write would publish something "
                "nobody approved."
            )
        self._db.execute(
            "INSERT INTO registry.governed_object (object_type, object_id, version, "
            "lifecycle_state, body, created_by_subject, created_by_type, created_by_name, "
            "created_ts, approved_by_subject, approved_by_type, approved_by_name, approved_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                obj.object_type.value,
                obj.object_id,
                obj.version,
                obj.lifecycle_state.value,
                json.dumps(obj.body, sort_keys=True, default=str),
                obj.created_by.subject,
                obj.created_by.actor_type.value,
                obj.created_by.display_name or None,
                obj.created_ts,
                obj.approved_by.subject if obj.approved_by else None,
                obj.approved_by.actor_type.value if obj.approved_by else None,
                obj.approved_by.display_name if obj.approved_by else None,
                obj.approved_ts,
            ),
        )
        return obj

    def record_transition(self, obj: GovernedObject, entry: AuditEntry) -> GovernedObject:
        # State and approver columns only — the body column is deliberately
        # absent from this UPDATE, so a transition cannot smuggle an edit past
        # versioning. The audit row lands in the same connection, which at
        # call sites running inside `pg_control.commit` makes the pair one
        # transaction: both writes, or neither.
        row = self._db.fetch_one(
            "SELECT 1 FROM registry.governed_object WHERE object_type = %s "
            "AND object_id = %s AND version = %s",
            (obj.object_type.value, obj.object_id, obj.version),
        )
        if row is None:
            raise ObjectNotFoundError(
                f"{obj.object_type}:{obj.object_id}@v{obj.version} was never saved — "
                "a state change to a phantom row is a lost approval"
            )
        self._db.execute(
            "UPDATE registry.governed_object SET lifecycle_state = %s, "
            "approved_by_subject = %s, approved_by_type = %s, approved_by_name = %s, "
            "approved_ts = %s WHERE object_type = %s AND object_id = %s AND version = %s",
            (
                obj.lifecycle_state.value,
                obj.approved_by.subject if obj.approved_by else None,
                obj.approved_by.actor_type.value if obj.approved_by else None,
                obj.approved_by.display_name if obj.approved_by else None,
                obj.approved_ts,
                obj.object_type.value,
                obj.object_id,
                obj.version,
            ),
        )
        self.append_audit(entry)
        return self.get(obj.object_type, obj.object_id, obj.version)

    def get(
        self, object_type: ObjectType, object_id: str, version: int | None = None
    ) -> GovernedObject:
        if version is None:
            row = self._db.fetch_one(
                "SELECT object_type, object_id, version, lifecycle_state, body, "
                "created_by_subject, created_by_type, created_by_name, created_ts, "
                "approved_by_subject, approved_by_type, approved_by_name, approved_ts "
                "FROM registry.governed_object WHERE object_type = %s AND object_id = %s "
                "ORDER BY version DESC LIMIT 1",
                (object_type.value, object_id),
            )
        else:
            row = self._db.fetch_one(
                "SELECT object_type, object_id, version, lifecycle_state, body, "
                "created_by_subject, created_by_type, created_by_name, created_ts, "
                "approved_by_subject, approved_by_type, approved_by_name, approved_ts "
                "FROM registry.governed_object WHERE object_type = %s AND object_id = %s "
                "AND version = %s",
                (object_type.value, object_id, version),
            )
        if row is None:
            suffix = f"@v{version}" if version is not None else ""
            raise ObjectNotFoundError(f"{object_type}:{object_id}{suffix}")
        return _governed_object(row)

    def list(self, object_type: ObjectType, **filters: Any) -> Sequence[GovernedObject]:
        rows = self._db.fetch_all(
            "SELECT DISTINCT ON (object_id) object_type, object_id, version, lifecycle_state, "
            "body, created_by_subject, created_by_type, created_by_name, created_ts, "
            "approved_by_subject, approved_by_type, approved_by_name, approved_ts "
            "FROM registry.governed_object WHERE object_type = %s "
            "ORDER BY object_id, version DESC",
            (object_type.value,),
        )
        found = [_governed_object(row) for row in rows]
        for key, value in filters.items():
            found = [o for o in found if o.body.get(key) == value]
        return tuple(sorted(found, key=lambda o: o.object_id))

    def history(self, object_type: ObjectType, object_id: str) -> Sequence[GovernedObject]:
        rows = self._db.fetch_all(
            "SELECT object_type, object_id, version, lifecycle_state, body, "
            "created_by_subject, created_by_type, created_by_name, created_ts, "
            "approved_by_subject, approved_by_type, approved_by_name, approved_ts "
            "FROM registry.governed_object WHERE object_type = %s AND object_id = %s "
            "ORDER BY version",
            (object_type.value, object_id),
        )
        return tuple(_governed_object(row) for row in rows)

    # ── governance.audit_ledger — append-only, no update, no delete ──────────
    def append_audit(self, entry: AuditEntry) -> None:
        self._db.execute(
            "INSERT INTO governance.audit_ledger (entry_id, object_type, object_id, version, "
            "action, actor_subject, actor_type, actor_name, occurred_ts, from_state, to_state, "
            "detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                entry.object_type.value,
                entry.object_id,
                entry.version,
                entry.action,
                entry.actor.subject,
                entry.actor.actor_type.value,
                entry.actor.display_name or None,
                entry.occurred_ts,
                entry.from_state.value if entry.from_state else None,
                entry.to_state.value if entry.to_state else None,
                entry.detail or None,
            ),
        )

    def read_audit(self, *, object_id: str | None = None, limit: int = 100) -> Sequence[AuditEntry]:
        statement = (
            "SELECT object_type, object_id, version, action, actor_subject, actor_type, "
            "actor_name, occurred_ts, from_state, to_state, detail FROM governance.audit_ledger"
        )
        parameters: tuple[Any, ...] = ()
        if object_id is not None:
            statement += " WHERE object_id = %s"
            parameters += (object_id,)
        statement += " ORDER BY occurred_ts DESC LIMIT %s"
        parameters += (limit,)
        rows = self._db.fetch_all(statement, parameters)
        return tuple(_audit_entry(row) for row in rows)

    # ── audit.agent_action — append-only, no update, no delete ───────────────
    def append_agent_action(self, action: AgentAction) -> None:
        self._db.execute(
            "INSERT INTO audit.agent_action (action_id, run_id, agent, action, outcome, "
            "actor_subject, actor_type, actor_name, occurred_ts, risk_class, prompt_ref, "
            "prompt_hash, model, model_version, prompt_tokens, completion_tokens, cost_usd, "
            "latency_ms, detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                action.run_id,
                action.agent,
                action.action,
                action.outcome.value,
                action.actor.subject,
                action.actor.actor_type.value,
                action.actor.display_name or None,
                action.occurred_ts,
                action.risk_class,
                action.prompt_ref or None,
                action.prompt_hash or None,
                action.model or None,
                action.model_version or None,
                action.prompt_tokens,
                action.completion_tokens,
                action.cost_usd,
                action.latency_ms,
                action.detail or None,
            ),
        )

    def read_agent_actions(
        self, *, run_id: str | None = None, agent: str | None = None, limit: int = 100
    ) -> Sequence[AgentAction]:
        statement = (
            "SELECT run_id, agent, action, outcome, actor_subject, actor_type, actor_name, "
            "occurred_ts, risk_class, prompt_ref, prompt_hash, model, model_version, "
            "prompt_tokens, completion_tokens, cost_usd, latency_ms, detail "
            "FROM audit.agent_action WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if run_id is not None:
            statement += " AND run_id = %s"
            parameters += (run_id,)
        if agent is not None:
            statement += " AND agent = %s"
            parameters += (agent,)
        # Oldest first: an agent's actions are a NARRATIVE, and reading a
        # trace backwards is how a reviewer misattributes a refusal.
        statement += " ORDER BY occurred_ts LIMIT %s"
        parameters += (limit,)
        rows = self._db.fetch_all(statement, parameters)
        return tuple(_agent_action(row) for row in rows)

    # ── profiling.file_profile · CF-V1-E5-01 ─────────────────────────────────
    def record_profile(self, record: FileProfileRecord) -> FileProfileRecord:
        """ON CONFLICT DO NOTHING — the idempotence is the DATABASE's, not a
        check somebody remembered to write.

        The primary key is (profile_id, feed_id) and profile_id is the digest
        of the facts, so a re-run over unchanged bytes collides by
        construction and the original row keeps its original timestamp. That
        is what makes "profiling statistics exactly reproducible on re-run"
        observable in the store rather than only in a test.
        """
        profile = record.profile
        self._db.execute(
            "INSERT INTO profiling.file_profile (profile_id, feed_id, source_key, "
            "source_fingerprint, profiler_version, readable, would_load, row_count, "
            "column_count, sampled, facts, profiled_by, profiled_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (profile_id, feed_id) DO NOTHING",
            (
                profile.profile_id,
                record.feed_id,
                profile.source_key,
                profile.source_fingerprint,
                profile.profiler_version,
                profile.readable,
                profile.would_load,
                profile.structure.data_rows,
                profile.structure.column_count,
                profile.structure.sampled,
                json.dumps(profile.to_dict(), sort_keys=True, default=str),
                record.profiled_by,
                record.profiled_ts,
            ),
        )
        # Read back rather than return the argument: on a conflict the stored
        # row is the FIRST one, and returning the caller's copy would report a
        # timestamp the store does not hold.
        return self.get_profile(profile.profile_id, record.feed_id)

    def get_profile(self, profile_id: str, feed_id: str) -> FileProfileRecord:
        row = self._db.fetch_one(
            "SELECT feed_id, facts, profiled_by, profiled_ts FROM profiling.file_profile "
            "WHERE profile_id = %s AND feed_id = %s",
            (profile_id, feed_id),
        )
        if row is None:
            raise ObjectNotFoundError(f"no profile {profile_id!r} for feed {feed_id!r}")
        return _profile_record(row)

    def list_profiles(
        self,
        *,
        feed_id: str | None = None,
        profile_id: str | None = None,
        source_fingerprint: str | None = None,
        limit: int = 50,
    ) -> Sequence[FileProfileRecord]:
        statement = (
            "SELECT feed_id, facts, profiled_by, profiled_ts FROM profiling.file_profile WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if feed_id is not None:
            statement += " AND feed_id = %s"
            parameters += (feed_id,)
        if profile_id is not None:
            statement += " AND profile_id = %s"
            parameters += (profile_id,)
        if source_fingerprint is not None:
            statement += " AND source_fingerprint = %s"
            parameters += (source_fingerprint,)
        statement += " ORDER BY profiled_ts DESC, profile_id DESC LIMIT %s"
        parameters += (limit,)
        return tuple(_profile_record(row) for row in self._db.fetch_all(statement, parameters))

    # ── proposals.proposal · CF-V1-E5-02 and every later R2 agent ────────────
    def record_proposal(self, proposal: Proposal) -> Proposal:
        """Upsert the state machine's row, PAYLOAD PINNED TO THE FIRST WRITE.

        The UPDATE clause concatenates only the `corrections` key back onto the
        stored document, so `payload` — the agent's own output — survives every
        later decision untouched. Same reason `record_transition` leaves `body`
        out of its UPDATE: the correction set is measured against what the
        agent said, and a decision able to rewrite that erases the evidence it
        is evidence of.
        """
        document = ProposalBody.to_dict(proposal)
        self._db.execute(
            "INSERT INTO proposals.proposal ("
            "proposal_id, agent, capability, risk_class, feed_id, run_id, state, "
            "payload, confidence, grounding_citations, prompt_hash, "
            "created_by_subject, created_by_type, created_ts, decided_by_subject, "
            "decision_comment, decided_ts, applied_object_type, applied_object_id, "
            "applied_version "
            ") "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (proposal_id) DO UPDATE SET "
            "  state = EXCLUDED.state,"
            "  confidence = EXCLUDED.confidence,"
            "  decided_by_subject = EXCLUDED.decided_by_subject,"
            "  decision_comment = EXCLUDED.decision_comment,"
            "  decided_ts = EXCLUDED.decided_ts,"
            "  applied_object_type = EXCLUDED.applied_object_type,"
            "  applied_object_id = EXCLUDED.applied_object_id,"
            "  applied_version = EXCLUDED.applied_version,"
            "  payload = proposals.proposal.payload || "
            "            jsonb_build_object('corrections', EXCLUDED.payload -> 'corrections')",
            (
                proposal.proposal_id,
                proposal.agent,
                proposal.capability,
                proposal.risk_class.name,
                proposal.feed_id,
                proposal.run_id,
                proposal.state.value,
                json.dumps(document, sort_keys=True, default=str),
                proposal.confidence,
                json.dumps([str(c) for c in proposal.grounding_citations]),
                proposal.prompt_hash or None,
                proposal.created_by.subject,
                proposal.created_by.actor_type.value,
                proposal.created_ts,
                proposal.decided_by.subject if proposal.decided_by else None,
                proposal.decision_comment or None,
                proposal.decided_ts,
                proposal.applied_object_type.value if proposal.applied_object_type else None,
                proposal.applied_object_id,
                proposal.applied_version,
            ),
        )
        return self.get_proposal(proposal.proposal_id)

    def get_proposal(self, proposal_id: str) -> Proposal:
        row = self._db.fetch_one(
            "SELECT "
            "proposal_id, agent, capability, risk_class, feed_id, run_id, state, "
            "payload, confidence, grounding_citations, prompt_hash, "
            "created_by_subject, created_by_type, created_ts, decided_by_subject, "
            "decision_comment, decided_ts, applied_object_type, applied_object_id, "
            "applied_version "
            "FROM proposals.proposal WHERE proposal_id = %s",
            (proposal_id,),
        )
        if row is None:
            raise ObjectNotFoundError(f"no proposal {proposal_id!r}")
        return _proposal(row)

    def list_proposals(
        self,
        *,
        feed_id: str | None = None,
        agent: str | None = None,
        state: ProposalState | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        statement = (
            "SELECT "
            "proposal_id, agent, capability, risk_class, feed_id, run_id, state, "
            "payload, confidence, grounding_citations, prompt_hash, "
            "created_by_subject, created_by_type, created_ts, decided_by_subject, "
            "decision_comment, decided_ts, applied_object_type, applied_object_id, "
            "applied_version "
            "FROM proposals.proposal WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if feed_id is not None:
            statement += " AND feed_id = %s"
            parameters += (feed_id,)
        if agent is not None:
            statement += " AND agent = %s"
            parameters += (agent,)
        if state is not None:
            statement += " AND state = %s"
            parameters += (state.value,)
        statement += " ORDER BY created_ts DESC, proposal_id DESC LIMIT %s"
        parameters += (limit,)
        return tuple(_proposal(row) for row in self._db.fetch_all(statement, parameters))

    # ── ops.feed_suspension · CF-V1-E3-04 — append-only, no update, no delete ──
    def record_suspension(self, event: SuspensionEvent) -> SuspensionEvent:
        """One INSERT, and there is no other statement against this table in
        the whole file. Append-only is kept the same way the audit ledger's is:
        by the absence of an UPDATE, not by a permission that could be
        misconfigured."""
        self._db.execute(
            "INSERT INTO ops.feed_suspension (suspension_id, feed_id, action, reason, "
            "actor_subject, actor_name, occurred_ts, resumes_after) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                event.feed_id,
                event.action.value,
                event.reason,
                event.actor.subject,
                event.actor.display_name,
                event.occurred_ts,
                event.resumes_after,
            ),
        )
        return event

    def current_suspension(self, feed_id: str) -> Suspension:
        """Folded from the NEWEST row rather than read from a stored flag.

        The ledger is the truth; a second column saying `is_paused` would be a
        second thing to keep in step with it.
        """
        return current(feed_id, tuple(self.list_suspensions(feed_id=feed_id, limit=1)))

    def list_suspensions(
        self, *, feed_id: str | None = None, limit: int = 50
    ) -> Sequence[SuspensionEvent]:
        statement = (
            "SELECT feed_id, action, reason, actor_subject, actor_name, occurred_ts, "
            "resumes_after FROM ops.feed_suspension WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if feed_id is not None:
            statement += " AND feed_id = %s"
            parameters += (feed_id,)
        statement += " ORDER BY occurred_ts DESC, suspension_id DESC LIMIT %s"
        parameters += (limit,)
        return tuple(_suspension(row) for row in self._db.fetch_all(statement, parameters))

    # ── ops.action_record · CF-V2-E12-03/E8-04 — append-only, one row per phase ──
    def record_action_event(self, row: ActionRecordRow) -> ActionRecordRow:
        """One INSERT; like the suspension ledger, append-only is kept by the
        absence of an UPDATE in this file, not by a permission. `occurred_ts`
        is derived from the record's own timestamps — this adapter never
        consults a clock, so replaying a write is idempotent in content."""
        record = row.record
        self._db.execute(
            "INSERT INTO ops.action_record (event_id, record_id, batch_id, feed_id, action, "
            "phase, actor_subject, actor_name, reason, approval_identifier, outcome, "
            "observed_state, requested_ts, verified_ts, occurred_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                row.record_id,
                record.target,
                row.feed_id,
                record.action.value,
                record.phase.value,
                record.actor.subject,
                record.actor.display_name,
                record.reason,
                record.approval_identifier,
                record.outcome,
                record.observed_state.value if record.observed_state else None,
                record.requested_ts,
                record.verified_ts,
                row.occurred_ts,
            ),
        )
        return row

    def get_action_record(self, record_id: str) -> ActionRecordRow:
        rows = self._db.fetch_all(
            "SELECT record_id, feed_id, batch_id, action, phase, actor_subject, actor_name, "
            "reason, approval_identifier, outcome, observed_state, requested_ts, verified_ts "
            "FROM ops.action_record WHERE record_id = %s "
            "ORDER BY occurred_ts DESC, event_id DESC LIMIT 1",
            (record_id,),
        )
        if not rows:
            raise ObjectNotFoundError(f"action record {record_id} was never written")
        return _action_row(rows[0])

    def list_action_records(
        self, *, batch_id: str | None = None, feed_id: str | None = None, limit: int = 50
    ) -> Sequence[ActionRecordRow]:
        statement = (
            "SELECT DISTINCT ON (record_id) record_id, feed_id, batch_id, action, phase, "
            "actor_subject, actor_name, reason, approval_identifier, outcome, observed_state, "
            "requested_ts, verified_ts FROM ops.action_record WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if batch_id is not None:
            statement += " AND batch_id = %s"
            parameters += (batch_id,)
        if feed_id is not None:
            statement += " AND feed_id = %s"
            parameters += (feed_id,)
        statement += " ORDER BY record_id, occurred_ts DESC, event_id DESC"
        current_rows = tuple(_action_row(row) for row in self._db.fetch_all(statement, parameters))
        newest_first = sorted(current_rows, key=lambda row: row.occurred_ts, reverse=True)
        return tuple(newest_first[:limit])

    # ── ops.incident_event · CF-V2-E12-04 — append-only, one row per transition ──
    def record_incident_event(self, event: IncidentEvent) -> IncidentEvent:
        self._db.execute(
            "INSERT INTO ops.incident_event (event_id, incident_id, batch_id, feed_id, "
            "signature, state, actor_subject, acknowledged_by, assigned_to, resolution, "
            "opened_ts, resolved_ts, occurred_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                event.incident_id,
                event.batch_id,
                event.feed_id,
                event.signature,
                event.state.value,
                event.actor_subject,
                event.acknowledged_by,
                event.assigned_to,
                event.resolution,
                event.opened_ts,
                event.resolved_ts,
                event.occurred_ts,
            ),
        )
        return event

    def get_incident_event(self, incident_id: str) -> IncidentEvent:
        rows = self._db.fetch_all(
            "SELECT incident_id, batch_id, feed_id, signature, state, actor_subject, "
            "acknowledged_by, assigned_to, resolution, opened_ts, resolved_ts, occurred_ts "
            "FROM ops.incident_event WHERE incident_id = %s "
            "ORDER BY occurred_ts DESC, event_id DESC LIMIT 1",
            (incident_id,),
        )
        if not rows:
            raise ObjectNotFoundError(f"incident {incident_id} has no events")
        return _incident_event(rows[0])

    def list_incident_events(
        self,
        *,
        batch_id: str | None = None,
        feed_id: str | None = None,
        state: IncidentState | None = None,
        limit: int = 50,
    ) -> Sequence[IncidentEvent]:
        statement = (
            "SELECT DISTINCT ON (incident_id) incident_id, batch_id, feed_id, signature, "
            "state, actor_subject, acknowledged_by, assigned_to, resolution, opened_ts, "
            "resolved_ts, occurred_ts FROM ops.incident_event WHERE 1=1"
        )
        parameters: tuple[Any, ...] = ()
        if batch_id is not None:
            statement += " AND batch_id = %s"
            parameters += (batch_id,)
        if feed_id is not None:
            statement += " AND feed_id = %s"
            parameters += (feed_id,)
        statement += " ORDER BY incident_id, occurred_ts DESC, event_id DESC"
        current_events = tuple(
            _incident_event(row) for row in self._db.fetch_all(statement, parameters)
        )
        # The state filter applies AFTER folding to the current event — in SQL
        # it would filter rows, and an incident whose newest event is CLOSED
        # would reappear as "open" through its older rows.
        filtered = [e for e in current_events if state is None or e.state is state]
        filtered.sort(key=lambda e: e.occurred_ts, reverse=True)
        return tuple(filtered[:limit])
