"""CF-V1-E5-02 wired — the agent, the gateway, the store and the routes.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: that the
grounding reaches the prompt, that the PHI scrub runs first, that a proposal is
the only thing written, and that approval creates a DRAFT contract authored by
the human. It proves NOTHING about quality.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md

The >= 90% acceptance gate is asserted in `tests/evaluation/`, on the real
endpoint, against the client's own human-written schemas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.api.app import create_app
from cinqflow.core.agents.schema_inference.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.proposals import ProposalState
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = pytest.mark.contract

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
KEY = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/roster.csv"
BA = "dev-ba@cinqcare.test"
READER = "dev-analyst@cinqcare.test"

ROSTER = (
    b"MemberID,First_Name,DOB,LOB,SUBSCR_REL_CD\n"
    b"MBR000001,FIRST000001,19360201,MEDICAID,01\n"
    b"MBR000002,,19370302,MEDICARE,02\n"
    b"MBR000003,FIRST000003,19380403,DUAL,01\n"
)

BG_004 = GlossaryTerm(
    glossary_id="BG-004",
    term="Member Date of Birth",
    definition="Date of birth of the member.",
    mapped_columns_original=("DOB", "Patient_dob"),
    mapped_columns_corrected=("Member_Date_Of_Birth",),
    is_phi=True,
)
BG_001 = GlossaryTerm(
    glossary_id="BG-001",
    term="Member Internal Identifier",
    definition="The internal identifier for a member.",
    mapped_columns_original=("MemberID",),
    mapped_columns_corrected=("Source_Member_Id",),
    is_phi=True,
)
BG_050 = GlossaryTerm(
    glossary_id="BG-050",
    term="Line of Business",
    definition="The product line a member is enrolled under.",
    mapped_columns_original=("LOB",),
    mapped_columns_corrected=("Line_Of_Business",),
    is_phi=False,
)
GLOSSARY = Glossary(terms=(BG_001, BG_004, BG_050))

#: What a competent model returns for the two open columns. Scripted, so the
#: machinery is deterministic — a machinery test that sometimes passes teaches
#: the team to re-run CI.
GOOD_ANSWER = json.dumps(
    {
        "columns": [
            {
                "source_name": "DOB",
                "name": "member_date_of_birth",
                "type": "date",
                "is_phi": True,
                "date_format": "YYYYMMDD",
                "confidence": 0.95,
                "rationale": "Eight-digit values in 1936-1938 with a glossary term naming it.",
            },
            {
                "source_name": "SUBSCR_REL_CD",
                "name": "subscriber_relationship_code",
                "type": "string",
                "is_phi": False,
                "confidence": 0.8,
                "rationale": "A two-character code; the values 01 and 02 are relationship codes.",
            },
            {
                "source_name": "First_Name",
                "name": "first_name",
                "type": "string",
                "is_phi": True,
                "confidence": 0.9,
                "rationale": "A member's given name.",
            },
        ]
    }
)


def _agent(store: MemMetadataDb, llm: ScriptedLlm) -> SchemaInferenceAgent:
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=Decimal("0.01"),
        clock=lambda: NOW,
    )
    return SchemaInferenceAgent(llm=gateway, metadata=store)


def _published(obj):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    author = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=author, now=NOW)))
    for term in GLOSSARY.terms:
        metadata.save(term.as_governed(author=author, now=NOW))
    return metadata


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return profile_bytes(ROSTER, file_format="csv", source_key=KEY, source_fingerprint="sha256-aaa")


# ── the guardrail trio, for this agent ───────────────────────────────────────


def test_phi_is_scrubbed_before_the_prompt_reaches_the_model(store, profile) -> None:  # type: ignore[no-untyped-def]
    """The gateway's stage ordering is checked at runtime; this asserts it
    holds for THIS agent's grounding, which carries example values from a
    payer's file."""
    llm = ScriptedLlm(lambda prompt, task: GOOD_ANSWER)
    caller = Actor(subject=BA, actor_type=ActorType.HUMAN)
    phi_roster = (
        b"MemberID,SSN,DOB\nMBR000001,123-45-6789,19360201\nMBR000002,987-65-4321,19370302\n"
    )
    _agent(store, llm).propose(
        profile_bytes(phi_roster, file_format="csv", source_fingerprint="sha256-b"),
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=caller,
        now=NOW,
    )
    sent = "\n".join(prompt for prompt, _ in llm.calls)
    assert "123-45-6789" not in sent, "an SSN reached a model"
    assert "987-65-4321" not in sent


