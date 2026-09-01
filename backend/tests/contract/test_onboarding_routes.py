"""CF-V1-E4-01/02/03 through the API — the wizard, the pack and the gates.

    "Show a single readiness view: what is complete, what is missing, what
     needs engineering review."
    "Given a user without permission on this object, when they attempt to view
     or change it, then access is denied, nothing is revealed, and the attempt
     is logged."
    — CF-V1-E4-01

`tests/unit/test_onboarding_*.py` prove the semantics. This proves the routes
cannot be talked past them: that the checklist a screen renders is the one the
submit gate enforces, that a red checklist and stale evidence both refuse with
409 rather than a 500, and that a feed outside the caller's scope is a 404 that
reveals nothing — including, especially, its obstacle list, which is as
informative about a feed as the feed itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.api.app import EVIDENCE_KEY
from cinqflow.core.mapping import FeedMapping, MappingLine, mapping_as_governed
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.contract import ContractColumn, SchemaContract, contract_as_governed
from cinqflow.core.schema_spec import TypeName
from cinqflow.ports.metadata_db import FileProfileRecord

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

BA = "dev-ba@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
FEED_ID = "fidelis-downstate-roster"

SEED = Actor(subject="seed@cinqcare.test", actor_type=ActorType.HUMAN)

CONTRACT = SchemaContract(
    feed_id=FEED_ID,
    version=1,
    columns=(ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),),
)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _approved(obj: GovernedObject) -> GovernedObject:
    from dataclasses import replace

    return replace(
        obj,
        lifecycle_state=LifecycleState.APPROVED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=datetime.now(UTC),
    )


def _feed(body: dict[str, Any] | None = None, *, version: int = 1) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED_ID,
        version=version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=SEED,
        created_ts=datetime.now(UTC),
        body=body or {},
    )


def _add_evidence(store: MemMetadataDb, pack: dict[str, Any]) -> None:
    """Attach a pack by saving the NEXT feed version.

    The realistic shape, and the one the staleness gate depends on: the pack
    lives on the feed's body, so it versions with the feed and "which evidence
    did this version publish on?" is answerable without a join.
    """
    store.save(_feed({EVIDENCE_KEY: pack}, version=2))


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    memory.save(_feed())
    return memory


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    with TestClient(create_app(authn=StaticAuthn(), metadata_db=store)) as test_client:
        yield test_client


def _add_sample(store: MemMetadataDb) -> None:
    """A real profile of a real two-line file — CF-V1-E5-01's own profiler.

    Not a stub record: the wizard asks whether a sample EXISTS, and building
    the answer out of the profiler that would really have produced it is what
    keeps this test honest about step 1.
    """
    store.record_profile(
        FileProfileRecord(
            feed_id=FEED_ID,
            profile=profile_bytes(
                b"First_Name\nAda\n",
                file_format="csv",
                source_key="enrollments/ROSTER_20260801.csv",
                source_fingerprint="sha256-sample",
            ),
            profiled_by="dev-ba@cinqcare.test",
            profiled_ts=datetime.now(UTC),
        )
    )


def _add_contract(store: MemMetadataDb) -> None:
    store.save(_approved(contract_as_governed(CONTRACT, author=SEED)))


def _add_mapping(store: MemMetadataDb) -> None:
    mapping = FeedMapping(
        feed_id=FEED_ID,
        lines=(
            MappingLine(
                target_entity="members",
                target_field="first_name",
                source_columns=("First_Name",),
            ),
        ),
    )
    store.save(_approved(mapping_as_governed(mapping, author=SEED)))


# ── the readiness view ───────────────────────────────────────────────────────
def test_a_new_feed_shows_five_steps_and_resumes_at_the_first(client: TestClient) -> None:
    view = client.get(f"/api/feeds/{FEED_ID}/onboarding", headers=_as(BA))
    assert view.status_code == 200, view.text
    body = view.json()
    assert [step["step"] for step in body["steps"]] == [
        "sample",
        "schema",
        "mapping",
        "rules",
        "publish",
    ]
    assert body["resume_at"] == "sample"
    assert body["is_publishable"] is False
    assert body["steps"][0]["label"] == "Upload a sample file"


def test_the_checklist_fills_in_as_approvals_land(client: TestClient, store: MemMetadataDb) -> None:
    _add_sample(store)
    _add_contract(store)
    body = client.get(f"/api/feeds/{FEED_ID}/onboarding", headers=_as(BA)).json()
    completed = [step["step"] for step in body["steps"] if step["is_complete"]]
    assert completed == ["sample", "schema"]
    assert body["resume_at"] == "mapping"


def test_a_submitted_object_is_needs_review_and_not_complete(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The don't, through the route: the checklist reflects real states."""
    from dataclasses import replace

    _add_sample(store)
    store.save(
        replace(
            contract_as_governed(CONTRACT, author=SEED),
            lifecycle_state=LifecycleState.PENDING_REVIEW,
        )
    )
    body = client.get(f"/api/feeds/{FEED_ID}/onboarding", headers=_as(BA)).json()
    schema = next(step for step in body["steps"] if step["step"] == "schema")
    assert schema["status"] == "Needs Review"
    assert schema["is_complete"] is False


