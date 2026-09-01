"""CF-V3-E10-01 through the API — the ALREADY-GENERIC governance routes,
proven for `ObjectType.ODS_MODEL`.

No new lifecycle routes exist for this story on purpose: `/api/objects/
ods_model/{id}/submit|approve|publish` already work for any routed object
type, and `ObjectType.ODS_MODEL` was routed (`_ENGINEERED`) the day it was
added. This proves that claim rather than assuming it, and proves the ONE
genuinely new thing publish does for this type: provisioning.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import OdsModel, as_governed, from_governed
from cinqflow.core.registry.ods_model_member_domain import MEMBER_DOMAIN_V1

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

#: Submits — `_ENGINEERED`'s reviewers/publishers deliberately do NOT hold
#: SUBMIT_FOR_REVIEW (plate 14's segregation: the platform engineer signs
#: off, the data engineer authors), so the story's own "Data Engineer"
#: persona is who drafts and submits.
DATA_ENGINEER = "dev-engineer@cinqcare.test"
#: Reviews and publishes — `_ENGINEERED`'s own lane.
PLATFORM_ENGINEER = "dev-platform@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
AUTHOR = Actor(subject=DATA_ENGINEER, actor_type=ActorType.HUMAN, display_name="Arun Menon")
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.fixture
def metadata() -> MemMetadataDb:
    store = MemMetadataDb()
    store.save(as_governed(MEMBER_DOMAIN_V1, author=AUTHOR, created_ts=NOW))
    return store


class _RecordingProvisioner:
    """A fake `OdsModelProvisioner` — records what it was asked to deploy,
    never touches a database. The real DDL execution is verified separately
    (`tests/contract/test_ddl_render_contract.py`, and by hand against the
    live twin); this proves the ROUTING wires publish to provisioning."""

    def __init__(self) -> None:
        self.calls: list[OdsModel] = []

    def __call__(self, model: OdsModel) -> tuple[str, ...]:
        self.calls.append(model)
        return ("-- fake DDL --",)


class _FailingProvisioner:
    def __call__(self, model: OdsModel) -> tuple[str, ...]:
        raise RuntimeError("the twin is unreachable")


@pytest.fixture
def provisioner() -> _RecordingProvisioner:
    return _RecordingProvisioner()


@pytest.fixture
def client(metadata: MemMetadataDb, provisioner: _RecordingProvisioner) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=MemStoreControlTables(),
        ods_model_provisioner=provisioner,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_generic_routes_carry_an_ods_model_to_published(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    submitted = client.post(
        "/api/objects/ods_model/silver_ods/submit",
        json={"comment": "Member domain v1, ready for review."},
        headers=_as(DATA_ENGINEER),
    )
    assert submitted.status_code == 200
    assert submitted.json()["lifecycle_state"] == "pending_review"

    approved = client.post(
        "/api/objects/ods_model/silver_ods/approve",
        json={"comment": "Both discrepancies decided; matches deployed conventions."},
        headers=_as(PLATFORM_ENGINEER),
    )
    assert approved.status_code == 200
    assert approved.json()["lifecycle_state"] == "approved"

    published = client.post(
        "/api/objects/ods_model/silver_ods/publish",
        headers=_as(PLATFORM_ENGINEER),
    )
    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"
    assert published.json()["warnings"] == []

    stored = metadata.get(ObjectType.ODS_MODEL, "silver_ods")
    assert stored.lifecycle_state is LifecycleState.PUBLISHED


def test_publishing_provisions_the_real_model(
    client: TestClient, provisioner: _RecordingProvisioner
) -> None:
    client.post("/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(DATA_ENGINEER))
    client.post(
        "/api/objects/ods_model/silver_ods/approve",
        json={"comment": "reviewed"},
        headers=_as(PLATFORM_ENGINEER),
    )
    client.post("/api/objects/ods_model/silver_ods/publish", headers=_as(PLATFORM_ENGINEER))

    assert len(provisioner.calls) == 1
    deployed = provisioner.calls[0]
    assert deployed.entity("Members").surrogate_key == "OurId"
    # The decided discrepancies, applied, reaching the provisioner exactly
    # as they would reach a live database.
    assert deployed.entity("Members").column("BatchId").type.value == "string"


def test_a_provisioning_failure_is_a_warning_not_a_500(metadata: MemMetadataDb) -> None:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=MemStoreControlTables(),
        ods_model_provisioner=_FailingProvisioner(),
    )
    with TestClient(app) as client:
        client.post("/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(DATA_ENGINEER))
        client.post(
            "/api/objects/ods_model/silver_ods/approve",
            json={"comment": "reviewed"},
            headers=_as(PLATFORM_ENGINEER),
        )
        published = client.post(
            "/api/objects/ods_model/silver_ods/publish", headers=_as(PLATFORM_ENGINEER)
        )

    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"
    assert "unreachable" in published.json()["warnings"][0]


def test_publishing_with_no_provisioner_fitted_degrades_silently(
    metadata: MemMetadataDb,
) -> None:
    """The mock/dev socket: the governed object still moves; there is simply
    no live `silver_ods` schema to provision."""
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as client:
        client.post("/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(DATA_ENGINEER))
        client.post(
            "/api/objects/ods_model/silver_ods/approve",
            json={"comment": "reviewed"},
            headers=_as(PLATFORM_ENGINEER),
        )
        published = client.post(
            "/api/objects/ods_model/silver_ods/publish", headers=_as(PLATFORM_ENGINEER)
        )

    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"
    assert published.json()["warnings"] == []


def test_a_read_only_caller_may_not_move_the_model(client: TestClient) -> None:
    response = client.post(
        "/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(READ_ONLY)
    )
    assert response.status_code == 403


def test_a_data_steward_holds_approve_but_not_this_objects_lane(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    """A data steward holds `Action.APPROVE` generally (they review mappings,
    rules, glossary terms) — and is still refused here, because plate 14
    routes ODS models to the PLATFORM engineer, never the steward. Routing is
    per-object-type, not a single platform-wide permission. Self-approval
    itself (`SelfApprovalError`) is exercised at the worker level in
    `test_ods_model_deploy.py`: no single test user in this role roster holds
    both SUBMIT_FOR_REVIEW and APPROVE for an ENGINEERED object, so it is not
    reachable through this HTTP surface without a role this fixture invents."""
    client.post("/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(DATA_ENGINEER))
    response = client.post(
        "/api/objects/ods_model/silver_ods/approve",
        json={"comment": "x"},
        headers=_as("dev-steward@cinqcare.test"),
    )
    assert response.status_code == 403


def test_the_deployed_model_still_carries_the_decided_discrepancies(
    client: TestClient, metadata: MemMetadataDb
) -> None:
    client.post("/api/objects/ods_model/silver_ods/submit", json={}, headers=_as(DATA_ENGINEER))
    client.post(
        "/api/objects/ods_model/silver_ods/approve",
        json={"comment": "x"},
        headers=_as(PLATFORM_ENGINEER),
    )
    client.post("/api/objects/ods_model/silver_ods/publish", headers=_as(PLATFORM_ENGINEER))

    published = metadata.get(ObjectType.ODS_MODEL, "silver_ods")
    model = from_governed(published)
    assert model.entity("Members").column("DateOfBirth").type.value == "date"
