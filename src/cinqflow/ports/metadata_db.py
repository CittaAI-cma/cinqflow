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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from cinqflow.core.model.agent_action import AgentAction
from cinqflow.core.model.governed import AuditEntry, GovernedObject, ObjectType
from cinqflow.core.profiling import FileProfile
from cinqflow.core.proposals import Proposal, ProposalState


@dataclass(frozen=True)
class FileProfileRecord:
    """One stored profiling run — the facts, plus who ran them and when.

    The profile itself is content-addressed and carries no clock, so the two
    fields that DO vary between runs live out here where they cannot disturb
    the fingerprint.
    """

    feed_id: str
    profile: FileProfile
    profiled_by: str
    profiled_ts: datetime

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id


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

    def record_transition(self, obj: GovernedObject, entry: AuditEntry) -> GovernedObject:
        """Persist a LIFECYCLE TRANSITION of an existing version — state and
        approver columns only, never the body — together with its audit row.

        One verb for both writes, deliberately: `transition_to` returns the
        moved object AND its entry as a pair, and a port that let a caller
        persist one without the other would un-make that guarantee at the
        first crash between two calls. Refuses (ObjectNotFoundError) a
        transition of a version that was never saved — a state change to a
        phantom row is a lost approval.
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

    def append_agent_action(self, action: AgentAction) -> None:
        """Append one row to `audit.agent_action`. Append-only, like the rest.

        A separate verb rather than a variant of `append_audit` because an
        agent action carries what a governance entry does not — prompt hash,
        model version, tokens, cost — and folding them into a free-text detail
        field would make "what did the agent cost yesterday?" a grep.
        """
        ...

    def read_agent_actions(
        self, *, run_id: str | None = None, agent: str | None = None, limit: int = 100
    ) -> Sequence[AgentAction]: ...

    # ── computed evidence · CF-V1-E5-01 ──────────────────────────────────────
    #
    # A file profile is NOT a governed object: nobody approves a fact, and it
    # carries no version because re-computing it from the same bytes produces
    # the same answer. It sits behind this pin rather than a new one because it
    # is the platform's own record of its own work — the same Postgres holding
    # the registry, the governance trail and the audit log — and because the
    # things it is evidence FOR are all stored here already. Plate 04's verb
    # for this pin reads `persist_governed_objects_and_audit`; the evidence
    # tables widen it, and the plate says so as of this story.

    def record_profile(self, record: FileProfileRecord) -> FileProfileRecord:
        """Store a profiling run's facts, idempotently.

        Re-profiling an unchanged file must be a no-op, not a second row: the
        profile's id IS the fingerprint of its facts, so an identical run
        collides by construction. The FIRST write wins and keeps its
        timestamp — evidence that has not changed must not look newer for
        having been recomputed, or a stale-evidence gate starts passing things
        it should hold.
        """
        ...

    def get_profile(self, profile_id: str, feed_id: str) -> FileProfileRecord:
        """Raises ObjectNotFoundError, for the reasons named on that class."""
        ...

    def list_profiles(
        self,
        *,
        feed_id: str | None = None,
        profile_id: str | None = None,
        source_fingerprint: str | None = None,
        limit: int = 50,
    ) -> Sequence[FileProfileRecord]:
        """Newest first.

        `source_fingerprint` answers "have we already profiled this exact
        file?", which is what makes re-upload cheap. `profile_id` answers
        "what does `profile:sha256-…` point at?" without the caller having to
        know which feed it was run for — a citation carries an id, not a feed,
        and a citation that cannot be resolved from what it carries is a dead
        end wearing an address.
        """
        ...

    # ── the HITL object · CF-V1-E5-02 and every later R2 agent ───────────────
    #
    # `proposals.proposal` is the ONLY table an agent writes to that a human
    # reads (plus knowledge.*, ops.*, forecasts.* and audit.agent_action).
    # These verbs are on this pin rather than a new one for the same reason
    # the profile verbs are: it is the platform's own record of its own work,
    # in the same Postgres as the registry the proposals become entries in.

    def record_proposal(self, proposal: Proposal) -> Proposal:
        """Insert or update one proposal.

        An UPDATE is permitted here — unlike a governed object, which is
        versioned rather than edited — because a proposal's whole life is a
        state machine over one row: draft, reviewed, decided, applied. What may
        NEVER change is `payload`, and the statement leaves that column out of
        the UPDATE for the same reason `record_transition` leaves out `body`.
        """
        ...

    def get_proposal(self, proposal_id: str) -> Proposal:
        """Raises ObjectNotFoundError, for the reasons named on that class."""
        ...

    def list_proposals(
        self,
        *,
        feed_id: str | None = None,
        agent: str | None = None,
        state: ProposalState | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        """Newest first. `state=PENDING_REVIEW` IS the agent review queue."""
        ...
