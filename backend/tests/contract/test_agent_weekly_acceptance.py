"""W1-35 (F6) — the acceptance rate per agent per week, over the real API.

    "the acceptance rate per agent per week is THE health metric"
    — `GET /api/proposals/{proposal_id}/acceptance`'s own docstring,
      CF-V1-E6-02

`core.proposals.weekly_acceptance` is proved as pure arithmetic in
`tests/unit/test_proposals.py`. This proves the missing half: that a seeded
set of accepted proposals produces the same numbers over the wire, through
`GET /api/agents/{agent}/acceptance`, for whichever agent name the path
names — the aggregation was never meant to know the roster of agents that
exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import Correction, Proposal, approve, reject, submit

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

READER = "dev-analyst@cinqcare.test"
FEED = "fidelis-downstate-roster"
AGENT_ACTOR = Actor(
    subject="schema-inference", actor_type=ActorType.AI, display_name="Schema inference"
)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")

# 2026-08-24 and 2026-08-30 both fall in ISO week 35; 2026-08-31 — the very
# next day — is already week 36. The seeded data leans on that boundary the
# same way the unit test does, so a route that quietly switched to a calendar
# week would fail here too.
WEEK_35 = datetime(2026, 8, 24, 9, tzinfo=UTC)
WEEK_36 = datetime(2026, 8, 31, 9, tzinfo=UTC)


def _seed(
    store: MemMetadataDb,
    *,
    proposal_id: str,
    agent: str,
    created_ts: datetime,
    total: int,
    corrected: int,
) -> None:
    """An APPROVED proposal with a KNOWN correction rate, written straight to
    the store — the acceptance/approval MACHINERY is proved elsewhere
    (`test_schema_inference_agent.py`, `test_mapping_redirect_proposal.py`);
    this suite is only about the sum across many."""
    payload = {
        "key": "source_name",
        "records": [{"source_name": f"col{i}"} for i in range(total)],
    }
    corrections = tuple(
        Correction(field_path=f"col{i}", proposed="agent-said", accepted="human-said")
        for i in range(corrected)
    )
    proposal = Proposal(
        proposal_id=proposal_id,
        agent=agent,
        capability="propose_schema_contract",
        risk_class=RiskClass.R2,
        run_id=f"run-{proposal_id}",
        feed_id=FEED,
        payload=payload,
        created_by=AGENT_ACTOR,
        created_ts=created_ts,
    )
    decided = approve(
        submit(proposal, now=created_ts), approver=BA, corrections=corrections, now=created_ts
    )
    store.record_proposal(decided)


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


def test_the_route_sums_a_seeded_set_of_accepted_proposals_by_week(
    client: TestClient, store: MemMetadataDb
) -> None:
    _seed(
        store, proposal_id="p1", agent="schema-inference", created_ts=WEEK_35, total=3, corrected=1
    )
    _seed(
        store, proposal_id="p2", agent="schema-inference", created_ts=WEEK_35, total=2, corrected=0
    )
    _seed(
        store, proposal_id="p3", agent="schema-inference", created_ts=WEEK_36, total=4, corrected=4
    )
    # A different agent, same week — must not leak into schema-inference's own numbers.
    _seed(
        store,
        proposal_id="p4",
        agent="mapping-suggestion",
        created_ts=WEEK_35,
        total=5,
        corrected=1,
    )

    response = client.get("/api/agents/schema-inference/acceptance", headers=_as(READER))
    assert response.status_code == 200, response.text
    weeks = {row["week"]: row for row in response.json()}

    assert set(weeks) == {"2026-W35", "2026-W36"}

    week35 = weeks["2026-W35"]
    assert week35["agent"] == "schema-inference"
    assert week35["proposal_count"] == 2
    # SUMMED across the two proposals (1 correction over 5 fields), not the
    # mean of their individual rates (2/3 and 1.0, which would read 0.833).
    assert week35["acceptance"]["total"] == 5
    assert week35["acceptance"]["corrected"] == 1
    assert week35["acceptance"]["rate"] == pytest.approx(0.8)

    week36 = weeks["2026-W36"]
    assert week36["proposal_count"] == 1
    assert week36["acceptance"]["rate"] == 0.0


def test_the_route_is_not_hardcoded_to_one_agent_name(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Mapping suggestion, not schema inference — the path parameter alone
    decides which agent's numbers come back."""
    _seed(
        store,
        proposal_id="p1",
        agent="mapping-suggestion",
        created_ts=WEEK_35,
        total=5,
        corrected=1,
    )
    response = client.get("/api/agents/mapping-suggestion/acceptance", headers=_as(READER))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["agent"] == "mapping-suggestion"
    assert body[0]["acceptance"]["total"] == 5


def test_an_agent_nobody_has_seeded_returns_an_empty_series_not_a_404(
    client: TestClient,
) -> None:
    """No history yet is not an error — it is the same "nothing recorded"
    answer the review queue gives for a feed with no proposals."""
    response = client.get("/api/agents/rule-authoring/acceptance", headers=_as(READER))
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_a_rejected_proposal_does_not_count_toward_acceptance(
    client: TestClient, store: MemMetadataDb
) -> None:
    """A rejection was never accepted — the health metric is about what a
    person approved, not everything an agent ever proposed."""
    payload = {"key": "source_name", "records": [{"source_name": "col0"}]}
    proposal = Proposal(
        proposal_id="p-rejected",
        agent="schema-inference",
        capability="propose_schema_contract",
        risk_class=RiskClass.R2,
        run_id="run-rejected",
        feed_id=FEED,
        payload=payload,
        created_by=AGENT_ACTOR,
        created_ts=WEEK_35,
    )
    rejected = reject(
        submit(proposal, now=WEEK_35),
        approver=BA,
        comment="wrong column entirely",
        now=WEEK_35,
    )
    store.record_proposal(rejected)

    response = client.get("/api/agents/schema-inference/acceptance", headers=_as(READER))
    assert response.status_code == 200, response.text
    assert response.json() == []
