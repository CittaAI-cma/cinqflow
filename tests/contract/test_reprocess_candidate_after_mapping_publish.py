"""W1-34 · CF-V1-E6-04 (F5, RE-SCOPED) — through the real API.

    "after a mapping-coverage proposal is approved and published, automatically
     offer/trigger reprocessing of the batch(es) that arrived with the
     now-newly-mapped column — using the EXISTING recovery toolkit, not new
     replay logic."

This proves the whole loop the story asks for, through the routes a reviewer
actually uses, not just the worker function underneath: W1-32's `classify`
finds `SUBSCR_REL_CD` UNMAPPED_COLUMN on a real batch; W1-33's
`propose_mapping_for_unmapped_columns` turns that into a real mapping-
suggestion proposal; a business analyst accepts it and a steward submits,
approves and PUBLISHES the result over `POST /api/objects/...` exactly as
`test_mapping_routes.py` does for a hand-authored mapping. Only THEN — never
before, and never by this test calling anything from `workers.drift`
directly — does a reprocess candidate for the batch that could not have known
appear in the SAME operations-action ledger CF-V2-E12-03 built.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.api import create_app
from cinqflow.core.drift import classify
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import ContractColumn, SchemaContract, compare_to_contract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.ports.control_tables import BatchControl, SchemaDrift, StageStatus
from cinqflow.workers.drift import propose_mapping_for_unmapped_columns

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
BA = "dev-ba@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="members",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("relationship_code", TypeName.STRING),
            ),
            primary_key=("member_row_id",),
        ),
    ),
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-060",
            term="Subscriber Relationship",
            definition="How the member relates to the subscriber.",
            mapped_domains=("Enrollment",),
            mapped_tables=("members",),
            mapped_columns_original=("SUBSCR_REL_CD",),
            mapped_columns_corrected=("relationship_code",),
        ),
    )
)

#: Deliberately unaware of `SUBSCR_REL_CD` — the real contract this feed's
#: batches arrived under, so `classify` calls the column additive AND
#: contract-unknown, exactly the finding this whole slab exists to answer.
CONTRACT = SchemaContract(
    feed_id=FEED,
    version=7,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, source_name="MemberID"),
    ),
    key_columns=("source_member_id",),
)

#: What THIS feed's mapping already covered when the batches below arrived —
#: `classify`'s own second question ("does any line of the PUBLISHED mapping
#: read it?") is what turns the plain ADDED finding into UNMAPPED_COLUMN.
MAPPING_AT_INGEST_TIME = FeedMapping(
    feed_id=FEED,
    version=1,
    lines=(
        MappingLine(
            target_entity="members", target_field="source_member_id", source_columns=("MemberID",)
        ),
    ),
)

MODEL = build((DEPLOYED,), GLOSSARY)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _agent(store: MemMetadataDb) -> MappingSuggestionAgent:
    def _must_not_be_called(prompt: str, task_class: object) -> str:
        raise AssertionError(
            "the model must not be called — SUBSCR_REL_CD is settled by the glossary"
        )

    gateway = LlmGateway(
        llm=ScriptedLlm(_must_not_be_called),
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=Decimal("0.01"),
        clock=lambda: NOW,
    )
    return MappingSuggestionAgent(llm=gateway, metadata=store)


def _land_batch_with_unmapped_column(
    control: MemStoreControlTables, *, batch_id: str, column: str, started: datetime
) -> None:
    """The REAL W1-32 write (`workers.pipeline._process`'s own
    `control.record_schema_drift` call), reproduced here at the fact level so
    this suite does not need a whole `PipelineRunner` to prove a route.

    The batch reaches COMPLETED, because the corrected premise is exactly
    this: `DriftKind.UNMAPPED_COLUMN.blocks_batch` is FALSE, unconditionally
    — an additive, ungoverned column never stops a batch."""
    control.open_batch(
        BatchControl(
            batch_id=batch_id,
            feed_id=FEED,
            feed_version=CONTRACT.version,
            business_date=started.date().isoformat(),
            state=BatchState.RECEIVED,
            started_ts=started,
        )
    )
    control.record_stage(
        StageStatus(
            batch_id=batch_id,
            stage=Layer.BRONZE,
            state=BatchState.COMPLETED,
            started_ts=started,
            completed_ts=started,
            records_in=612,
            records_out=612,
        )
    )
    control.update_batch_state(batch_id, BatchState.COMPLETED)

    assessment = classify(
        compare_to_contract(("MemberID", column), CONTRACT),
        contract=CONTRACT,
        glossary=GLOSSARY,
        mapping=MAPPING_AT_INGEST_TIME,
    )
    for finding in assessment.findings:
        control.record_schema_drift(
            SchemaDrift(
                batch_id=batch_id,
                feed_id=FEED,
                classification=finding.kind.value,
                column_name=finding.column,
                detail=finding.detail,
                blocked_batch=finding.blocks_batch,
                detected_ts=started,
            )
        )


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


@pytest.fixture
def client(store: MemMetadataDb, control: MemStoreControlTables) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


def _publish_the_accepted_mapping(client: TestClient) -> None:
    """Exactly `test_mapping_routes.py`'s own `_publish` shape: BA submits
    what they authored by accepting, STEWARD approves and publishes it — the
    real three-route lifecycle, no shortcut through `lifecycle.*` directly."""
    submitted = client.post(
        f"/api/objects/mapping/{FEED}/submit", json={"comment": "ready"}, headers=_as(BA)
    )
    assert submitted.status_code == 200, submitted.text
    approved = client.post(
        f"/api/objects/mapping/{FEED}/approve",
        json={"comment": "matches the glossary"},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text
    published = client.post(f"/api/objects/mapping/{FEED}/publish", json={}, headers=_as(STEWARD))
    assert published.status_code == 200, published.text


# ── the whole loop: finding -> proposal -> accept -> publish -> candidate ────


def test_the_batch_that_could_not_have_known_gets_a_reprocess_candidate(
    client: TestClient, store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    _land_batch_with_unmapped_column(
        control, batch_id="B-OLD", column="SUBSCR_REL_CD", started=NOW - timedelta(days=3)
    )
    assert control.get_batch("B-OLD").state is BatchState.COMPLETED, (
        "the corrected premise: additive, ungoverned drift never blocks a batch"
    )
    findings = control.get_schema_drift("B-OLD")
    assert any(
        d.classification == "unmapped_column" and d.column_name == "SUBSCR_REL_CD" for d in findings
    ), "the fixture must reproduce W1-32's own finding"

    proposal = propose_mapping_for_unmapped_columns(
        _agent(store),
        feed_id=FEED,
        unmapped_columns=("SUBSCR_REL_CD",),
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=MAPPING_AT_INGEST_TIME,
        run_id="B-OLD",
        now=NOW,
    )
    assert proposal is not None

    accepted = client.post(
        f"/api/proposals/{proposal.proposal_id}/approve",
        json={"comment": "the glossary already settles this"},
        headers=_as(BA),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "applied"

    _publish_the_accepted_mapping(client)

    # THE ASSERTION THIS SLAB IS FOR — found through the real route, on the
    # SAME ops-action ledger a human's own retry lands in.
    history = client.get(
        "/api/operations/batches/B-OLD/action-history", headers=_as(STEWARD)
    ).json()
    candidates = [entry for entry in history if entry["action"] == "reprocess_batch"]
    assert len(candidates) == 1, history
    (candidate,) = candidates
    assert candidate["target"] == "B-OLD"
    assert "SUBSCR_REL_CD" in candidate["reason"]

    # NEVER AUTO-EXECUTED. "agents propose; humans dispose" — the candidate is
    # REFUSED, never REQUESTED (which would mean something actually ran) and
    # never VERIFIED (which would mean it succeeded).
    assert candidate["phase"] == "refused"
    assert candidate["is_complete"] is False
    assert "human" in candidate["outcome"]

    # And genuinely inert: no new batch exists for this feed.
    assert {b.batch_id for b in control.list_batches(FEED)} == {"B-OLD"}


def test_a_batch_with_a_different_unmapped_column_is_not_swept_in(
    client: TestClient, store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """Proves the candidate set is read from EACH batch's own finding, not
    "every recent batch of this feed" — a distractor with an unrelated,
    still-ungoverned column earns nothing when THIS mapping publishes."""
    _land_batch_with_unmapped_column(
        control, batch_id="B-OLD", column="SUBSCR_REL_CD", started=NOW - timedelta(days=3)
    )
    _land_batch_with_unmapped_column(
        control, batch_id="B-OTHER", column="PLAN_CODE", started=NOW - timedelta(days=2)
    )

    proposal = propose_mapping_for_unmapped_columns(
        _agent(store),
        feed_id=FEED,
        unmapped_columns=("SUBSCR_REL_CD",),
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=MAPPING_AT_INGEST_TIME,
        run_id="B-OLD",
        now=NOW,
    )
    assert proposal is not None
    client.post(
        f"/api/proposals/{proposal.proposal_id}/approve",
        json={"comment": "the glossary already settles this"},
        headers=_as(BA),
    )
    _publish_the_accepted_mapping(client)

    old_history = client.get(
        "/api/operations/batches/B-OLD/action-history", headers=_as(STEWARD)
    ).json()
    other_history = client.get(
        "/api/operations/batches/B-OTHER/action-history", headers=_as(STEWARD)
    ).json()
    assert any(e["action"] == "reprocess_batch" for e in old_history)
    assert not any(e["action"] == "reprocess_batch" for e in other_history), (
        "PLAN_CODE is still ungoverned after this publish — B-OTHER earns nothing"
    )
