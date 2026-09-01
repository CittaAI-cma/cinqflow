"""CF-V3-E10-02 through the API — the model browser's version history,
changelog and per-entity contract pages.

    "Show 'what changed and why' between any two model versions in terms a
     consumer understands ... every entity a stable contract page downstream
     teams can link to ... announce deprecations with lead time and list
     affected consumers from lineage."
    "Guardrail — Given a user without permission on this object, when they
     attempt to view or change it, then access is denied, nothing is
     revealed, and the attempt is logged."
    — CF-V3-E10-02

Proven against the REAL harvested Member domain (`MEMBER_DOMAIN_V1`) —
`Members`/`OurId`/`DateOfBirth` are the client's own workbook columns, not a
fixture invented for this test.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import as_governed
from cinqflow.core.registry.ods_model_member_domain import (
    MEMBER_DOMAIN_DISCREPANCIES,
    MEMBER_DOMAIN_V1,
)
from cinqflow.core.schema_spec import Column, TypeName
from cinqflow.workers.ods_model import publish_ods_model

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

DATA_ENGINEER = "dev-engineer@cinqcare.test"
PLATFORM_ENGINEER = "dev-platform@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
AUTHOR = Actor(subject=DATA_ENGINEER, actor_type=ActorType.HUMAN, display_name="Arun Menon")
REVIEWER = Actor(subject=PLATFORM_ENGINEER, actor_type=ActorType.HUMAN, display_name="Sam Patel")
ENGINEERED_ROLES = frozenset({Role.PLATFORM_ENGINEER})
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _publish_v1(metadata: MemMetadataDb) -> GovernedObject:
    return publish_ods_model(
        metadata,
        MEMBER_DOMAIN_V1,
        MEMBER_DOMAIN_DISCREPANCIES,
        author=AUTHOR,
        reviewer=REVIEWER,
        reviewer_roles=ENGINEERED_ROLES,
        publisher=REVIEWER,
        publisher_roles=ENGINEERED_ROLES,
        review_comment="Member domain v1, ready for review.",
        approval_comment="Both discrepancies decided; matches deployed conventions.",
        now=NOW,
    )


@pytest.fixture
def metadata() -> MemMetadataDb:
    store = MemMetadataDb()
    _publish_v1(store)
    return store


def _mapping(
    object_id: str,
    lines: tuple[dict[str, str], ...],
    *,
    business_consumers: tuple[str, ...] = (),
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.MAPPING,
        object_id=object_id,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=REVIEWER,
        approved_ts=NOW,
        body={"lines": list(lines), "business_consumers": list(business_consumers)},
    )


@pytest.fixture
def client(metadata: MemMetadataDb) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as test_client:
        yield test_client


# ── /api/ods-model ────────────────────────────────────────────────────────


def test_summary_reports_the_published_version_and_its_entities(client: TestClient) -> None:
    response = client.get("/api/ods-model", headers=_as(READ_ONLY))
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["published_by"] == PLATFORM_ENGINEER
    assert set(body["entities"]) == {"Members", "Members_Addresses"}


def test_summary_404s_when_nothing_has_ever_been_published() -> None:
    empty = MemMetadataDb()
    app = create_app(authn=StaticAuthn(), metadata_db=empty, control_tables=MemStoreControlTables())
    with TestClient(app) as client:
        response = client.get("/api/ods-model", headers=_as(READ_ONLY))
    assert response.status_code == 404


def test_an_unauthenticated_caller_is_denied_and_nothing_is_revealed(client: TestClient) -> None:
    """No token at all — the platform-wide guardrail every VIEW route shares,
    never bypassed for this one. E10-02's "access denied, nothing revealed"
    guardrail: the 401 names no entity, no version, nothing about the model."""
    response = client.get("/api/ods-model")
    assert response.status_code == 401
    assert "Members" not in response.text
    assert "silver_ods" not in response.text


# ── /api/ods-model/versions ───────────────────────────────────────────────


def test_versions_lists_history_newest_first_and_flags_the_current_one(
    client: TestClient,
) -> None:
    response = client.get("/api/ods-model/versions", headers=_as(READ_ONLY))
    assert response.status_code == 200
    (only,) = response.json()
    assert only["version"] == 1
    assert only["lifecycle_state"] == "published"
    assert only["is_current"] is True
    assert only["approved_by_subject"] == PLATFORM_ENGINEER


def test_versions_404s_when_the_model_has_never_been_drafted() -> None:
    empty = MemMetadataDb()
    app = create_app(authn=StaticAuthn(), metadata_db=empty, control_tables=MemStoreControlTables())
    with TestClient(app) as client:
        response = client.get("/api/ods-model/versions", headers=_as(READ_ONLY))
    assert response.status_code == 404


# ── /api/ods-model/versions/diff ──────────────────────────────────────────


def test_changelog_conflicts_when_only_one_version_exists(client: TestClient) -> None:
    response = client.get("/api/ods-model/versions/diff", headers=_as(READ_ONLY))
    assert response.status_code == 409


def test_changelog_between_two_published_versions_reports_the_addition_and_its_rationale(
    metadata: MemMetadataDb,
) -> None:
    v2 = replace(
        MEMBER_DOMAIN_V1,
        version=2,
        entities=(
            replace(
                MEMBER_DOMAIN_V1.entity("Members"),
                columns=(
                    *MEMBER_DOMAIN_V1.entity("Members").columns,
                    Column("PreferredLanguage", TypeName.STRING),
                ),
            ),
            MEMBER_DOMAIN_V1.entity("Members_Addresses"),
        ),
    )
    publish_ods_model(
        metadata,
        v2,
        (),
        author=AUTHOR,
        reviewer=REVIEWER,
        reviewer_roles=ENGINEERED_ROLES,
        publisher=REVIEWER,
        publisher_roles=ENGINEERED_ROLES,
        review_comment="Adds PreferredLanguage per the Q3 workbook addendum.",
        approval_comment="Additive only; no downstream consumer is affected.",
        now=NOW,
    )
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as client:
        response = client.get("/api/ods-model/versions/diff", headers=_as(READ_ONLY))

    assert response.status_code == 200
    body = response.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert body["removed"] == []
    assert {c["column"] for c in body["added"]} == {"PreferredLanguage"}
    assert "no downstream consumer is affected" in body["rationale"]


# ── /api/ods-model/{entity} ───────────────────────────────────────────────


def test_contract_page_lists_columns_and_their_real_consumers(metadata: MemMetadataDb) -> None:
    metadata.save(
        _mapping(
            "fidelis-ny",
            ({"target_entity": "Members", "target_field": "OurId"},),
            business_consumers=("CMS Quality Report",),
        )
    )
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as client:
        response = client.get("/api/ods-model/Members", headers=_as(READ_ONLY))

    assert response.status_code == 200
    body = response.json()
    assert body["entity"] == "Members"
    assert body["model_version"] == 1
    assert {c["name"] for c in body["columns"]} >= {"OurId", "DateOfBirth", "BatchId"}
    (our_id_consumer,) = body["consumers"]["OurId"]
    assert our_id_consumer["mapping_id"] == "fidelis-ny"
    assert our_id_consumer["business_consumers"] == ["CMS Quality Report"]
    assert body["consumers"]["DateOfBirth"] == []
    assert body["pending_deprecations"] == []


def test_contract_page_404s_for_an_unknown_entity(client: TestClient) -> None:
    response = client.get("/api/ods-model/NoSuchEntity", headers=_as(READ_ONLY))
    assert response.status_code == 404


def test_contract_page_surfaces_a_pending_removal_with_its_real_consumers_before_publish(
    metadata: MemMetadataDb,
) -> None:
    """CF-V3-E10-02's exception, verbatim: a proposed removal names its
    consumers on the CURRENT contract page — during review, not after the
    version that drops the column ever publishes."""
    metadata.save(
        _mapping(
            "molina-ny",
            ({"target_entity": "Members", "target_field": "BatchId"},),
            business_consumers=("Reconciliation Report",),
        )
    )
    v2_draft = replace(
        MEMBER_DOMAIN_V1,
        version=2,
        entities=(
            replace(
                MEMBER_DOMAIN_V1.entity("Members"),
                columns=tuple(
                    c for c in MEMBER_DOMAIN_V1.entity("Members").columns if c.name != "BatchId"
                ),
            ),
            MEMBER_DOMAIN_V1.entity("Members_Addresses"),
        ),
    )
    metadata.save(as_governed(v2_draft, author=AUTHOR, created_ts=NOW))

    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as client:
        response = client.get("/api/ods-model/Members", headers=_as(READ_ONLY))

    assert response.status_code == 200
    body = response.json()
    # v1 is still what is published — the removal has not happened yet.
    assert "BatchId" in {c["name"] for c in body["columns"]}
    (notice,) = body["pending_deprecations"]
    assert notice["change"]["column"] == "BatchId"
    assert notice["is_breaking"] is True
    assert notice["consumers"][0]["mapping_id"] == "molina-ny"
