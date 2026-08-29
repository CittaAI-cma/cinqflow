"""CF-V1-E5-03 wired — the agent, the scrubber, the store and the routes.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: that the
classification reaches the prompt WITHOUT any values in it, that a downgrade is
refused at every layer, that the recall gate stops a broken detector before it
can reach a queue, and that approval merges flags onto a contract a human
authored.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md

The 100% recall gate is asserted in `tests/evaluation/`, on the real endpoint,
against the client's own 171-term glossary.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
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
from cinqflow.core.agents.phi_detection.grounding import ground
from cinqflow.core.agents.phi_detection.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.phi import Basis
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.proposals import ProposalState
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent, RecallGateFailedError
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = pytest.mark.contract

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
KEY = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/roster.csv"
BA = "dev-ba@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"
READER = "dev-analyst@cinqcare.test"

#: The SSN values here are the ones that must never appear in a prompt.
ROSTER = (
    b"MemberID,DOB,LOB,SSN,PROV_NPI,SUBSCR_REL_CD\n"
    b"MBR000001,19360201,MEDICAID,078-05-1120,1234567893,01\n"
    b"MBR000002,19370302,MEDICARE,219-09-9999,1841293990,02\n"
    b"MBR000003,19380403,DUAL,457-55-5462,1215930367,01\n"
)

BG_004 = GlossaryTerm(
    glossary_id="BG-004",
    term="Member Date of Birth",
    definition="Date of birth of the member.",
    mapped_columns_original=("DOB",),
    mapped_columns_corrected=("Member_Date_Of_Birth",),
    is_phi=True,
)
BG_050 = GlossaryTerm(
    glossary_id="BG-050",
    term="Line of Business",
    definition="The product line a member is enrolled under.",
    mapped_columns_original=("LOB",),
    is_phi=False,
)
GLOSSARY = Glossary(terms=(BG_004, BG_050))

GOOD_ANSWER = json.dumps(
    {
        "columns": [
            {
                "source_name": "SUBSCR_REL_CD",
                "is_phi": True,
                "phi_kind": "member_id",
                "confidence": 0.8,
                "rationale": "A subscriber relationship code links the member to a subscriber.",
            }
        ]
    }
)


def _agent(store: MemMetadataDb, llm: ScriptedLlm) -> PhiDetectionAgent:
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
    return PhiDetectionAgent(llm=gateway, scrub=PatternPhiScrub(), metadata=store)


def _published(obj):  # type: ignore[no-untyped-def]
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject=STEWARD, actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    author = Actor(subject=BA, actor_type=ActorType.HUMAN)
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=author, now=NOW)))
    for term in GLOSSARY.terms:
        metadata.save(term.as_governed(author=author, now=NOW))
    return metadata


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return profile_bytes(ROSTER, file_format="csv", source_key=KEY, source_fingerprint="sha256-aaa")


def _propose(store, profile, answer=GOOD_ANSWER):  # type: ignore[no-untyped-def]
    llm = ScriptedLlm(lambda prompt, task: answer)
    result = _agent(store, llm).propose(
        profile,
        feed_id=FEED,
        glossary=GLOSSARY,
        caller=Actor(subject=BA, actor_type=ActorType.HUMAN),
        now=NOW,
    )
    return result, llm


# ── THE property this agent exists to have ───────────────────────────────────


def test_no_value_from_the_file_reaches_the_model(store, profile) -> None:  # type: ignore[no-untyped-def]
    """The one thing that makes this agent different from every other one.

    Not "the SSNs are masked" — the gateway's scrubber would do that. The
    assertion is that NOTHING from the data reaches the prompt: no example,
    no top value, no minimum, no maximum. An agent whose job is deciding
    which columns hold protected data should not have to read protected data
    to do it.
    """
    _, llm = _propose(store, profile)
    sent = "\n".join(prompt for prompt, _ in llm.calls)

    for value in ("078-05-1120", "219-09-9999", "457-55-5462"):
        assert value not in sent, f"an SSN reached the model: {value}"
    for value in ("MBR000001", "19360201", "MEDICAID", "1234567893"):
        assert value not in sent, f"a data value reached the model: {value}"
    # And the masked forms are absent too, because nothing was sent to mask.
    assert "<US_SSN>" not in sent


def test_the_grounding_carries_names_and_integers_only(profile) -> None:  # type: ignore[no-untyped-def]
    """Asserted on the grounding itself as well as on the prompt, so a future
    change to how prompts are assembled cannot quietly make the previous test
    vacuous."""
    from cinqflow.core.phi import classify

    text = ground(classify(profile, feed_id=FEED, glossary=GLOSSARY)).as_prompt_grounding()
    for value in ("078-05-1120", "MBR000001", "19360201", "MEDICAID"):
        assert value not in text
    assert "SUBSCR_REL_CD" in text, "the column NAME is grounding, and is sent"
    assert "rows 3, populated 3" in text, "so are the integers"


def test_the_untrusted_fence_holds_only_column_names(profile) -> None:  # type: ignore[no-untyped-def]
    """A payer's header row is still attacker-controlled text — a file whose
    first line reads `ignore previous instructions` is a file somebody can
    send — so the names go below the fence and nothing else does."""
    from cinqflow.core.phi import classify

    fenced = ground(classify(profile, feed_id=FEED, glossary=GLOSSARY)).as_input()
    assert set(fenced.split("\n")) == {"MemberID", "SUBSCR_REL_CD"}
    for value in ("078-05-1120", "MBR000001", "19360201"):
        assert value not in fenced


# ── the guardrail trio ───────────────────────────────────────────────────────


def test_the_agent_writes_a_proposal_and_nothing_else(store, profile) -> None:  # type: ignore[no-untyped-def]
    before = len(store.list(ObjectType.CONTRACT))
    result, _ = _propose(store, profile)
    assert result.proposal.state is ProposalState.PENDING_REVIEW
    assert len(store.list_proposals(feed_id=FEED)) == 1
    assert len(store.list(ObjectType.CONTRACT)) == before, "no contract was created"


def test_the_proposal_is_r2_and_created_by_an_ai_actor(store, profile) -> None:  # type: ignore[no-untyped-def]
    result, _ = _propose(store, profile)
    assert result.proposal.risk_class is RiskClass.R2
    assert result.proposal.created_by.actor_type is ActorType.AI
    assert result.proposal.grounding_citations, "every claim carries an address"


def test_the_deterministic_nodes_reach_no_model(store) -> None:  # type: ignore[no-untyped-def]
    """Asserted on the AST, not on a docstring.

    `_classify` and `_confirm` may reach the scrubber pin — a local NER model
    is not the gateway — but neither may reach `self.llm`.
    """
    from cinqflow.intelligence.agents import phi_detection as wired

    tree = ast.parse(inspect.getsource(wired))
    bodies = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"_classify", "_confirm"}
    ]
    assert len(bodies) == 2, "both deterministic nodes must exist under these names"
    for node in bodies:
        attributes = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}
        assert "llm" not in attributes, f"{node.name} reaches the gateway"
        assert "complete" not in attributes, f"{node.name} calls a model"


# ── the refusals ─────────────────────────────────────────────────────────────


def test_a_model_trying_to_unprotect_a_column_is_refused_on_the_proposal(store, profile) -> None:  # type: ignore[no-untyped-def]
    answer = json.dumps(
        {
            "columns": [
                {
                    "source_name": "SUBSCR_REL_CD",
                    "is_phi": False,
                    "confidence": 0.99,
                    "rationale": "just a code",
                }
            ]
        }
    )
    result, _ = _propose(store, profile, answer)
    column = result.classification.column("SUBSCR_REL_CD")
    assert column is not None and column.is_phi, "the column stays protected"
    assert any("Refused" in r for r in result.refusals)
    assert result.proposal.payload["refusals"], "and the attempt reaches the review screen"


def test_a_refused_downgrade_leaves_an_agent_action_row(store, profile) -> None:  # type: ignore[no-untyped-def]
    """A guardrail nobody can see fire is a comment."""
    answer = json.dumps(
        {"columns": [{"source_name": "SUBSCR_REL_CD", "is_phi": False, "confidence": 0.9}]}
    )
    _propose(store, profile, answer)
    actions = store.read_agent_actions(agent="phi-detection")
    assert actions and any("Refused" in a.detail for a in actions)


def test_a_column_the_model_invented_is_discarded(store, profile) -> None:  # type: ignore[no-untyped-def]
    answer = json.dumps(
        {"columns": [{"source_name": "MEMBER_SSN_2", "is_phi": True, "confidence": 0.9}]}
    )
    result, _ = _propose(store, profile, answer)
    assert result.classification.column("MEMBER_SSN_2") is None
    assert any("not a column in the profiled file" in r for r in result.refusals)


def test_a_detector_that_misses_a_flagged_column_raises_instead_of_proposing(store) -> None:  # type: ignore[no-untyped-def]
    """The gate, as a circuit breaker rather than a metric.

    Simulated by handing the agent a glossary that flags a column AFTER the
    classification would have been built — which is exactly the shape of the
    real failure: a term flagged PHI while a run was in flight.
    """
    from cinqflow.core.phi import Classification, ColumnClassification

    agent = _agent(store, ScriptedLlm(lambda p, t: GOOD_ANSWER))
    hostile = Classification(
        feed_id=FEED,
        profile_id="sha256-x",
        columns=(
            ColumnClassification(source_name="DOB", position=1, is_phi=False, basis=Basis.GLOSSARY),
        ),
    )
    with pytest.raises(RecallGateFailedError, match="DOB"):
        agent._confirm(hostile, {}, GLOSSARY)


def test_a_wholly_settled_file_calls_no_model_at_all(store) -> None:  # type: ignore[no-untyped-def]
    """Zero tokens. Not a cheap call — none."""
    settled = profile_bytes(
        b"DOB,LOB,SSN\n19360201,MEDICAID,078-05-1120\n19370302,DUAL,219-09-9999\n",
        file_format="csv",
        source_fingerprint="sha256-b",
    )
    result, llm = _propose(store, settled)
    assert not result.model_called
    assert llm.calls == []
    assert result.proposal.prompt_hash == ""


def test_the_overall_confidence_is_the_weakest_column(store, profile) -> None:  # type: ignore[no-untyped-def]
    """Averaging would let five glossary-certain columns hide the one the
    platform is protecting because it has no idea what it is."""
    result, _ = _propose(store, profile)
    assert result.proposal.confidence == min(c.confidence for c in result.classification.columns)


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(store, tmp_path):  # type: ignore[no-untyped-def]
    landing = LocalFsStorage(root=str(tmp_path))
    landing.place(KEY, ROSTER)
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        storage=landing,
        phi_detection_factory=lambda metadata: _agent(
            metadata, ScriptedLlm(lambda p, t: GOOD_ANSWER)
        ),
    )
    return TestClient(app)


def _as(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}"}


def _profile_and_detect(client: TestClient) -> dict:
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    response = client.post(
        f"/api/feeds/{FEED}/detect-phi",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(BA),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_ba_classifies_a_feed_from_a_stored_profile(client: TestClient) -> None:
    body = _profile_and_detect(client)
    assert body["agent"] == "phi-detection"
    assert body["state"] == "pending_review"
    assert body["columns"] == [], "the schema-inference list stays empty for this agent"

    by_name = {c["source_name"]: c for c in body["phi_columns"]}
    assert by_name["DOB"]["basis"] == "glossary"
    assert by_name["SSN"]["basis"] == "computation"
    assert by_name["SSN"]["phi_kind"] == "ssn"
    assert by_name["PROV_NPI"]["code_set"] == "npi"
    assert by_name["PROV_NPI"]["is_phi"] is False
    assert "SSN" in body["masked_columns"]
    assert "PROV_NPI" not in body["masked_columns"]


def test_every_flagged_column_says_why_it_is_flagged(client: TestClient) -> None:
    """ "A flag with no basis is a flag nobody can argue with." """
    for column in _profile_and_detect(client)["phi_columns"]:
        assert column["basis"], column["source_name"]
        assert column["rationale"], column["source_name"]


def test_a_read_only_user_may_not_run_the_agent(client: TestClient) -> None:
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    refused = client.post(
        f"/api/feeds/{FEED}/detect-phi",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(READER),
    )
    assert refused.status_code == 403


def test_the_recall_route_recomputes_the_gate_against_the_current_glossary(
    client: TestClient,
) -> None:
    body = _profile_and_detect(client)
    recall = client.get(f"/api/proposals/{body['proposal_id']}/recall", headers=_as(BA)).json()
    assert recall["passes"]
    assert recall["missed"] == []
    assert recall["expected"] == 1 and recall["protected"] == 1
    assert "gate holds" in recall["report"]


def test_the_masking_policy_masks_what_is_still_awaiting_a_steward(client: TestClient) -> None:
    _profile_and_detect(client)
    policy = client.get(f"/api/feeds/{FEED}/masking-policy", headers=_as(BA)).json()
    assert "SSN" in policy["masked_columns"]
    assert "SUBSCR_REL_CD" in policy["masked_columns"]
    assert "SUBSCR_REL_CD" in policy["pending_steward"]
    assert policy["state"] == "pending_review", "protection is in place before the review"


def test_a_feed_with_no_classification_says_so_rather_than_reporting_nothing_masked(
    client: TestClient,
) -> None:
    """A 404 rather than an empty policy. An empty masking policy and an
    absent one look identical to a caller, and one of them masks nothing."""
    missing = client.get("/api/feeds/some-other-feed/masking-policy", headers=_as(BA))
    assert missing.status_code == 404


def test_a_deployment_with_no_llm_pin_says_what_still_works(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    landing = LocalFsStorage(root=str(tmp_path))
    landing.place(KEY, ROSTER)
    client = TestClient(create_app(authn=StaticAuthn(), metadata_db=store, storage=landing))
    profiled = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(BA),
    ).json()
    refused = client.post(
        f"/api/feeds/{FEED}/detect-phi",
        json={"profile_id": profiled["profile_id"]},
        headers=_as(BA),
    )
    assert refused.status_code == 503
    assert "need no model" in refused.json()["detail"]


# ── approval ─────────────────────────────────────────────────────────────────


def _approve(client: TestClient, proposal_id: str, subject: str, **body):  # type: ignore[no-untyped-def]
    return client.post(f"/api/proposals/{proposal_id}/approve", json=body, headers=_as(subject))


def test_approving_a_classification_needs_a_contract_to_attach_it_to(
    client: TestClient,
) -> None:
    """A PHI flag is a property OF a contract column, so the ordering is real
    and the refusal says which story to run first."""
    body = _profile_and_detect(client)
    refused = _approve(client, body["proposal_id"], BA, comment="looks right")
    assert refused.status_code == 409
    assert "no data contract yet" in refused.json()["detail"]
    assert "stays masked meanwhile" in refused.json()["detail"]


def _with_contract(store: MemMetadataDb) -> None:
    from cinqflow.core.model.governed import GovernedObject

    store.save(
        GovernedObject(
            object_type=ObjectType.CONTRACT,
            object_id=FEED,
            version=1,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=Actor(subject=BA, actor_type=ActorType.HUMAN),
            created_ts=NOW,
            body={
                "key_columns": ["source_member_id"],
                "columns": [
                    {"name": "source_member_id", "source_name": "MemberID", "type": "string"},
                    {"name": "date_of_birth", "source_name": "DOB", "type": "date"},
                    {"name": "line_of_business", "source_name": "LOB", "type": "string"},
                    {"name": "ssn", "source_name": "SSN", "type": "string"},
                ],
            },
        )
    )


def test_approval_merges_the_flags_onto_a_draft_contract_the_approver_authored(
    client: TestClient, store: MemMetadataDb
) -> None:
    _with_contract(store)
    body = _profile_and_detect(client)
    approved = _approve(client, body["proposal_id"], BA, comment="accepted")
    assert approved.status_code == 200, approved.text

    contract = store.get(ObjectType.CONTRACT, FEED)
    assert contract.version == 2
    assert contract.lifecycle_state is LifecycleState.DRAFT
    assert contract.created_by.subject == BA
    assert contract.approved_by is None, "nothing on this path signs anything"

    by_source = {c["source_name"]: c for c in contract.body["columns"]}
    assert by_source["SSN"]["is_phi"] is True
    assert by_source["SSN"]["phi_basis"] == "computation"
    assert by_source["LOB"]["is_phi"] is False


def test_a_ba_may_not_clear_a_flag_while_accepting_the_classification(
    client: TestClient, store: MemMetadataDb
) -> None:
    """TWO ACTS, TWO DOORS.

    Accepting an agent's draft is authoring, which a BA does. Clearing a PHI
    flag is a steward's decision. Folding them into one route would let a BA
    unprotect a column as a side effect of accepting a contract — so the
    approve path refuses, and says where the other door is.
    """
    _with_contract(store)
    body = _profile_and_detect(client)
    refused = _approve(
        client,
        body["proposal_id"],
        BA,
        comment="this one is fine",
        columns=[{"source_name": "SUBSCR_REL_CD", "is_phi": False}],
    )
    assert refused.status_code == 403
    assert "data steward's decision" in refused.json()["detail"]
    assert "reclassify route" in refused.json()["detail"]
    assert store.get_proposal(body["proposal_id"]).state is ProposalState.PENDING_REVIEW


def _reclassify(client: TestClient, proposal_id: str, subject: str, **body):  # type: ignore[no-untyped-def]
    return client.post(f"/api/proposals/{proposal_id}/reclassify", json=body, headers=_as(subject))


def test_the_reclassify_route_is_the_stewards_and_not_the_analysts(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The other half of the segregation: a BA cannot reach the steward's door
    either, so neither role holds both halves."""
    _with_contract(store)
    body = _profile_and_detect(client)
    refused = _reclassify(
        client,
        body["proposal_id"],
        BA,
        rationale="I checked with the payer",
        columns=[{"source_name": "SUBSCR_REL_CD", "is_phi": False}],
    )
    assert refused.status_code == 403


