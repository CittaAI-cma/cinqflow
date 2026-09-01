"""CF-V3-E10-01 — deploying the canonical ODS model as governed, versioned truth.

    "Given the claims model review resolved all differences, when the model
     deploys, then the ODS structures exist with audit columns and version
     tags."
    "Given two workbook versions disagree on a column's nullability ...
     deployment waits for the steward's call, which is recorded with
     rationale."
    — CF-V3-E10-01

Proven against the REAL harvested Member domain (`MEMBER_DOMAIN_V1`,
`MEMBER_DOMAIN_DISCREPANCIES`) — the client's own workbook, not a fixture
invented for this test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.lifecycle import ApprovalRoutingError, LifecycleViolationError
from cinqflow.core.model.governed import (
    Actor,
    LifecycleState,
    ObjectType,
    SelfApprovalError,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import (
    ModelDiscrepancy,
    OdsModel,
    UndecidedDiscrepancyError,
    from_governed,
)
from cinqflow.core.registry.ods_model_member_domain import (
    MEMBER_DOMAIN_DISCREPANCIES,
    MEMBER_DOMAIN_V1,
)
from cinqflow.core.schema_spec import Column, TypeName
from cinqflow.workers.ods_model import publish_ods_model

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, tzinfo=UTC)
AUTHOR = Actor(subject="engineer@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")
REVIEWER = Actor(
    subject="platform-eng@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam"
)
PUBLISHER = REVIEWER
ENGINEERED_ROLES = frozenset({Role.PLATFORM_ENGINEER})


def _publish_member_domain(metadata: MemMetadataDb) -> object:
    return publish_ods_model(
        metadata,
        MEMBER_DOMAIN_V1,
        MEMBER_DOMAIN_DISCREPANCIES,
        author=AUTHOR,
        reviewer=REVIEWER,
        reviewer_roles=ENGINEERED_ROLES,
        publisher=PUBLISHER,
        publisher_roles=ENGINEERED_ROLES,
        review_comment="Member domain v1, spine + one satellite, ready for review.",
        approval_comment="Both discrepancies decided; matches deployed silver_raw conventions.",
        now=NOW,
    )


def test_the_real_member_domain_deploys_end_to_end() -> None:
    """The happy path, on the client's own harvested workbook."""
    metadata = MemMetadataDb()

    published = _publish_member_domain(metadata)

    assert published.lifecycle_state is LifecycleState.PUBLISHED
    assert published.version == 1
    assert published.approved_by is not None
    round_tripped = from_governed(published)
    assert round_tripped.entity("Members").surrogate_key == "OurId"
    assert round_tripped.entity("Members_Addresses").satellite_of == "Members"


def test_the_decided_discrepancies_applied_resolution_survives_the_round_trip() -> None:
    """The workbook said `datetime` and `int`; the deployed model says DATE
    and STRING — the decision, not the disagreement, is what ships."""
    metadata = MemMetadataDb()
    published = _publish_member_domain(metadata)
    model = from_governed(published)
    assert model.entity("Members").column("DateOfBirth").type is TypeName.DATE
    assert model.entity("Members").column("BatchId").type is TypeName.STRING


def test_every_transition_is_a_separate_audited_act() -> None:
    """The object's own row moves in place (one version, its current state);
    the audit trail is what proves three DIFFERENT acts happened, by three
    different reasons, in order."""
    metadata = MemMetadataDb()
    _publish_member_domain(metadata)
    # All three share one `stamp` (one call, one moment) — with tied
    # timestamps, `read_audit`'s stable sort preserves insertion order.
    trail = [e.action for e in metadata.read_audit(object_id="silver_ods")]
    assert trail == ["transition:pending_review", "transition:approved", "transition:published"]
    (only_version,) = metadata.history(ObjectType.ODS_MODEL, "silver_ods")
    assert only_version.lifecycle_state is LifecycleState.PUBLISHED


def test_an_undecided_discrepancy_blocks_deployment() -> None:
    """ "Given two workbook versions disagree ... deployment waits for the
    steward's call." — the exception path, on a synthetic open question."""
    metadata = MemMetadataDb()
    open_discrepancy = ModelDiscrepancy(
        entity="Members",
        column="Gender",
        sources=(
            ("workbook draft: Enrollment_Lake_Models_Draft.xlsx", "string(1)"),
            ("workbook final: Enrollment_Lake_Models.xlsx", "string(10)"),
        ),
        # No decision recorded.
    )

    with pytest.raises(UndecidedDiscrepancyError, match=r"Members\.Gender"):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            (*MEMBER_DOMAIN_DISCREPANCIES, open_discrepancy),
            author=AUTHOR,
            reviewer=REVIEWER,
            reviewer_roles=ENGINEERED_ROLES,
            publisher=PUBLISHER,
            publisher_roles=ENGINEERED_ROLES,
            review_comment="Draft with one open question.",
            approval_comment="Reviewed; one discrepancy still open.",
            now=NOW,
        )


