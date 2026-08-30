"""CF-V2-E12-04 — the incident, written down the moment a batch fails.

The GET route has computed incidents on demand since W2-4; what never existed
was the ROW. A recomputed incident vanishes with the response, so nothing
could be acknowledged, assigned, resolved or closed — and CF-V2-E16-07's
"only a closed one teaches" had nothing that could ever close.

ONE PLAIN SYNCHRONOUS METHOD, exactly like `SlaWorker.materialise` and for
the same reason: `on_batch_failed` is callable directly — from the pipeline's
failure seam, from a test, from a CLI command — and separately reachable
through `workers.consumer.Consumer` once something enqueues failures. The
dispatch mechanism is not this worker's concern.

THE MATCHED PATH NEVER TOUCHES A MODEL. Clustering, fingerprinting and guide
matching are `core.operations.fingerprint` arithmetic — R0, no approval, the
same answer on every machine. The R2 half — W2-38, `FingerprintMatchAgent`
drafting a runbook for a NOVEL fingerprint — is wired below, and only ever
reached for a fingerprint the deterministic half could not match.

IDEMPOTENT ON THE BATCH. A batch that fails, is retried and fails again is
the SAME incident continuing, not a second one — the incident id derives from
the batch and signature, and an opening event is written once. Re-running the
hook on a batch whose incident already has events is a no-op, so the pipeline
can call it on every failure without checking first. The agent call rides the
SAME guard: it happens only inside the branch that writes the opening event,
so a batch replayed through `on_batch_failed` earns at most one drafted
proposal, the same way it earns at most one ledger row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.core.operations import monitor as ops_monitor
from cinqflow.core.operations.actions import OpsAction
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError

#: The subject the opening event names. The platform opened the incident —
#: recording the failing batch's engineer here would blame whoever built the
#: feed for the payer's outage.
PLATFORM_SUBJECT = "platform@cinqflow"

#: The `caller` on every automatic trigger of the fingerprint-match agent.
#: SYSTEM, not AI: this names WHO ASKED (the platform, off a batch failure,
#: with no human principal in hand), never the agent's own authored work —
#: that is `fingerprint_match.AGENT_ACTOR`, which names `created_by` on the
#: proposal itself and is asserted `ActorType.AI` there, unconditionally.
PLATFORM_ACTOR = Actor(
    subject=PLATFORM_SUBJECT, actor_type=ActorType.SYSTEM, display_name="platform"
)


def recovery_guides(metadata: MetadataDbPort) -> tuple[fingerprinting.RecoveryGuide, ...]:
    """The recovery library, from PUBLISHED runbooks.

    Published only: a draft guide is one person's account of what worked once,
    and offering it at 3 AM as the known fix is how a wrong answer becomes the
    recommended one. CF-V2-E16-07 is what keeps them current.
    """
    return tuple(
        fingerprinting.RecoveryGuide(
            guide_id=obj.object_id,
            title=str(obj.body.get("title") or obj.object_id),
            signatures=frozenset(str(s) for s in obj.body.get("signatures", ())),
            steps=tuple(str(step) for step in obj.body.get("steps", ())),
            remedy=(
                OpsAction(obj.body["remedy"]) if obj.body.get("remedy") in set(OpsAction) else None
            ),
            is_transient=bool(obj.body.get("is_transient", False)),
            stale=_feed_retired(metadata, obj.body.get("feed_id")),
        )
        for obj in metadata.list(ObjectType.RUNBOOK)
        if obj.lifecycle_state is LifecycleState.PUBLISHED
    )


def _feed_retired(metadata: MetadataDbPort, feed_id: object) -> bool:
    """CF-V1-W1-26 — staleness is a fact computed HERE, at read time, never a
    field written onto the runbook. A guide's own body records which feed it
    came from; whether that feed has SINCE retired is a fact about a
    DIFFERENT object's lifecycle, and re-versioning every runbook every time
    some feed it references retires would churn a governed object's history
    for a fact that isn't about that object at all. `mirrors
    intelligence.tools._feed_retired` — the identical arithmetic the two
    duplicated `recovery_guides` need, for the layering reason the module
    note at the top of `intelligence.tools` states.

    A guide with no recorded `feed_id` (hand-authored, or from before
    CF-V1-W1-26) is never stale — there is nothing to check it against.
    """
    if not feed_id:
        return False
    try:
        feed = metadata.get(ObjectType.FEED, str(feed_id))
    except ObjectNotFoundError:
        return False
    return feed.lifecycle_state is LifecycleState.RETIRED


def priors_for(
    control: ControlTablesPort,
    *,
    feed_id: str,
    batch_id: str,
    errors: Sequence[ops_monitor.ErrorLike],
) -> tuple[fingerprinting.PriorIncident, ...]:
    """How often this exact failure has happened on this feed before.

    Computed from the control tables rather than a curated count, so "14 prior
    occurrences" is a query somebody can run rather than a number in a
    spreadsheet — the same discipline the board's counters are held to.
    """
    cascade = ops_monitor.separate_cascade(errors)
    root = cascade.first
    if root is None:
        return ()
    found = fingerprinting.signature(
        stage=root.stage,
        category=root.category,
        message=root.message,
        rule_id=root.rule_id,
    )
    priors: list[fingerprinting.PriorIncident] = []
    for prior in control.list_batches(feed_id, 200):
        if prior.batch_id == batch_id:
            continue
        prior_errors = list(control.list_errors(batch_id=prior.batch_id))
        prior_root = ops_monitor.separate_cascade(prior_errors).first
        if prior_root is None:
            continue
        if (
            fingerprinting.signature(
                stage=prior_root.stage,
                category=prior_root.category,
                message=prior_root.message,
                rule_id=prior_root.rule_id,
            )
            != found
        ):
            continue
        priors.append(
            fingerprinting.PriorIncident(
                incident_id=f"INC-{prior.batch_id}",
                occurred_ts=prior.started_ts,
                fix_minutes=(
                    int((prior.completed_ts - prior.started_ts).total_seconds() // 60)
                    if prior.completed_ts
                    else None
                ),
                batch_id=prior.batch_id,
            )
        )
    return tuple(priors)


class IncidentWorker:
    def __init__(
        self,
        *,
        control: ControlTablesPort,
        metadata: MetadataDbPort,
        fingerprint_agent: FingerprintMatchAgent | None = None,
    ) -> None:
        self._control = control
        self._metadata = metadata
        #: Optional, and defaulted to `None`, so every existing caller and
        #: every test that constructs this worker without one keeps working
        #: unchanged. A worker with no agent still opens incidents exactly as
        #: before — it simply never drafts.
        self._fingerprint_agent = fingerprint_agent

    def on_batch_failed(
        self, batch_id: str, *, now: datetime | None = None
    ) -> fingerprinting.Incident:
        """Cluster the batch's errors, match the library, write the OPEN event.

        Deterministic end to end — the R0 boundary made structural. Returns
        the incident either way; only writes when the ledger has never seen
        it, so calling this on every failure is safe.
        """
        stamp = now or datetime.now(UTC)
        batch = self._control.get_batch(batch_id)
        errors = tuple(self._control.list_errors(batch_id=batch_id))
        guides = recovery_guides(self._metadata)
        incident = fingerprinting.fingerprint_batch(
            batch_id=batch_id,
            feed_id=batch.feed_id,
            errors=errors,
            guides=guides,
            history=priors_for(
                self._control, feed_id=batch.feed_id, batch_id=batch_id, errors=errors
            ),
            now=stamp,
        )
        try:
            held = self._metadata.get_incident_event(incident.incident_id)
        except ObjectNotFoundError:
            self._metadata.record_incident_event(
                fingerprinting.event_for(
                    incident, actor_subject=PLATFORM_SUBJECT, occurred_ts=stamp
                )
            )
            # NOVEL only — a matched fingerprint never reaches the agent at
            # all, the same discipline the module docstring states for the
            # deterministic half. `propose` would refuse a KNOWN incident on
            # its own (it is safe to call unconditionally), but checking here
            # too means a matched failure never even attempts the call, and
            # never spends a run_id or a budget cent doing so.
            if (
                self._fingerprint_agent is not None
                and incident.kind is fingerprinting.IncidentKind.NOVEL
            ):
                self._fingerprint_agent.propose(incident, caller=PLATFORM_ACTOR, now=stamp)
            return incident
        # The same failure continuing: fold the decisions people already made
        # onto the fresh evidence, and write nothing. This branch is also
        # what keeps the agent call idempotent — it is reachable only from
        # the branch above, which runs exactly once per batch.
        return fingerprinting.hydrate(incident, held)