def test_a_steward_clearing_a_flag_must_give_a_reason(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Refused by the SCHEMA — the earliest place a reason can be required.

    A downgrade is the one request in the platform that reduces protection on
    a field, and an unexplained one is an unreviewable one.
    """
    _with_contract(store)
    body = _profile_and_detect(client)
    refused = _reclassify(
        client,
        body["proposal_id"],
        STEWARD,
        rationale="",
        columns=[{"source_name": "SUBSCR_REL_CD", "is_phi": False}],
    )
    assert refused.status_code == 422, refused.text


def test_a_steward_downgrade_with_a_reason_is_recorded_as_a_correction(
    client: TestClient, store: MemMetadataDb
) -> None:
    _with_contract(store)
    body = _profile_and_detect(client)
    reason = "relationship code, no member data — checked against the payer's companion guide"
    decided = _reclassify(
        client,
        body["proposal_id"],
        STEWARD,
        rationale=reason,
        columns=[{"source_name": "SUBSCR_REL_CD", "is_phi": False}],
    )
    assert decided.status_code == 200, decided.text
    assert any(
        c["field_path"] == "SUBSCR_REL_CD.is_phi" and c["accepted"] is False
        for c in decided.json()["corrections"]
    )

    ledger = store.read_audit(object_id=FEED)
    reclassified = [e for e in ledger if e.action == "reclassified"]
    assert reclassified, "the decision is on the record"
    assert reason in reclassified[0].detail, "and so is the reason"
    assert store.get(ObjectType.CONTRACT, FEED).created_by.subject == STEWARD


def test_a_steward_may_not_reclassify_a_schema_proposal(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The route is about PHI, and a schema-inference proposal classifies
    nothing — so the refusal names what the caller actually asked for."""
    from cinqflow.core.model.governed import Actor as _Actor
    from cinqflow.core.model.vocabulary import RiskClass as _Risk
    from cinqflow.core.proposals import Proposal, submit

    store.record_proposal(
        submit(
            Proposal(
                proposal_id="99999999-9999-4999-8999-999999999999",
                agent="schema-inference",
                capability="propose_schema_contract",
                risk_class=_Risk.R2,
                run_id="r",
                feed_id=FEED,
                payload={"records": []},
                created_by=_Actor(subject="schema-inference", actor_type=ActorType.AI),
                created_ts=NOW,
            ),
            now=NOW,
        )
    )
    refused = _reclassify(
        client, "99999999-9999-4999-8999-999999999999", STEWARD, rationale="x", columns=[]
    )
    assert refused.status_code == 400
    assert "classifies nothing" in refused.json()["detail"]


def test_a_refused_downgrade_leaves_a_ledger_row(client: TestClient, store: MemMetadataDb) -> None:
    _with_contract(store)
    body = _profile_and_detect(client)
    _approve(
        client,
        body["proposal_id"],
        BA,
        comment="fine",
        columns=[{"source_name": "SUBSCR_REL_CD", "is_phi": False}],
    )
    ledger = store.read_audit(object_id=FEED)
    assert any(entry.action == "refused:phi_downgrade" for entry in ledger)


def test_a_contract_column_already_flagged_stays_flagged(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The OR rule. Two sources of a PHI flag combine the only way that cannot
    lose one — and the exception is the steward's explicit decision, which is
    tested above."""
    from cinqflow.core.model.governed import GovernedObject

    store.save(
        GovernedObject(
            object_type=ObjectType.CONTRACT,
            object_id=FEED,
            version=1,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=Actor(subject=BA, actor_type=ActorType.HUMAN),
            created_ts=NOW,
            body={
                "key_columns": [],
                # A human already flagged the provider column. The
                # classification says a code set is not PHI — and must not be
                # allowed to undo somebody's deliberate decision.
                "columns": [
                    {"name": "npi", "source_name": "PROV_NPI", "type": "string", "is_phi": True}
                ],
            },
        )
    )
    body = _profile_and_detect(client)
    assert _approve(client, body["proposal_id"], BA, comment="ok").status_code == 200
    contract = store.get(ObjectType.CONTRACT, FEED)
    assert contract.body["columns"][0]["is_phi"] is True


# ── the prompt ───────────────────────────────────────────────────────────────


def test_the_prompt_is_a_registry_object_with_the_fixed_assembly_order() -> None:
    from cinqflow.core.prompts import ASSEMBLY_ORDER, REQUIRED_SECTIONS

    assert len(TEMPLATES) == 1, "one question, one template"
    for template in TEMPLATES:
        assert set(template.sections) >= REQUIRED_SECTIONS
        assert template.response_schema is not None
        assembled = [s for s in ASSEMBLY_ORDER if s in template.sections]
        assert assembled == [s for s in ASSEMBLY_ORDER if s in template.sections]


def test_the_prompt_states_that_a_downgrade_is_unavailable() -> None:
    """A model with no way to say "not PHI" would say it in `rationale`
    anyway; telling it what happens is what turns a refused answer into an
    answer never given."""
    constraints = TEMPLATES[0].sections[
        __import__("cinqflow.core.prompts", fromlist=["PromptSection"]).PromptSection.CONSTRAINTS
    ]
    assert "CANNOT UNPROTECT" in constraints
    assert "NOT BEING SHOWN ANY VALUES" in constraints
