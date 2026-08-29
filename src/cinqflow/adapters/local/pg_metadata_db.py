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
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.ports import port
from cinqflow.ports.metadata_db import ConcurrentVersionError, ObjectNotFoundError


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
