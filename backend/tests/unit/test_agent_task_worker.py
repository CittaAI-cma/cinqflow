"""AgentTaskWorker — the orchestration around one background agent call.

A fake factory stands in for the real agent: what `SchemaInferenceAgent
.propose` actually decides is already certified by
`test_schema_inference_agent.py`. This file only certifies what this worker
does AROUND that call — state transitions, and that a failure of any kind
(the factory raises, the agent name is unregistered, the profile was never
taken) lands on the job row rather than propagating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.model.vocabulary import AgentJobState
from cinqflow.core.profiling import FileProfile, FileStructure
from cinqflow.ports.metadata_db import AgentJob, FileProfileRecord
from cinqflow.workers.agent_task import AgentTaskWorker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
FEED_ID = "fidelis-downstate-roster"


@dataclass(frozen=True)
class _FakeProposal:
    proposal_id: str


@dataclass(frozen=True)
class _FakeResult:
    proposal: _FakeProposal
    model_called: bool = True


class _FakeAgent:
    """Stands in for `SchemaInferenceAgent` — same `.propose(...)` shape,
    scripted rather than model-backed."""

    def __init__(self, *, proposal_id: str = "prop-1", raises: Exception | None = None) -> None:
        self._proposal_id = proposal_id
        self._raises = raises

    def propose(self, profile: object, *, feed_id: str, glossary: object, caller: object) -> _FakeResult:
        if self._raises is not None:
            raise self._raises
        return _FakeResult(proposal=_FakeProposal(self._proposal_id))


def _seed_profile(metadata: MemMetadataDb, *, profile_id: str = "sha256-deadbeef") -> str:
    profile = FileProfile(
        source_key=f"incoming/{FEED_ID}/roster.csv",
        source_fingerprint=profile_id,
        structure=FileStructure(file_format="csv", encoding="utf-8", declared_encoding="utf-8"),
    )
    metadata.record_profile(
        FileProfileRecord(feed_id=FEED_ID, profile=profile, profiled_by="ba@cinqcare.test", profiled_ts=NOW)
    )
    return profile.profile_id


def _pending_job(job_id: str, *, agent: str = "schema_inference") -> AgentJob:
    return AgentJob(
        job_id=job_id,
        feed_id=FEED_ID,
        agent=agent,
        state=AgentJobState.PENDING,
        requested_ts=NOW,
        requested_by="ba@cinqcare.test",
    )


def _payload(job: AgentJob, *, profile_id: str) -> dict[str, object]:
    return {"job_id": job.job_id, "feed_id": job.feed_id, "profile_id": profile_id}


def test_a_successful_call_completes_the_job_with_its_proposal_id() -> None:
    metadata = MemMetadataDb()
    profile_id = _seed_profile(metadata)
    job = _pending_job("job-1")
    metadata.record_agent_job(job)

    worker = AgentTaskWorker(metadata=metadata, agent_factories={"schema_inference": lambda m: _FakeAgent()})
    worker.handle(_payload(job, profile_id=profile_id))

    completed = metadata.get_agent_job("job-1")
    assert completed.state is AgentJobState.COMPLETED
    assert completed.proposal_id == "prop-1"
    assert completed.error is None
    assert completed.started_ts is not None
    assert completed.completed_ts is not None


def test_the_factory_raising_fails_the_job_rather_than_the_worker() -> None:
    metadata = MemMetadataDb()
    profile_id = _seed_profile(metadata)
    job = _pending_job("job-2")
    metadata.record_agent_job(job)

    worker = AgentTaskWorker(
        metadata=metadata,
        agent_factories={"schema_inference": lambda m: _FakeAgent(raises=RuntimeError("gateway timed out"))},
    )
    worker.handle(_payload(job, profile_id=profile_id))  # must not raise

    failed = metadata.get_agent_job("job-2")
    assert failed.state is AgentJobState.FAILED
    assert failed.proposal_id is None
    assert "gateway timed out" in failed.error


def test_an_unregistered_agent_name_fails_the_job_by_name() -> None:
    metadata = MemMetadataDb()
    profile_id = _seed_profile(metadata)
    job = _pending_job("job-3", agent="mapping_suggestion")
    metadata.record_agent_job(job)

    worker = AgentTaskWorker(metadata=metadata, agent_factories={"schema_inference": lambda m: _FakeAgent()})
    worker.handle(_payload(job, profile_id=profile_id))

    failed = metadata.get_agent_job("job-3")
    assert failed.state is AgentJobState.FAILED
    assert "mapping_suggestion" in failed.error


def test_a_profile_that_was_never_taken_fails_the_job_rather_than_crashing() -> None:
    metadata = MemMetadataDb()
    job = _pending_job("job-4")
    metadata.record_agent_job(job)

    worker = AgentTaskWorker(metadata=metadata, agent_factories={"schema_inference": lambda m: _FakeAgent()})
    worker.handle(_payload(job, profile_id="never-profiled"))

    failed = metadata.get_agent_job("job-4")
    assert failed.state is AgentJobState.FAILED
    assert failed.error