def test_the_agent_writes_a_proposal_and_nothing_else(store, profile) -> None:  # type: ignore[no-untyped-def]
    """ "No agent path writes production state." Asserted on the STORE, not on
    the source: after a run, there is a proposal and no new contract."""
    before = len(store.list(ObjectType.CONTRACT))
    result = _agent(store, ScriptedLlm(lambda p, t: GOOD_ANSWER)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert result.proposal.state is ProposalState.PENDING_REVIEW
    assert len(store.list_proposals(feed_id=FEED)) == 1
    assert len(store.list(ObjectType.CONTRACT)) == before, "no contract was created"


def test_the_proposal_is_r2_and_created_by_an_ai_actor(store, profile) -> None:  # type: ignore[no-untyped-def]
    result = _agent(store, ScriptedLlm(lambda p, t: GOOD_ANSWER)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert result.proposal.risk_class.name == "R2"
    assert result.proposal.created_by.actor_type is ActorType.AI
    assert result.proposal.prompt_hash, "every model call carries its prompt hash"


# ── the zero-token path ──────────────────────────────────────────────────────


def test_a_wholly_settled_file_calls_no_model_at_all(store) -> None:  # type: ignore[no-untyped-def]
    """Not a cheap call. None. The deterministic-first rule, on an invoice."""
    llm = ScriptedLlm(lambda p, t: GOOD_ANSWER)
    settled = profile_bytes(
        b"MemberID,LOB\nMBR000001,MEDICAID\nMBR000002,MEDICARE\n",
        file_format="csv",
        source_fingerprint="sha256-c",
    )
    result = _agent(store, llm).propose(
        settled,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert llm.calls == [], "a model was called for a file nobody needed to ask about"
    assert result.model_called is False
    assert result.cost_usd == Decimal("0")
    assert all(c.settled_by == "computation" for c in result.columns)
    assert result.needs_input == ()


# ── what the platform refuses to accept ──────────────────────────────────────


def test_a_column_the_model_invented_is_discarded(store, profile) -> None:  # type: ignore[no-untyped-def]
    """A contract field with no source column cannot be mapped, tested or
    approved — so it never reaches the review screen as a field."""
    invented = json.dumps(
        {
            "columns": [
                {
                    "source_name": "TOTALLY_MADE_UP",
                    "name": "x",
                    "type": "string",
                    "confidence": 0.99,
                }
            ]
        }
    )
    result = _agent(store, ScriptedLlm(lambda p, t: invented)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert "TOTALLY_MADE_UP" not in {c.source_name for c in result.columns}
    assert any("not a column in the profiled file" in r for r in result.refusals)


def test_a_low_confidence_column_becomes_needs_your_input(store, profile) -> None:  # type: ignore[no-untyped-def]
    """The floor is the PLATFORM's, not the prompt's — a model asked to
    self-censor at a number will report that number."""
    unsure = json.dumps(
        {
            "columns": [
                {
                    "source_name": "SUBSCR_REL_CD",
                    "name": "subscriber_relationship_code",
                    "type": "string",
                    "confidence": 0.2,
                    "rationale": "Guessing from the abbreviation.",
                }
            ]
        }
    )
    result = _agent(store, ScriptedLlm(lambda p, t: unsure)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    column = next(c for c in result.columns if c.source_name == "SUBSCR_REL_CD")
    assert column.needs_input is True
    assert "below the platform's floor" in column.rationale


def test_a_column_the_model_declines_is_needs_your_input_not_a_string(store, profile) -> None:  # type: ignore[no-untyped-def]
    """ "Do NOT type a column as `string` to avoid saying you are unsure."

    Declining is a correct answer, and it must survive to the screen as one.
    """
    declined = json.dumps(
        {
            "columns": [
                {
                    "source_name": "SUBSCR_REL_CD",
                    "confidence": 0.95,
                    "needs_input": True,
                    "rationale": "No glossary term and no pattern identifies this code set.",
                }
            ]
        }
    )
    result = _agent(store, ScriptedLlm(lambda p, t: declined)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    column = next(c for c in result.columns if c.source_name == "SUBSCR_REL_CD")
    assert column.needs_input is True
    assert column.name is None, "the model declined to name it, and nothing invented one"
    # The TYPE the arithmetic determined still travels — blanking the whole row
    # would throw away a fact nobody disputes, and a BA filling this gap is
    # answering one question rather than re-doing the column.
    assert column.type == "string"
    assert "code set" in column.rationale


def test_a_phi_downgrade_is_refused_and_surfaces_on_the_proposal(store, profile) -> None:  # type: ignore[no-untyped-def]
    downgrade = json.dumps(
        {
            "columns": [
                {
                    "source_name": "DOB",
                    "name": "member_date_of_birth",
                    "type": "date",
                    "is_phi": False,
                    "confidence": 0.99,
                }
            ]
        }
    )
    result = _agent(store, ScriptedLlm(lambda p, t: downgrade)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    dob = next(c for c in result.columns if c.source_name == "DOB")
    assert dob.is_phi is True
    assert any("clearing the PHI flag" in r for r in result.refusals)
    assert result.proposal.payload["refusals"], "the attempt reaches the review screen"


def test_a_model_that_never_returns_valid_output_degrades_to_the_manual_path(
    store, profile
) -> None:  # type: ignore[no-untyped-def]
    """ "The feature degrades to its manual path, and Operations sees the event
    — never a silent hang or a surprise bill." Every open column becomes
    "needs your input", which is the screen a BA would have used anyway."""
    result = _agent(store, ScriptedLlm(lambda p, t: "not json at all")).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert {c.source_name for c in result.needs_input} == {"First_Name", "DOB", "SUBSCR_REL_CD"}
    assert all(c.settled_by == "computation" for c in result.columns if not c.needs_input)


def test_the_overall_confidence_is_the_weakest_column_not_the_mean(store, profile) -> None:  # type: ignore[no-untyped-def]
    """A contract is approved as a whole, and averaging lets forty easy columns
    hide the one the agent barely guessed at."""
    result = _agent(store, ScriptedLlm(lambda p, t: GOOD_ANSWER)).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    assert result.proposal.confidence == min(c.confidence for c in result.columns)


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(store, tmp_path):  # type: ignore[no-untyped-def]
    landing = LocalFsStorage(root=str(tmp_path))
    landing.place(KEY, ROSTER)
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        storage=landing,
        schema_inference_factory=lambda metadata: _agent(
            metadata, ScriptedLlm(lambda p, t: GOOD_ANSWER)
        ),
    )
    return TestClient(app)


def _as(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}"}


def _profile_and_infer(client: TestClient) -> dict:
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    response = client.post(
        f"/api/feeds/{FEED}/infer-schema",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(BA),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_ba_infers_a_schema_from_a_stored_profile(client: TestClient) -> None:
    body = _profile_and_infer(client)
    assert body["state"] == "pending_review"
    assert body["risk_class"] == "R2"
    assert {c["source_name"] for c in body["columns"]} == {
        "MemberID",
        "First_Name",
        "DOB",
        "LOB",
        "SUBSCR_REL_CD",
    }
    assert body["grounding_citations"], "every proposal cites the facts it read"


def test_each_proposed_column_says_whether_a_model_touched_it(client: TestClient) -> None:
    """A BA reviewing forty columns needs to know which five to look at hard."""
    body = _profile_and_infer(client)
    by_name = {c["source_name"]: c for c in body["columns"]}
    assert by_name["LOB"]["settled_by"] == "computation"
    assert by_name["SUBSCR_REL_CD"]["settled_by"] == "inference"


def test_a_read_only_user_may_not_run_the_agent(client: TestClient) -> None:
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    response = client.post(
        f"/api/feeds/{FEED}/infer-schema",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(READER),
    )
    assert response.status_code == 403


def test_inferring_from_a_profile_that_does_not_exist_is_a_404(client: TestClient) -> None:
    response = client.post(
        f"/api/feeds/{FEED}/infer-schema",
        json={"profile_id": "sha256-nothing"},
        headers=_as(BA),
    )
    assert response.status_code == 404
    assert "profile the sample first" in response.json()["detail"]


def test_approval_creates_a_draft_contract_authored_by_the_approver(
    client: TestClient, store: MemMetadataDb
) -> None:
    """THE SHAPE THAT KEEPS SEGREGATION. The reviewer becomes the contract's
    author, so they cannot then approve it — E11-01's universal negative does
    the rest, with no special case for agent-authored objects."""
    proposal = _profile_and_infer(client)
    response = client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve",
        json={"comment": "looks right", "key_columns": ["source_member_id"]},
        headers=_as(BA),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "applied"

    contract = store.get(ObjectType.CONTRACT, FEED)
    assert contract.lifecycle_state is LifecycleState.DRAFT
    assert contract.created_by.subject == BA
    assert contract.created_by.actor_type is ActorType.HUMAN
    assert contract.body["key_columns"] == ["source_member_id"]


def test_the_approved_contract_is_machine_enforceable(
    client: TestClient, store: MemMetadataDb
) -> None:
    """ "Contract emitted machine-enforceable." The proof: it rebuilds into a
    `SchemaContract` the engine's `cast` and `validate` steps already run."""
    from cinqflow.core.registry.contract import from_governed

    proposal = _profile_and_infer(client)
    client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve",
        json={"key_columns": ["source_member_id"]},
        headers=_as(BA),
    )
    contract = from_governed(store.get(ObjectType.CONTRACT, FEED))
    assert contract.feed_id == FEED
    assert {c.reads_from for c in contract.columns} >= {"MemberID", "DOB", "LOB"}
    assert contract.column("member_date_of_birth").type.value == "date"


def test_a_column_still_needing_input_is_not_typed_into_the_contract(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ "Never silently typed." A field nobody could type is not a field the
    engine can enforce, so it lands on the undecided list instead."""
    declined = json.dumps(
        {"columns": [{"source_name": "SUBSCR_REL_CD", "needs_input": True, "confidence": 0.9}]}
    )
    landing = LocalFsStorage(root=str(tmp_path))
    landing.place(KEY, ROSTER)
    client = TestClient(
        create_app(
            authn=StaticAuthn(),
            metadata_db=store,
            storage=landing,
            schema_inference_factory=lambda m: _agent(m, ScriptedLlm(lambda p, t: declined)),
        )
    )
    proposal = _profile_and_infer(client)
    client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve",
        json={"key_columns": []},
        headers=_as(BA),
    )
    body = store.get(ObjectType.CONTRACT, FEED).body
    assert "SUBSCR_REL_CD" in body["undecided"]
    assert "SUBSCR_REL_CD" not in {c["source_name"] for c in body["columns"]}


def test_a_correction_is_captured_on_the_proposal(client: TestClient) -> None:
    """ "Corrections captured to eval set." They travel WITH the approval —
    recorded later would under-count every crash in between."""
    proposal = _profile_and_infer(client)
    response = client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve",
        json={
            "comment": "SUBSCR_REL_CD is a relationship code, but we call it relationship_code",
            "columns": [
                {"source_name": "SUBSCR_REL_CD", "name": "relationship_code", "type": "string"}
            ],
        },
        headers=_as(BA),
    ).json()

    corrections = {c["field_path"]: c for c in response["corrections"]}
    assert "SUBSCR_REL_CD.name" in corrections
    assert corrections["SUBSCR_REL_CD.name"]["proposed"] == "subscriber_relationship_code"
    assert corrections["SUBSCR_REL_CD.name"]["accepted"] == "relationship_code"


def test_the_acceptance_rate_separates_the_models_share(client: TestClient) -> None:
    """A 94% contract built from 90% arithmetic is not a claim about a model,
    and the route refuses to let it read as one."""
    proposal = _profile_and_infer(client)
    client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve",
        json={"columns": [{"source_name": "SUBSCR_REL_CD", "name": "relationship_code"}]},
        headers=_as(BA),
    )
    acceptance = client.get(
        f"/api/proposals/{proposal['proposal_id']}/acceptance", headers=_as(BA)
    ).json()

    assert acceptance["total"] == 5
    assert acceptance["corrected"] == 1
    assert acceptance["deterministic_total"] >= 1
    assert acceptance["inferred_corrected"] == 1
    assert acceptance["inferred_rate"] < acceptance["rate"]
    assert "gate 90%" in acceptance["report"]


def test_a_rejection_needs_a_reason(client: TestClient) -> None:
    proposal = _profile_and_infer(client)
    refused = client.post(
        f"/api/proposals/{proposal['proposal_id']}/reject", json={"comment": "  "}, headers=_as(BA)
    )
    assert refused.status_code == 400

    accepted = client.post(
        f"/api/proposals/{proposal['proposal_id']}/reject",
        json={"comment": "DOB is a member id at this payer, not a date"},
        headers=_as(BA),
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "rejected"


def test_a_rejected_proposal_cannot_then_be_approved(client: TestClient) -> None:
    """A proposal somebody can re-run until it passes is not a review."""
    proposal = _profile_and_infer(client)
    client.post(
        f"/api/proposals/{proposal['proposal_id']}/reject",
        json={"comment": "wrong"},
        headers=_as(BA),
    )
    response = client.post(
        f"/api/proposals/{proposal['proposal_id']}/approve", json={}, headers=_as(BA)
    )
    assert response.status_code == 403
    assert "not a legal" in response.json()["detail"]


def test_the_review_queue_lists_pending_proposals(client: TestClient) -> None:
    _profile_and_infer(client)
    queue = client.get("/api/proposals?state=pending_review", headers=_as(BA)).json()
    assert len(queue) == 1
    assert queue[0]["agent"] == "schema-inference"


def test_a_deployment_with_no_llm_pin_says_so_and_keeps_the_manual_path(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    landing = LocalFsStorage(root=str(tmp_path))
    landing.place(KEY, ROSTER)
    client = TestClient(create_app(authn=StaticAuthn(), metadata_db=store, storage=landing))
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    response = client.post(
        f"/api/feeds/{FEED}/infer-schema",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(BA),
    )
    assert response.status_code == 503
    assert "manual contract editor still works" in response.json()["detail"]
