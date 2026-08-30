"""W2-39 · CF-V2-E12-04 — the approve ROUTE, wired for fingerprint-match.

`test_fingerprint_match_agent.py`'s own closing test proves the payload's
SHAPE by driving `core.proposals.approve`/`apply` directly, and says exactly
why: "that route's agent dispatch ... has no branch for `fingerprint-match`
yet". This suite is the follow-up that test named — it drives the same arc
through the real HTTP route, `POST /api/proposals/{id}/approve`, and proves
three things the route-less test could not:

  1. the route does not crash or mis-route a fingerprint-match proposal into
     the CONTRACT-shaped fallback (`_apply_decisions`/`diff_fields` over
     columns that do not exist on a runbook payload);
  2. approving one produces a REAL, retrievable `ObjectType.RUNBOOK`, authored
     by the human who approved it, never the agent;
  3. once published, the SAME fingerprint on a SECOND incident resolves as
     KNOWN via `core.operations.fingerprint.match_guide` — "novel today,
     known tomorrow", the feature's entire reason for existing.

LANE 1. Scripted model, no credentials — the same discipline every other
contract suite for an R2 agent holds to.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.api.app import create_app
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, Layer
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import BatchControl, ErrorRecord
from cinqflow.ports.metadata_db import ObjectNotFoundError
from cinqflow.workers import incidents as incidents_worker

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

FEED = "acme-claims"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
BATCH = "B-9001"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)

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


def _tools(store: MemMetadataDb, control: MemStoreControlTables):  # type: ignore[no-untyped-def]
    from cinqflow.core.model.identity import Principal, Scopes

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
        "remedy": "retry",
        "is_transient": True,
        "confidence": 0.9,
        "rationale": "Looks like a flaky upstream write.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _responder(draft: str):  # type: ignore[no-untyped-def]
    def respond(prompt: str, task: TaskClass) -> str:
        # narrate is never reached here — no near-miss sibling is seeded — but
        # answering harmlessly either way keeps this fixture reusable.
        return draft

    return respond


def _seed_prompts(store: MemMetadataDb) -> None:
    """Published prompt templates — `LlmGateway.complete` reads one back for
    every call, exactly the same seeding `test_fingerprint_match_agent.py`'s
    own `store` fixture does."""
    for template in TEMPLATES:
        obj = template.as_governed(author=BA, now=NOW)
        published = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)
        store.save(
            replace(
                obj,
                lifecycle_state=LifecycleState.PUBLISHED,
                approved_by=published,
                approved_ts=NOW,
            )
        )


def _submit_proposal(store: MemMetadataDb, control: MemStoreControlTables) -> str:
    """Run the REAL agent end to end, exactly as `test_fingerprint_match_agent
    .py` does, so the proposal this route approves is the genuine article —
    not a hand-assembled stand-in for one."""
    _seed_prompts(store)
    gateway = LlmGateway(
        llm=ScriptedLlm(_responder(_draft_answer())),
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    agent = FingerprintMatchAgent(
        llm=gateway, tools=_tools(store, control), runtime=InProcAgentRuntime()
    )
    incident = _novel_incident()
    result = agent.propose(incident, caller=BA, run_id="R-approve-route", now=NOW)
    assert result.proposal is not None
    return result.proposal.proposal_id


def _app(store: MemMetadataDb):  # type: ignore[no-untyped-def]
    return create_app(authn=StaticAuthn(), metadata_db=store)


def _headers(subject: str = "dev-ba@cinqcare.test") -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


# ── the route no longer crashes or mis-routes a runbook proposal ────────────


def test_approving_a_fingerprint_match_proposal_does_not_reach_the_contract_path() -> None:
    """Before W2-39 this either raised inside `diff_fields` (a runbook payload
    has no `source_name` records) or, worse, wrote `ObjectType.CONTRACT` for
    the feed from a payload that is not a contract at all. Neither happens
    now: the route recognises the agent and takes the runbook door."""
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    proposal_id = _submit_proposal(store, control)

    with TestClient(_app(store)) as client:
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "matches what we saw on-call"},
            headers=_headers(),
        )

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["applied_object_type"] == "runbook"
    assert list(store.list(ObjectType.CONTRACT)) == [], (
        "a fingerprint-match proposal must never write a CONTRACT"
    )


def test_accepting_produces_a_real_retrievable_draft_runbook_authored_by_the_human() -> None:
    """The agent's output enters the world exactly where a hand-typed draft
    would: DRAFT, authored by the APPROVER, never by the agent."""
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    proposal_id = _submit_proposal(store, control)
    guide_id = store.get_proposal(proposal_id).payload["records"][0]["guide_id"]

    with TestClient(_app(store)) as client:
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "matches what we saw on-call"},
            headers=_headers(),
        )
    assert accepted.status_code == 200, accepted.text

    stored = store.get(ObjectType.RUNBOOK, guide_id)
    assert stored.object_type is ObjectType.RUNBOOK
    assert stored.version == 1
    assert stored.lifecycle_state is LifecycleState.DRAFT
    assert stored.created_by.subject == "dev-ba@cinqcare.test"
    assert stored.created_by.actor_type is ActorType.HUMAN, (
        "the reviewer authors the runbook, never the agent"
    )
    assert stored.body["title"] == "Novel schema failure at silver_raw"
    assert stored.body["remedy"] == "retry"
    assert stored.body["is_transient"] is True
    assert stored.body["steps"], "the steps a human reads must survive"

    decided = store.get_proposal(proposal_id)
    assert decided.applied_object_type is ObjectType.RUNBOOK
    assert decided.applied_object_id == guide_id
    assert decided.decided_by is not None
    assert decided.decided_by.subject == "dev-ba@cinqcare.test"


def test_a_second_proposal_for_the_same_guide_versions_rather_than_collides() -> None:
    """The FIRST-time version numbering follows the same rule the contract and
    mapping paths already use: `max(history) + 1`, which is 1 when there is no
    history yet."""
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    proposal_id = _submit_proposal(store, control)

    with TestClient(_app(store)) as client:
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "first pass"},
            headers=_headers(),
        )
    assert accepted.json()["applied_version"] == 1


# ── the whole arc: propose -> approve (HTTP) -> publish -> KNOWN next time ──


def _publish(store: MemMetadataDb, draft: GovernedObject) -> GovernedObject:
    """Move the ALREADY-SAVED draft straight to PUBLISHED, the way a data
    steward's publish action would.

    Through `record_transition` — the same persistence seam `app.py`'s own
    `publish_object` route ends at — rather than a second `save()` at the same
    version, which the store correctly refuses: the HTTP approve route this
    test just drove already persisted version 1 as DRAFT, so publishing it is
    a STATE CHANGE to that row, not a new one.
    """
    steward = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)
    published = replace(
        draft, lifecycle_state=LifecycleState.PUBLISHED, approved_by=steward, approved_ts=NOW
    )
    entry = AuditEntry(
        object_type=draft.object_type,
        object_id=draft.object_id,
        version=draft.version,
        action="transition:published",
        actor=steward,
        occurred_ts=NOW,
        from_state=draft.lifecycle_state,
        to_state=LifecycleState.PUBLISHED,
    )
    return store.record_transition(published, entry)


def test_novel_today_known_tomorrow_through_the_real_approve_route() -> None:
    """The arc the whole feature exists for, driven through the HTTP route
    rather than around it:

        propose (agent) -> approve (POST /api/proposals/{id}/approve)
        -> a real DRAFT RUNBOOK -> published -> `recovery_guides()` reads it
        back -> the SAME fingerprint on a second incident is now KNOWN.
    """
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    first_incident = _novel_incident()
    assert first_incident.kind is fingerprinting.IncidentKind.NOVEL

    proposal_id = _submit_proposal(store, control)
    guide_id = store.get_proposal(proposal_id).payload["records"][0]["guide_id"]

    with TestClient(_app(store)) as client:
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "matches what we saw on-call"},
            headers=_headers(),
        )
    assert accepted.status_code == 200, accepted.text

    draft = store.get(ObjectType.RUNBOOK, guide_id)
    assert draft.lifecycle_state is LifecycleState.DRAFT

    # A data steward reviews and publishes it — straight to PUBLISHED rather
    # than walked through submit-for-review/approve/publish: `core.lifecycle`
    # has its own suite, and this is the same kind of shortcut
    # `test_fingerprint_match_agent.py`'s own closing test uses to reach it.
    _publish(store, draft)

    guides = incidents_worker.recovery_guides(store)
    published_guide = next(g for g in guides if g.guide_id == guide_id)
    assert published_guide.remedy is not None
    assert published_guide.remedy.value == "retry"
    assert published_guide.signatures == {first_incident.signature}

    second_incident = fingerprinting.fingerprint_batch(
        batch_id="B-REOCCUR",
        feed_id=FEED,
        errors=(_error("B-REOCCUR", NOW),),
        guides=guides,
        now=NOW,
    )
    assert second_incident.kind is fingerprinting.IncidentKind.KNOWN, (
        "the next occurrence of the SAME fingerprint is now recognised through the "
        "real approve route — novel today, known tomorrow"
    )
    assert second_incident.match is not None
    assert second_incident.match.guide.guide_id == guide_id


# ── the honest door: no correction model fits a runbook, so accept-as-drafted ─


def test_column_shaped_corrections_in_the_request_body_are_silently_irrelevant() -> None:
    """`ApproveProposalIn.columns`/`.mappings` describe contract fields and
    mapping lines. Sending one against a runbook proposal must not raise and
    must not be mistaken for a real correction — there is no field on a
    runbook they could possibly correct."""
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    proposal_id = _submit_proposal(store, control)

    with TestClient(_app(store)) as client:
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={
                "comment": "accepted",
                "columns": [{"source_name": "whatever", "name": "x"}],
            },
            headers=_headers(),
        )

    assert accepted.status_code == 200, accepted.text
    decided = store.get_proposal(proposal_id)
    assert decided.corrections == (), "a runbook proposal has nothing a column correction fits"


def test_the_proposal_out_response_carries_no_bogus_contract_columns() -> None:
    """Before W2-39's `_proposal_out` guard, a fingerprint-match record would
    fall through the "which agent is this" dispatch and render as a mangled
    `ProposedColumnOut` — the runbook's title/steps read as an empty contract
    column. The response for this agent must render no `columns` at all."""
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    control = MemStoreControlTables()
    proposal_id = _submit_proposal(store, control)

    with TestClient(_app(store)) as client:
        fetched = client.get(f"/api/proposals/{proposal_id}", headers=_headers())
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "accepted"},
            headers=_headers(),
        )

    assert fetched.json()["columns"] == []
    assert accepted.json()["columns"] == []


# ── a missing proposal is still a 404, same as every other agent ────────────


def test_approving_a_missing_proposal_is_still_a_404() -> None:
    from fastapi.testclient import TestClient

    store = MemMetadataDb()
    with pytest.raises(ObjectNotFoundError):
        store.get_proposal("no-such-proposal")

    with TestClient(_app(store)) as client:
        refused = client.post(
            "/api/proposals/no-such-proposal/approve",
            json={"comment": "n/a"},
            headers=_headers(),
        )
    assert refused.status_code == 404
