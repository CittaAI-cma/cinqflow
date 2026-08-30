"""CF-V1-E16-05 — the chunk-boundary half, in isolation.

    "Only Published governed objects embed. Draft embeds nothing; Retired
     deletes its chunks."
    — ADR-0007

`core.knowledge` is PURE — no port call, so every guarantee here is provable
without a mock adapter in sight. The wired spine (`workers.knowledge
.KnowledgeIngestWorker`, PHI-verify, embed, index) has its own contract suite
in `tests/contract/test_knowledge_ingestion_pipeline.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.citations import CitationKind
from cinqflow.core.knowledge import (
    KnowledgeSourceError,
    chunk_id_for,
    chunk_incident,
    chunk_runbook,
)
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, ErrorCategory, Layer
from cinqflow.core.operations.fingerprint import IncidentTransitionError, fingerprint_batch
from cinqflow.ports.control_tables import ErrorRecord

pytestmark = pytest.mark.unit

STEWARD = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def _closed_incident(resolution: str = "Re-ran validate_input; business_date restored."):
    error = ErrorRecord(
        error_id_hash="e1",
        batch_id="8842",
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message="required key 'business_date' absent",
        occurred_ts=NOW,
    )
    incident = fingerprint_batch(batch_id="8842", feed_id="fidelis_roster", errors=[error], now=NOW)
    return (
        incident.acknowledge(by=STEWARD.subject)
        .resolve(resolution=resolution, at=NOW + timedelta(minutes=18))
        .close()
    )


def _published_runbook(**body_overrides: object) -> GovernedObject:
    body: dict[str, object] = {
        "title": "Missing business_date",
        "signatures": ["fp-abc123"],
        "steps": ["Check the upstream XCom.", "Re-run validate_input.", "Confirm the row count."],
    }
    body.update(body_overrides)
    return GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body=body,
        approved_by=STEWARD,
        approved_ts=NOW,
    )


# ── chunk_id_for: content-addressed, the idempotency key ────────────────────


def test_the_same_citation_index_and_version_always_produces_the_same_id() -> None:
    citation = _closed_incident().citation
    first = chunk_id_for(citation, index=0, object_version=None)
    second = chunk_id_for(citation, index=0, object_version=None)
    assert first == second


def test_a_different_index_or_version_produces_a_different_id() -> None:
    incident = _closed_incident()
    base = chunk_id_for(incident.citation, index=0, object_version=None)
    assert chunk_id_for(incident.citation, index=1, object_version=None) != base
    assert chunk_id_for(incident.citation, index=0, object_version=2) != base


# ── chunk_incident ────────────────────────────────────────────────────────


def test_a_closed_incident_produces_exactly_one_chunk_carrying_the_narrative() -> None:
    incident = _closed_incident()
    (chunk,) = chunk_incident(incident)
    assert chunk.text == incident.narrative()
    assert chunk.citation == incident.citation
    assert chunk.kind == "incident_narrative"
    assert chunk.feed_id == "fidelis_roster"
    assert f"feed:{incident.feed_id}" in chunk.scope_tags


def test_an_open_incident_is_refused_by_narrative_itself_not_masked_or_skipped() -> None:
    """`chunk_incident` does not re-derive the gate — it reuses `.narrative()`'s
    own refusal, unwrapped."""
    error = ErrorRecord(
        error_id_hash="e2",
        batch_id="9001",
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message="required key 'run_date' absent",
        occurred_ts=NOW,
    )
    open_incident = fingerprint_batch(batch_id="9001", feed_id="fidelis_roster", errors=[error])
    with pytest.raises(IncidentTransitionError):
        chunk_incident(open_incident)


# ── chunk_runbook ────────────────────────────────────────────────────────


def test_a_published_runbook_produces_one_chunk_per_step_not_one_blob() -> None:
    runbook = _published_runbook()
    chunks = chunk_runbook(runbook)
    assert [c.text for c in chunks] == list(runbook.body["steps"])
    assert all(c.kind == "runbook_section" for c in chunks)
    assert [c.citation.fragment for c in chunks] == ["step-1", "step-2", "step-3"]
    assert all(c.citation.kind is CitationKind.RUNBOOK for c in chunks)
    assert all(c.citation.subject == "RB-1" for c in chunks)
    assert all(c.object_version == 1 for c in chunks)


def test_a_draft_runbook_is_refused_never_chunked() -> None:
    """ "Only Published governed objects embed. Draft embeds nothing." — ADR-0007"""
    draft = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-2",
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=STEWARD,
        created_ts=NOW,
        body={"title": "Draft guide", "steps": ["Do the thing."]},
    )
    with pytest.raises(KnowledgeSourceError, match="draft"):
        chunk_runbook(draft)


def test_a_retired_runbook_is_refused_too() -> None:
    published = _published_runbook()
    retired, _ = published.transition_to(LifecycleState.RETIRED, actor=STEWARD)
    with pytest.raises(KnowledgeSourceError, match="retired"):
        chunk_runbook(retired)


def test_a_runbook_with_no_steps_is_refused() -> None:
    empty = _published_runbook(steps=[])
    with pytest.raises(KnowledgeSourceError, match="no steps"):
        chunk_runbook(empty)


def test_a_non_runbook_object_type_is_refused() -> None:
    not_a_runbook = GovernedObject(
        object_type=ObjectType.DQ_RULE,
        object_id="DQ-002",
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body={"steps": ["not a runbook"]},
        approved_by=STEWARD,
        approved_ts=NOW,
    )
    with pytest.raises(KnowledgeSourceError, match="not a runbook"):
        chunk_runbook(not_a_runbook)


def test_re_chunking_the_same_published_version_reproduces_the_same_ids() -> None:
    """The idempotency key survives a second, independent chunking pass —
    which is what lets `VectorPort`'s upsert-by-id keep the store from
    doubling on a re-run."""
    runbook = _published_runbook()
    first = [c.chunk_id for c in chunk_runbook(runbook)]
    second = [c.chunk_id for c in chunk_runbook(runbook)]
    assert first == second
    assert len(set(first)) == len(first), "each step earns its own id"


def test_a_new_version_of_the_same_runbook_gets_different_chunk_ids() -> None:
    v1 = _published_runbook()
    v2 = GovernedObject(
        object_type=ObjectType.RUNBOOK,
        object_id="RB-1",
        version=2,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=STEWARD,
        created_ts=NOW,
        body=v1.body,
        approved_by=STEWARD,
        approved_ts=NOW,
    )
    ids_v1 = {c.chunk_id for c in chunk_runbook(v1)}
    ids_v2 = {c.chunk_id for c in chunk_runbook(v2)}
    assert ids_v1.isdisjoint(ids_v2), (
        "a superseding version must not silently update the prior version's chunks"
    )
