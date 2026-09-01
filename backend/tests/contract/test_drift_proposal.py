"""CF-V2-E5-04 — the proposed contract v2, through the SAME acceptance door.

    "a proposed contract v2 awaits steward approval"
    "Auto-modify a contract — even a compatible rename becomes a proposed new
     contract version for approval." — the documented don't

The drift proposal travels the one review queue and the one apply path every
agent uses. These tests prove the whole loop: the worker writes the draft, a
person accepts it over the API, and the DRAFT contract v(n+1) that appears
reads from the NEW spelling — with the glossary rows as the proposal's
grounding, because no model was called and nothing needed judging.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.drift import Rename
from cinqflow.core.model.governed import LifecycleState, ObjectType
from cinqflow.core.proposals import ProposalState
from cinqflow.core.registry.contract import ContractColumn, SchemaContract, from_governed
from cinqflow.core.schema_spec import TypeName
from cinqflow.workers.drift import AGENT, propose_contract_update

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
BA = "dev-ba@cinqcare.test"

CONTRACT = SchemaContract(
    feed_id=FEED,
    version=3,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, source_name="MemberID"),
        ContractColumn(
            "date_of_birth",
            TypeName.DATE,
            nullable=False,
            source_name="DOB",
            is_phi=True,
            date_formats=("%Y-%m-%d",),
        ),
    ),
    key_columns=("source_member_id",),
)

RENAME = Rename(
    was="DOB",
    now="date_of_birth",
    glossary_id="BG-004",
    term="Member Date of Birth",
    term_slug="member-date-of-birth",
)


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=store)
    with TestClient(app) as test_client:
        yield test_client


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def test_the_proposal_is_a_draft_grounded_in_the_glossary_and_no_model(
    store: MemMetadataDb,
) -> None:
    proposal = propose_contract_update(
        store, feed_id=FEED, contract=CONTRACT, renames=(RENAME,), run_id="B-1244", now=NOW
    )
    assert proposal is not None
    assert proposal.state is ProposalState.PENDING_REVIEW
    assert proposal.confidence == 1.0
    assert [str(c) for c in proposal.grounding_citations] == ["term:member-date-of-birth"]
    (rename,) = proposal.payload["renames"]
    assert "one concept, two spellings" in rename["evidence"]
    # The records already read from the NEW spelling.
    sources = {r["source_name"] for r in proposal.payload["records"]}
    assert sources == {"MemberID", "date_of_birth"}


def test_a_daily_renamed_delivery_earns_one_proposal_not_one_per_day(
    store: MemMetadataDb,
) -> None:
    first = propose_contract_update(
        store, feed_id=FEED, contract=CONTRACT, renames=(RENAME,), run_id="B-1", now=NOW
    )
    second = propose_contract_update(
        store, feed_id=FEED, contract=CONTRACT, renames=(RENAME,), run_id="B-2", now=NOW
    )
    assert first is not None
    assert second is None
    assert len(store.list_proposals(feed_id=FEED, agent=AGENT)) == 1


def test_acceptance_produces_the_draft_contract_v2_reading_the_new_spelling(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The whole story in one loop: rename classified → proposal → steward's
    reviewer accepts → DRAFT contract v(n+1) reads `date_of_birth`, and the
    published contract the pipeline runs on is untouched until the lifecycle
    says otherwise."""
    proposal = propose_contract_update(
        store, feed_id=FEED, contract=CONTRACT, renames=(RENAME,), run_id="B-1244", now=NOW
    )
    assert proposal is not None
    response = client.post(
        f"/api/proposals/{proposal.proposal_id}/approve",
        json={"comment": "the payer renamed it", "key_columns": ["source_member_id"]},
        headers=_as(BA),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "applied"

    draft = store.get(ObjectType.CONTRACT, FEED)
    assert draft.lifecycle_state is LifecycleState.DRAFT
    rebuilt = from_governed(draft)
    assert {c.reads_from for c in rebuilt.columns} == {"MemberID", "date_of_birth"}
    assert rebuilt.column("date_of_birth").type.value == "date"
