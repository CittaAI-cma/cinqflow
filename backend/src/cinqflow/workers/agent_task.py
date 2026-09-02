"""CF-V1-E5-02, backgrounded — one agent proposal, off the request thread.

`POST /feeds/{feed_id}/infer-schema` used to call `agent_factory(metadata)
.propose(...)` — an LLM call — INLINE in the request handler, so the caller
waited out the model's full latency. This module is the other end of that
same call, moved onto the queue/consumer pairing `workers.run_feed` already
proves works: the route enqueues a job and returns immediately; this worker
claims it, runs the SAME agent code the route used to call directly, and
records the outcome on the job row the route's poll endpoint reads back.

ONE GENERIC TOPIC, PARAMETERISED BY `agent`. Same "one generic DAG,
parameterised by feed_id" reasoning `workers.scheduler.RUN_FEED_TOPIC` states
for the pipeline queue — a topic per agent would reintroduce per-agent wiring
in the consumer, in the one place this platform keeps removing it.

NEVER RE-RAISES. A job that fails records FAILED on its own row and returns
normally: the failure belongs there, not in the queue's own retry-on-exception
path, which would otherwise just call the same model again for free.

ONLY `schema_inference` IS WIRED TODAY. `detect_phi`/`mapping_suggestion`
/`rule_authoring` stay synchronous — converting them is the same handful of
lines repeated per agent, deliberately not done here in one pass.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType, AgentJobState
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.ports.metadata_db import AgentJob, MetadataDbPort

__all__ = ["AGENT_TASK_TOPIC", "AgentTaskError", "AgentTaskWorker"]

#: One generic topic, not one per agent — see the module docstring.
AGENT_TASK_TOPIC = "agent.run"

#: `payload["agent"]` -> a factory closing over this deployment's LLM gateway,
#: the same shape `api.app`'s `schema_inference_factory` etc. already are.
AgentFactories = dict[str, Callable[[MetadataDbPort], Any]]


class AgentTaskError(RuntimeError):
    """A payload naming an agent this worker has no factory for."""


def _glossary(metadata: MetadataDbPort) -> Glossary:
    """The domain projection `workers.run_feed._glossary` also builds — a
    payer's rename is drift by MEANING, never a dropped column plus a new
    one, and both callers need the same governed terms to say so."""
    return Glossary(
        terms=tuple(
            GlossaryTerm.from_governed(obj) for obj in metadata.list(ObjectType.GLOSSARY_TERM)
        )
    )


def _actor(requested_by: str) -> Actor:
    """Rebuilt from the job row's own string — a background task has no live
    Principal, only who asked for it."""
    return Actor(
        subject=requested_by,
        actor_type=ActorType.HUMAN,
        display_name=requested_by.split("@")[0],
    )


@dataclass(frozen=True)
class AgentTaskWorker:
    """One background agent call, claimed off `AGENT_TASK_TOPIC`."""

    metadata: MetadataDbPort
    agent_factories: AgentFactories

    def handle(self, payload: dict[str, Any]) -> None:
        """The `Consumer` handler registered against `AGENT_TASK_TOPIC`.

        The job row (`state=PENDING`) already exists — the route that
        enqueued this payload wrote it before calling `queue.enqueue`.
        """
        job = self.metadata.get_agent_job(str(payload["job_id"]))
        feed_id = str(payload["feed_id"])
        profile_id = str(payload["profile_id"])

        running = replace(job, state=AgentJobState.RUNNING, started_ts=datetime.now(UTC))
        self.metadata.record_agent_job(running)
        try:
            result = self._propose(job.agent, feed_id=feed_id, profile_id=profile_id, job=job)
        except Exception as exc:  # noqa: BLE001 - the failure belongs on the job row
            self.metadata.record_agent_job(
                replace(running, state=AgentJobState.FAILED, completed_ts=datetime.now(UTC), error=str(exc))
            )
            return

        self.metadata.record_agent_job(
            replace(
                running,
                state=AgentJobState.COMPLETED,
                completed_ts=datetime.now(UTC),
                proposal_id=result.proposal.proposal_id,
            )
        )

    def _propose(self, agent: str, *, feed_id: str, profile_id: str, job: AgentJob) -> Any:
        factory = self.agent_factories.get(agent)
        if factory is None:
            raise AgentTaskError(
                f"{agent!r} has no factory registered on this worker — only "
                f"{sorted(self.agent_factories)} are wired today"
            )
        record = self.metadata.get_profile(profile_id, feed_id)
        return factory(self.metadata).propose(
            record.profile,
            feed_id=feed_id,
            glossary=_glossary(self.metadata),
            caller=_actor(job.requested_by),
        )