def test_a_blocked_deploy_leaves_the_object_approved_not_published() -> None:
    """The gate fires between Approved and Published — the object is left in
    a real, visible state (Approved, waiting), never silently rolled back or
    half-transitioned."""
    metadata = MemMetadataDb()
    open_discrepancy = ModelDiscrepancy(entity="Members", column="Gender", sources=())

    with pytest.raises(UndecidedDiscrepancyError):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            (*MEMBER_DOMAIN_DISCREPANCIES, open_discrepancy),
            author=AUTHOR,
            reviewer=REVIEWER,
            reviewer_roles=ENGINEERED_ROLES,
            publisher=PUBLISHER,
            publisher_roles=ENGINEERED_ROLES,
            review_comment="x",
            approval_comment="y",
            now=NOW,
        )

    stuck = metadata.get(ObjectType.ODS_MODEL, "silver_ods")
    assert stuck.lifecycle_state is LifecycleState.APPROVED


def test_the_author_may_not_review_their_own_model() -> None:
    """The universal negative, inherited from `core.lifecycle.approve` —
    never re-implemented here, only exercised."""
    metadata = MemMetadataDb()
    with pytest.raises(SelfApprovalError):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            MEMBER_DOMAIN_DISCREPANCIES,
            author=AUTHOR,
            reviewer=AUTHOR,
            reviewer_roles=ENGINEERED_ROLES,
            publisher=PUBLISHER,
            publisher_roles=ENGINEERED_ROLES,
            review_comment="x",
            approval_comment="y",
            now=NOW,
        )


def test_approval_without_a_rationale_is_refused() -> None:
    metadata = MemMetadataDb()
    with pytest.raises(LifecycleViolationError):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            MEMBER_DOMAIN_DISCREPANCIES,
            author=AUTHOR,
            reviewer=REVIEWER,
            reviewer_roles=ENGINEERED_ROLES,
            publisher=PUBLISHER,
            publisher_roles=ENGINEERED_ROLES,
            review_comment="x",
            approval_comment="   ",
            now=NOW,
        )


def test_reviewing_requires_the_engineered_routes_own_role() -> None:
    """A steward — the WRONG lane for this object type, plate 14's own
    reasoning — may not review an ODS model even if they hold APPROVE
    generally; `_ENGINEERED`'s reviewers are platform engineers only."""
    metadata = MemMetadataDb()
    with pytest.raises(ApprovalRoutingError, match="ods_model"):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            MEMBER_DOMAIN_DISCREPANCIES,
            author=AUTHOR,
            reviewer=REVIEWER,
            reviewer_roles=frozenset({Role.DATA_STEWARD}),
            publisher=PUBLISHER,
            publisher_roles=ENGINEERED_ROLES,
            review_comment="x",
            approval_comment="y",
            now=NOW,
        )


def test_publishing_requires_the_engineered_routes_own_role() -> None:
    """Reviewed correctly, but the publisher is the wrong lane — the second,
    independent routing check `_ENGINEERED`'s publishers gate."""
    metadata = MemMetadataDb()
    with pytest.raises(ApprovalRoutingError, match="ods_model"):
        publish_ods_model(
            metadata,
            MEMBER_DOMAIN_V1,
            MEMBER_DOMAIN_DISCREPANCIES,
            author=AUTHOR,
            reviewer=REVIEWER,
            reviewer_roles=ENGINEERED_ROLES,
            publisher=PUBLISHER,
            publisher_roles=frozenset({Role.DATA_STEWARD}),
            review_comment="x",
            approval_comment="y",
            now=NOW,
        )


def test_a_column_typed_int_for_batch_id_would_have_been_a_defect_not_a_style_choice() -> None:
    """Guards the decision itself, not just its presence: if a future harvest
    forgot to apply the standing BatchId->STRING convention, this would fail
    — proving the resolution is enforced, not merely documented."""
    drifted_entity = replace(
        MEMBER_DOMAIN_V1.entity("Members"),
        columns=tuple(
            Column("BatchId", TypeName.INT64, nullable=False) if c.name == "BatchId" else c
            for c in MEMBER_DOMAIN_V1.entity("Members").columns
        ),
    )
    drifted_model = OdsModel(
        version=1, entities=(drifted_entity, MEMBER_DOMAIN_V1.entity("Members_Addresses"))
    )
    assert drifted_model.entity("Members").column("BatchId").type is TypeName.INT64
    assert MEMBER_DOMAIN_V1.entity("Members").column("BatchId").type is TypeName.STRING