def test_every_obstacle_carries_a_route_the_client_does_not_have_to_build(
    client: TestClient, store: MemMetadataDb
) -> None:
    """`CitationId.route` is resolved server-side, so one-click navigation is a
    link the UI renders rather than a mapping it has to know."""
    from dataclasses import replace

    _add_sample(store)
    store.save(
        replace(
            contract_as_governed(CONTRACT, author=SEED),
            lifecycle_state=LifecycleState.PENDING_REVIEW,
        )
    )
    body = client.get(f"/api/feeds/{FEED_ID}/onboarding", headers=_as(BA)).json()
    schema = next(step for step in body["steps"] if step["step"] == "schema")
    obstacle = schema["obstacles"][0]
    assert obstacle["citation"].startswith("contract:")
    assert obstacle["route"].startswith("/data/intake/contract/")


# ── the evidence pack ────────────────────────────────────────────────────────
def test_no_test_run_is_a_404_that_says_what_to_do(client: TestClient) -> None:
    """An empty pack rendered as a document is evidence that says nothing while
    looking like evidence, and somebody would attach it to an approval."""
    missing = client.get(f"/api/feeds/{FEED_ID}/evidence", headers=_as(BA))
    assert missing.status_code == 404
    assert "Run the test on your sample file" in missing.text


def test_a_stored_pack_comes_back_with_its_document(
    client: TestClient, store: MemMetadataDb
) -> None:
    _add_evidence(
        store,
        {
            "feed_id": FEED_ID,
            "fingerprint": "sha256-abc",
            "produced_ts": datetime.now(UTC).isoformat(),
            "rows_in": 10_000,
            "rows_loaded": 9_992,
            "rows_quarantined": 8,
            "drops": [
                {
                    "rule_id": "DQ-002",
                    "reason": "Member First Name Not Null",
                    "record_count": 8,
                    "columns": ["First_Name"],
                }
            ],
            "sample_filename": "ROSTER_20260801.csv",
        },
    )
    body = client.get(f"/api/feeds/{FEED_ID}/evidence", headers=_as(BA)).json()
    assert body["rows_in"] == 10_000
    assert body["accounts_for_every_row"] is True
    assert "10,000 rows in / 9,992 loaded / 8 quarantined" in body["summary"]
    assert "# Onboarding evidence" in body["markdown"]
    assert "Member First Name Not Null" in body["markdown"]


# ── the submit gate ──────────────────────────────────────────────────────────
def test_submitting_without_a_test_run_is_refused_with_the_reason(
    client: TestClient,
) -> None:
    refused = client.post(f"/api/feeds/{FEED_ID}/onboarding/submit", headers=_as(BA))
    assert refused.status_code == 409
    assert "end-to-end sample test has not been run" in refused.text


def test_a_red_checklist_refuses_and_the_refusal_is_the_checklist(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The screen and the gate read the same function, which is what stops the
    screen showing green while submit returns 409."""
    _add_evidence(store, {"feed_id": FEED_ID, "fingerprint": "sha256-abc"})
    refused = client.post(f"/api/feeds/{FEED_ID}/onboarding/submit", headers=_as(BA))
    assert refused.status_code == 409
    assert "Upload a sample file" in refused.text


def test_stale_evidence_blocks_submission(client: TestClient, store: MemMetadataDb) -> None:
    """THE WAVE'S EXIT CRITERION, through the route.

    The pack's fingerprint is deliberately not the one this configuration
    produces, which is exactly the state a mapping edited after the test
    leaves behind.
    """
    _add_sample(store)
    _add_contract(store)
    _add_mapping(store)
    _add_evidence(
        store,
        {
            "feed_id": FEED_ID,
            "fingerprint": "sha256-from-before-the-edit",
            "produced_ts": datetime.now(UTC).isoformat(),
        },
    )
    refused = client.post(f"/api/feeds/{FEED_ID}/onboarding/submit", headers=_as(BA))
    assert refused.status_code == 409
    assert "changed after the last end-to-end test" in refused.text


# ── the narrative ────────────────────────────────────────────────────────────
def test_the_journey_reads_as_one_story_across_object_types(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The contract's approval and the feed's publication are chapters of the
    same story — a narrative that read only feed events would omit most of it."""
    from cinqflow.core.model.governed import AuditEntry

    for action, object_type in (
        ("evidence:produced", ObjectType.FEED),
        ("transition:approved", ObjectType.CONTRACT),
        ("signature:business", ObjectType.FEED),
    ):
        store.append_audit(
            AuditEntry(
                object_type=object_type,
                object_id=FEED_ID,
                version=1,
                action=action,
                actor=SEED,
                occurred_ts=datetime.now(UTC),
            )
        )
    body = client.get(f"/api/feeds/{FEED_ID}/narrative", headers=_as(BA)).json()
    told = [chapter["what"] for chapter in body["chapters"]]
    assert "ran the end-to-end test" in told
    assert "approved it" in told
    assert "signed the business approval" in told


# ── the guardrail ────────────────────────────────────────────────────────────
def test_a_read_only_user_may_see_the_checklist_and_not_submit(
    client: TestClient,
) -> None:
    assert client.get(f"/api/feeds/{FEED_ID}/onboarding", headers=_as(READ_ONLY)).status_code == 200
    denied = client.post(f"/api/feeds/{FEED_ID}/onboarding/submit", headers=_as(READ_ONLY))
    assert denied.status_code == 403


def test_an_unauthenticated_caller_sees_nothing(client: TestClient) -> None:
    assert client.get(f"/api/feeds/{FEED_ID}/onboarding").status_code == 401
    assert client.get(f"/api/feeds/{FEED_ID}/evidence").status_code == 401
    assert client.post(f"/api/feeds/{FEED_ID}/onboarding/submit").status_code == 401
