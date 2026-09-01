"""W2-38 · CF-V2-E12-04 — the novel case now actually reaches the agent that
drafts for it.

`IncidentWorker.on_batch_failed` has computed KNOWN/NOVEL deterministically
since W2-33 and, until this slab, stopped there: `FingerprintMatchAgent`
(`test_fingerprint_match_agent.py`) was built, tested and gate-verified in
complete isolation — reachable from nothing the running platform ever called.

These tests prove the WIRING, not the agent's own machinery (that suite still
owns draft quality, refusals, the manual path and the rest): a NOVEL incident
reaches `propose` and lands exactly one drafted proposal in the SAME store
`on_batch_failed` writes its ledger event to; a KNOWN incident never reaches
`propose` AT ALL, proven with a spy rather than by reading a no-op result
(the agent would refuse a KNOWN incident on its own, which is a different
claim from the worker never trying); and the SAME idempotency guard that
already protects the ledger's opening event also protects the proposal,
because the agent is only ever called from inside that one-time branch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, Layer
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import BatchControl, ErrorRecord
from cinqflow.workers.incidents import IncidentWorker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

FEED = "acme-claims"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
BATCH = "B-4200"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)

#: The story's own worked example — a required key absent in XCom.
ROOT_MESSAGE = (
    "evaluate_bronze_load: required key 'business_date' absent in XCom from upstream validate_input"
)


def _error(batch_id: str = BATCH, occurred: datetime = NOW) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=f"err-{batch_id}",
        batch_id=batch_id,
        stage=Layer.SILVER_RAW,
        category=ErrorCategory.SCHEMA,
        message=ROOT_MESSAGE,
        occurred_ts=occurred,
    )


def _batch(batch_id: str = BATCH) -> BatchControl:
    return BatchControl(
        batch_id=batch_id,
        feed_id=FEED,
        feed_version=1,
        business_date="2026-08-30",
        state=BatchState.FAILED,
        started_ts=NOW,
    )


def _published(obj: GovernedObject) -> GovernedObject:
    """The same shortcut `test_fingerprint_match_agent.py` uses: straight to
    PUBLISHED, without re-testing the lifecycle engine that already has its
    own suite."""
    return replace(
        obj, lifecycle_state=LifecycleState.PUBLISHED, approved_by=STEWARD, approved_ts=NOW
    )


@pytest.fixture
def control() -> MemStoreControlTables:
    tables = MemStoreControlTables()
    tables.open_batch(_batch())
    tables.record_error(_error())
    return tables


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=BA, now=NOW)))
    return metadata


def _tools(store: MemMetadataDb, control: MemStoreControlTables) -> ToolContext:
    return ToolContext(
        principal=Principal(
            subject="platform@cinqflow",
            display_name="platform",
            scopes=Scopes(feeds=frozenset({"*"})),
        ),
        control=control,
        metadata=store,
        agent="fingerprint-match",
        now=NOW,
    )


def _draft_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "title": "Novel schema failure at silver_raw",
        "steps": ["Check the upstream task's XCom for the missing key.", "Re-run once fixed."],
        "confidence": 0.9,
        "rationale": "Looks like a flaky upstream write.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _narrate_answer(**overrides: object) -> str:
    payload: dict[str, object] = {"narrative": "", "citations": []}
    payload.update(overrides)
    return json.dumps(payload)


def _responder(narrate: str, draft: str):  # type: ignore[no-untyped-def]
    def respond(prompt: str, task: TaskClass) -> str:
        return narrate if task is TaskClass.SMALL else draft

    return respond


def _agent(store: MemMetadataDb, control: MemStoreControlTables) -> FingerprintMatchAgent:
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    return FingerprintMatchAgent(
        llm=gateway, tools=_tools(store, control), runtime=InProcAgentRuntime()
    )


@dataclass
class _SpyFingerprintAgent:
    """A stand-in that never touches a model — it only records whether
    `propose` was reached at all, which is exactly the claim a KNOWN incident
    must fail: not "returned nothing", but "was never asked"."""

    calls: list[fingerprinting.Incident] = field(default_factory=list)

    def propose(
        self,
        incident: fingerprinting.Incident,
        *,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self.calls.append(incident)


# ── a NOVEL incident reaches the agent and drafts exactly one proposal ──────


def test_a_novel_incident_on_batch_failed_drafts_exactly_one_proposal(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    worker = IncidentWorker(
        control=control, metadata=store, fingerprint_agent=_agent(store, control)
    )

    incident = worker.on_batch_failed(BATCH, now=NOW)

    assert incident.kind is fingerprinting.IncidentKind.NOVEL
    proposals = store.list_proposals(feed_id=FEED)
    assert len(proposals) == 1
    assert proposals[0].agent == "fingerprint-match"
    assert proposals[0].created_by.actor_type is ActorType.AI


# ── a KNOWN incident never reaches propose at all ───────────────────────────


def test_a_known_incident_never_reaches_the_agents_propose(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    root = _error()
    guide_sig = fingerprinting.signature(
        stage=root.stage, category=root.category, message=root.message, rule_id=root.rule_id
    )
    store.save(
        GovernedObject(
            object_type=ObjectType.RUNBOOK,
            object_id="RB-1",
            version=1,
            lifecycle_state=LifecycleState.PUBLISHED,
            created_by=STEWARD,
            created_ts=NOW,
            body={"title": "Known fix", "signatures": [guide_sig], "steps": ["Do X."]},
            approved_by=STEWARD,
            approved_ts=NOW,
        )
    )
    spy = _SpyFingerprintAgent()
    worker = IncidentWorker(control=control, metadata=store, fingerprint_agent=spy)

    incident = worker.on_batch_failed(BATCH, now=NOW)

    assert incident.kind is fingerprinting.IncidentKind.KNOWN
    assert spy.calls == [], "a matched incident must never reach propose at all"
    assert store.list_proposals(feed_id=FEED) == ()


# ── replaying a batch never doubles the proposal ────────────────────────────


def test_on_batch_failed_twice_does_not_double_propose(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    worker = IncidentWorker(
        control=control, metadata=store, fingerprint_agent=_agent(store, control)
    )

    first = worker.on_batch_failed(BATCH, now=NOW)
    again = worker.on_batch_failed(BATCH, now=NOW)

    assert first.incident_id == again.incident_id
    assert len(store.list_proposals(feed_id=FEED)) == 1


def test_a_worker_with_no_agent_still_opens_the_incident(control: MemStoreControlTables) -> None:
    """The default stays `None` — every existing caller that never wired an
    agent keeps opening incidents exactly as before, drafting nothing."""
    metadata = MemMetadataDb()
    worker = IncidentWorker(control=control, metadata=metadata)

    incident = worker.on_batch_failed(BATCH, now=NOW)

    assert incident.kind is fingerprinting.IncidentKind.NOVEL
    assert metadata.list_proposals(feed_id=FEED) == ()
