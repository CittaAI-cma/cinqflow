"""CF-V2-E12-04 wired — the fingerprint-match agent, the gateway and the store.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: that a
KNOWN incident never reaches a model or writes anything, that a NOVEL one
drafts exactly one R2 proposal, that the deterministic nodes reach no model,
and that the drafted guide's payload is the SAME shape `core.proposals.apply`
turns into a real `ObjectType.RUNBOOK` — the one `workers.incidents`'s own
`recovery_guides()` reads back. It proves NOTHING about draft QUALITY; that is
Lane 3's ≥95% precision gate, on the seeded failure library.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core import proposals as proposals_mod
from cinqflow.core.agents.fingerprint_match.graph import (
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    NODE_GATHER,
    NODE_RETRIEVE,
)
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, Layer, RiskClass
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.core.proposals import ProposalState
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import BatchControl, ErrorRecord
from cinqflow.workers import incidents as incidents_worker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

FEED = "acme-claims"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
EARLIER = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
BATCH = "B-9001"
SIBLING_BATCH = "B-8801"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)

#: The story's own worked example — a required key absent in XCom.
ROOT_MESSAGE = (
    "evaluate_bronze_load: required key 'business_date' absent in XCom from upstream validate_input"
)


def _error(batch_id: str, occurred: datetime) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=f"err-{batch_id}",
        batch_id=batch_id,
        stage=Layer.SILVER_RAW,
        category=ErrorCategory.SCHEMA,
        message=ROOT_MESSAGE,
        occurred_ts=occurred,
    )


def _batch(batch_id: str, business_date: str, started: datetime) -> BatchControl:
    return BatchControl(
        batch_id=batch_id,
        feed_id=FEED,
        feed_version=1,
        business_date=business_date,
        state=BatchState.FAILED,
        started_ts=started,
    )


def _novel_incident(batch_id: str = BATCH, occurred: datetime = NOW) -> fingerprinting.Incident:
    return fingerprinting.fingerprint_batch(
        batch_id=batch_id,
        feed_id=FEED,
        errors=(_error(batch_id, occurred),),
        guides=(),
        now=occurred,
    )


def _published(obj):  # type: ignore[no-untyped-def]
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=STEWARD,
        approved_ts=NOW,
    )


@pytest.fixture
def control() -> MemStoreControlTables:
    tables = MemStoreControlTables()
    tables.open_batch(_batch(SIBLING_BATCH, "2026-08-29", EARLIER))
    tables.record_error(_error(SIBLING_BATCH, EARLIER))
    tables.open_batch(_batch(BATCH, "2026-08-30", NOW))
    tables.record_error(_error(BATCH, NOW))
    return tables


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=BA, now=NOW)))
    return metadata


def _seed_sibling(store: MemMetadataDb) -> fingerprinting.Incident:
    """An OTHER incident, still open, carrying the EXACT same fingerprint —
    the near-miss precedent `retrieve` is built to find."""
    sibling = _novel_incident(SIBLING_BATCH, EARLIER)
    store.record_incident_event(
        fingerprinting.event_for(sibling, actor_subject="platform@cinqflow", occurred_ts=EARLIER)
    )
    return sibling


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


def _agent(
    store: MemMetadataDb, control: MemStoreControlTables, llm: ScriptedLlm
) -> FingerprintMatchAgent:
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


def _draft_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "title": "Novel schema failure at silver_raw",
        "steps": ["Check the upstream task's XCom for the missing key.", "Re-run once fixed."],
        "remedy": "retry",
        "is_transient": True,
        "confidence": 0.9,
        "rationale": "Looks like a flaky upstream write.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _narrate_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "narrative": "This exact failure is already open elsewhere.",
        "citations": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _responder(narrate: str, draft: str):  # type: ignore[no-untyped-def]
    def respond(prompt: str, task: TaskClass) -> str:
        return narrate if task is TaskClass.SMALL else draft

    return respond


# ── the deterministic half reaches no model ──────────────────────────────────


def test_the_deterministic_nodes_never_reach_the_gateway() -> None:
    """Asserted by walking the AST, not by reading the code — the same idiom
    `mapping_suggestion`/`rule_authoring`/`schema_inference` use for their own
    ground/assemble nodes."""
    from cinqflow.intelligence.agents import fingerprint_match as wired

    tree = ast.parse(inspect.getsource(wired))
    bodies = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"_gather", "_retrieve"}
    }
    assert set(bodies) == {"_gather", "_retrieve"}, "both deterministic nodes must exist"

    for name, node in bodies.items():
        attributes = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}
        assert "llm" not in attributes, f"{name} reaches the gateway"
        assert "complete" not in attributes, f"{name} calls a model"

    assert {NODE_GATHER, NODE_RETRIEVE} == DETERMINISTIC_NODES


def test_the_graph_package_imports_no_runtime() -> None:
    """`graphs-are-data` as a test as well as an import-linter contract."""
    source = Path("src/cinqflow/core/agents/fingerprint_match/graph.py").read_text()
    tree = ast.parse(source)
    imported_modules = tuple(
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    )
    roots = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {m.split(".")[0] for m in imported_modules}
    assert "langgraph" not in roots
    assert not any(m.startswith(("cinqflow.adapters", "cinqflow.ports")) for m in imported_modules)


# ── a KNOWN incident never reaches this agent at all ─────────────────────────


def test_a_known_incident_never_reaches_the_model_or_writes_a_proposal(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """The already-existing deterministic machinery answered this question
    before the agent was ever called; the graph must not run at all."""
    root = _error(BATCH, NOW)
    guide_sig = fingerprinting.signature(
        stage=root.stage, category=root.category, message=root.message, rule_id=root.rule_id
    )
    guide = fingerprinting.RecoveryGuide(
        guide_id="RB-1", title="Known fix", signatures=frozenset({guide_sig}), steps=("Do X",)
    )
    known = fingerprinting.fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=(root,), guides=(guide,), now=NOW
    )
    assert known.kind is fingerprinting.IncidentKind.KNOWN

    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, control, llm).propose(known, caller=BA, run_id="R-known", now=NOW)

    assert result.proposal is None
    assert result.model_called is False
    assert llm.calls == []
    assert store.list_proposals(feed_id=FEED) == ()


def test_an_incident_with_no_signature_is_also_a_no_op(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """`NOVEL` and `no signature to draft a guide for` are not the same thing,
    even though both leave `match` as `None`."""
    empty = fingerprinting.fingerprint_batch(batch_id="B-EMPTY", feed_id=FEED, errors=(), now=NOW)
    assert empty.kind is fingerprinting.IncidentKind.NOVEL
    assert not empty.signature

    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, control, llm).propose(empty, caller=BA, run_id="R-empty", now=NOW)

    assert result.proposal is None
    assert llm.calls == []


# ── a NOVEL incident drafts exactly one R2 proposal ──────────────────────────


def test_a_novel_incident_drafts_exactly_one_r2_proposal(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-1", now=NOW)

    assert result.proposal is not None
    assert result.proposal.state is ProposalState.PENDING_REVIEW
    assert result.proposal.risk_class is RiskClass.R2
    assert result.proposal.created_by.actor_type is ActorType.AI
    assert len(store.list_proposals(feed_id=FEED)) == 1
    assert list(store.list(ObjectType.RUNBOOK)) == [], "no runbook was created — only proposed"


def test_the_proposal_carries_the_incidents_own_citation(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-1", now=NOW)
    assert result.proposal is not None
    assert str(incident.citation) in {str(c) for c in result.proposal.grounding_citations}


def test_a_batch_with_no_near_miss_and_no_reference_hit_skips_narrate(
    store: MemMetadataDb,
) -> None:
    """`novel` fires, not `has_grounding` — `draft` runs from the evidence
    bundle alone, and the small narrate model is never called."""
    tables = MemStoreControlTables()
    tables.open_batch(_batch(BATCH, "2026-08-30", NOW))
    tables.record_error(
        ErrorRecord(
            error_id_hash="err-unique",
            batch_id=BATCH,
            stage=Layer.SILVER_RAW,
            category=ErrorCategory.SYSTEM,
            message="a genuinely unprecedented failure nobody has ever logged before",
            occurred_ts=NOW,
        )
    )
    incident = fingerprinting.fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=tuple(tables.list_errors(BATCH)),
        guides=(),
        now=NOW,
    )
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, tables, llm).propose(incident, caller=BA, run_id="R-novel", now=NOW)

    assert result.proposal is not None
    small_calls = [t for _, t in llm.calls if t is TaskClass.SMALL]
    assert small_calls == [], "narrate must not run when retrieve found nothing"


def test_a_near_miss_sibling_reaches_narrate_and_the_draft(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """`has_grounding` fires when another still-open incident carries this
    EXACT fingerprint — narrate runs, and the draft carries its citation."""
    sibling = _seed_sibling(store)
    incident = _novel_incident()
    assert incident.signature == sibling.signature

    llm = ScriptedLlm(
        _responder(
            _narrate_answer(citations=[f"batch:{SIBLING_BATCH}"]),
            _draft_answer(),
        )
    )
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-2", now=NOW)

    assert result.near_miss_count == 1
    small_calls = [t for _, t in llm.calls if t is TaskClass.SMALL]
    assert len(small_calls) == 1, "narrate ran exactly once"
    assert result.proposal is not None
    assert f"batch:{SIBLING_BATCH}" in {str(c) for c in result.proposal.grounding_citations}


def test_narrates_hallucinated_citation_is_dropped(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    _seed_sibling(store)
    incident = _novel_incident()
    llm = ScriptedLlm(
        _responder(_narrate_answer(citations=["batch:not-a-real-one"]), _draft_answer())
    )
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-3", now=NOW)
    assert result.proposal is not None
    assert "batch:not-a-real-one" not in {str(c) for c in result.proposal.grounding_citations}


# ── the platform, not the model, decides what a remedy is worth ─────────────


def test_a_confident_remedy_reaches_the_draft(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer(remedy="retry", confidence=0.9)))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-4", now=NOW)
    assert result.drafted is not None
    assert result.drafted.guide.remedy is not None
    assert result.drafted.guide.remedy.value == "retry"
    record = result.proposal.payload["records"][0]  # type: ignore[union-attr]
    assert record["remedy"] == "retry"


def test_a_low_confidence_remedy_is_dropped_but_the_steps_survive(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    assert CONFIDENCE_FLOOR > 0.3
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer(remedy="retry", confidence=0.3)))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-5", now=NOW)
    assert result.drafted is not None
    assert result.drafted.guide.remedy is None
    assert any("below the platform's floor" in r for r in result.drafted.refusals)
    assert result.drafted.guide.title
    assert result.drafted.guide.steps


def test_an_uncertified_remedy_name_is_discarded(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(
        _responder(_narrate_answer(), _draft_answer(remedy="delete_everything", confidence=0.95))
    )
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-6", now=NOW)
    assert result.drafted is not None
    assert result.drafted.guide.remedy is None
    assert any("not a certified OpsAction" in r for r in result.drafted.refusals)


def test_every_refusal_leaves_an_agent_action_row(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(
        _responder(_narrate_answer(), _draft_answer(remedy="delete_everything", confidence=0.95))
    )
    _agent(store, control, llm).propose(incident, caller=BA, run_id="R-7", now=NOW)
    actions = store.read_agent_actions(agent="fingerprint-match")
    assert any("not a certified OpsAction" in a.detail for a in actions)


def test_the_drafted_guide_has_the_signature_of_the_incident_it_answers(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer()))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-8", now=NOW)
    assert result.drafted is not None
    assert result.drafted.guide.signatures == frozenset({incident.signature})
    assert result.drafted.guide.guide_id.startswith("DRAFT-")


# ── the manual path degrades honestly ────────────────────────────────────────


def test_narrate_failing_still_lets_draft_run(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """`narrate` degrading must not cost the incident its draft — the one
    thing this agent exists to produce."""
    _seed_sibling(store)
    incident = _novel_incident()

    def respond(prompt: str, task: TaskClass) -> str:
        if task is TaskClass.SMALL:
            return "not json at all"
        return _draft_answer()

    llm = ScriptedLlm(respond)
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-9", now=NOW)

    assert result.proposal is not None
    assert result.manual_path is True


def test_draft_failing_reports_no_proposal_rather_than_an_empty_one(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """THE DEFECT THE LANE-3 GATE FOUND ELSEWHERE, guarded here too: a broken
    run must not read as a careful one."""
    incident = _novel_incident()
    llm = ScriptedLlm(lambda p, t: "{ not json")
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-10", now=NOW)

    assert result.proposal is None
    assert result.model_called is True
    assert result.manual_path is True
    assert store.list_proposals(feed_id=FEED) == ()


# ── the whole loop: propose -> approve -> a REAL governed RUNBOOK ───────────


def test_the_draft_survives_approval_and_the_next_occurrence_becomes_known(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """The full story in one loop, mirroring `test_drift_proposal.py`'s
    pattern: propose -> a steward accepts -> a REAL `ObjectType.RUNBOOK`
    appears -> `workers.incidents.recovery_guides` reads it back -> the
    fingerprint the agent drafted FOR is no longer novel.

    Goes through `core.proposals.approve`/`apply` directly rather than the
    HTTP `/api/proposals/{id}/approve` route: that route's agent dispatch
    (`MAPPING_AGENT` / `PHI_AGENT` / the generic CONTRACT fallback) has no
    branch for `fingerprint-match` yet — wiring the operator-facing approval
    SCREEN is a follow-up integration this slab does not include. What this
    test proves is the payload's own shape: that `core.proposals.apply` turns
    it into a real, working `RecoveryGuide`, which is this agent's actual
    design responsibility.
    """
    incident = _novel_incident()
    llm = ScriptedLlm(_responder(_narrate_answer(), _draft_answer(remedy="retry", confidence=0.9)))
    result = _agent(store, control, llm).propose(incident, caller=BA, run_id="R-11", now=NOW)
    assert result.proposal is not None
    record = result.proposal.payload["records"][0]

    decided = proposals_mod.approve(
        result.proposal, approver=STEWARD, comment="matches what we saw on-call", now=NOW
    )
    applied, draft = proposals_mod.apply(
        decided,
        object_type=ObjectType.RUNBOOK,
        object_id=record["guide_id"],
        body={
            "title": record["title"],
            "steps": record["steps"],
            "remedy": record["remedy"],
            "is_transient": record["is_transient"],
            "signatures": record["signatures"],
        },
        version=1,
        now=NOW,
    )
    assert draft.object_type is ObjectType.RUNBOOK
    assert draft.lifecycle_state is LifecycleState.DRAFT
    assert draft.created_by == STEWARD, "the approver authors the object, never the agent"

    store.record_proposal(applied)
    assert applied.state is ProposalState.APPLIED

    # A data steward reviews the DRAFT this test just proved `apply` produced,
    # and publishes it. Saved once, straight to PUBLISHED, rather than saved
    # as DRAFT and then transitioned — `core.lifecycle`'s submit/approve/
    # publish engine has its own suite, and `_published` (the same shortcut
    # `test_mapping_suggestion_agent.py`'s own fixture uses to seed published
    # prompts) reaches PUBLISHED without re-testing that engine here.
    store.save(_published(draft))

    guides = incidents_worker.recovery_guides(store)
    assert any(g.guide_id == record["guide_id"] for g in guides)
    published_guide = next(g for g in guides if g.guide_id == record["guide_id"])
    assert published_guide.remedy is not None
    assert published_guide.remedy.value == "retry"
    assert published_guide.signatures == {incident.signature}

    match = fingerprinting.match_guide(incident.signature, guides)
    assert match is not None

    reoccurred = fingerprinting.fingerprint_batch(
        batch_id="B-REOCCUR",
        feed_id=FEED,
        errors=(_error("B-REOCCUR", NOW),),
        guides=guides,
        now=NOW,
    )
    assert reoccurred.kind is fingerprinting.IncidentKind.KNOWN, (
        "the next occurrence of the SAME fingerprint is now recognised — the entire point"
    )
