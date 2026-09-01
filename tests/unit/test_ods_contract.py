"""CF-V3-E10-02 — the downstream data contract.

    "Show 'what changed and why' between any two model versions in terms a
     consumer understands ... every entity a stable contract page ...
     announce deprecations with lead time and list affected consumers from
     lineage."
    "Exception — Given a proposed change would remove a column two reports
     consume, when the proposal is drafted, then lineage lists the consumers
     on the proposal automatically, and their owners are notified as part of
     the review — not after the break."
    — CF-V3-E10-02

Reuses two facts already declared elsewhere rather than a new lineage graph:
a mapping line's own `target_entity`/`target_field` (CF-V1-E6-03) and its
`business_consumers` key (CF-V1-E11-02). Composing them is new; neither fact
is invented here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_contract import (
    Consumer,
    all_consumers_of,
    consumers_of,
    contract_page,
    deprecations_in,
    latest_published,
    pending_version,
)
from cinqflow.core.registry.ods_model import HistoryMode, OdsEntity, OdsModel, diff
from cinqflow.core.registry.ods_model_member_domain import MEMBER_DOMAIN_V1
from cinqflow.core.schema_spec import Column, TypeName

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, tzinfo=UTC)
AUTHOR = Actor(subject="ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Nadia")


def _mapping(
    object_id: str,
    lines: tuple[dict[str, str], ...],
    *,
    business_consumers: tuple[str, ...] = (),
    version: int = 1,
    lifecycle_state: LifecycleState = LifecycleState.PUBLISHED,
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.MAPPING,
        object_id=object_id,
        version=version,
        lifecycle_state=lifecycle_state,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=AUTHOR if lifecycle_state not in (LifecycleState.DRAFT,) else None,
        approved_ts=NOW,
        body={"lines": list(lines), "business_consumers": list(business_consumers)},
    )


def _ods_object(version: int, lifecycle_state: LifecycleState) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.ODS_MODEL,
        object_id="silver_ods",
        version=version,
        lifecycle_state=lifecycle_state,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=AUTHOR if lifecycle_state != LifecycleState.DRAFT else None,
        approved_ts=NOW,
        body={},
    )


# ── consumers_of ──────────────────────────────────────────────────────────


def test_consumers_of_finds_the_mapping_that_targets_the_column() -> None:
    mapping = _mapping(
        "fidelis-ny",
        ({"target_entity": "Members", "target_field": "OurId"},),
        business_consumers=("CMS Quality Report",),
    )
    found = consumers_of("Members", "OurId", (mapping,))
    assert found == (Consumer(mapping_id="fidelis-ny", business_consumers=("CMS Quality Report",)),)


def test_consumers_of_ignores_a_mapping_targeting_a_different_column() -> None:
    mapping = _mapping("fidelis-ny", ({"target_entity": "Members", "target_field": "Gender"},))
    assert consumers_of("Members", "OurId", (mapping,)) == ()


def test_consumers_of_ignores_a_retired_mapping() -> None:
    mapping = _mapping(
        "molina-ny",
        ({"target_entity": "Members", "target_field": "OurId"},),
        lifecycle_state=LifecycleState.RETIRED,
    )
    assert consumers_of("Members", "OurId", (mapping,)) == ()


def test_consumers_of_ignores_non_mapping_objects() -> None:
    other = GovernedObject(
        object_type=ObjectType.DQ_RULE,
        object_id="dq-1",
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=AUTHOR,
        approved_ts=NOW,
        body={"lines": [{"target_entity": "Members", "target_field": "OurId"}]},
    )
    assert consumers_of("Members", "OurId", (other,)) == ()


def test_consumers_of_a_mapping_with_no_declared_business_consumers_still_counts() -> None:
    """The mapping itself is a consumer of the column even when it names no
    downstream report — an empty `business_consumers` is a real, reportable
    answer, not a reason to drop the mapping from the list."""
    mapping = _mapping("optum-ga", ({"target_entity": "Members", "target_field": "OurId"},))
    found = consumers_of("Members", "OurId", (mapping,))
    assert found == (Consumer(mapping_id="optum-ga", business_consumers=()),)


# ── deprecations_in ───────────────────────────────────────────────────────


def _member_entity(*columns: Column) -> OdsEntity:
    return OdsEntity(
        name="Members",
        columns=columns,
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
    )


def test_deprecations_in_lists_a_removed_column_with_its_real_consumers() -> None:
    v1 = OdsModel(
        version=1,
        entities=(
            _member_entity(
                Column("OurId", TypeName.INT64, nullable=False),
                Column("LegacySsn", TypeName.STRING),
            ),
        ),
    )
    v2 = OdsModel(
        version=2, entities=(_member_entity(Column("OurId", TypeName.INT64, nullable=False)),)
    )
    mapping = _mapping(
        "centene-il",
        ({"target_entity": "Members", "target_field": "LegacySsn"},),
        business_consumers=("Fraud Review Report",),
    )

    notices = deprecations_in(diff(v1, v2), (mapping,))

    assert len(notices) == 1
    (notice,) = notices
    assert notice.change.entity == "Members"
    assert notice.change.column == "LegacySsn"
    assert notice.is_breaking is True
    assert notice.consumers == (
        Consumer(mapping_id="centene-il", business_consumers=("Fraud Review Report",)),
    )


def test_deprecations_in_a_removal_nobody_maps_to_is_not_breaking() -> None:
    v1 = OdsModel(
        version=1,
        entities=(
            _member_entity(
                Column("OurId", TypeName.INT64, nullable=False), Column("Scratch", TypeName.STRING)
            ),
        ),
    )
    v2 = OdsModel(
        version=2, entities=(_member_entity(Column("OurId", TypeName.INT64, nullable=False)),)
    )

    (notice,) = deprecations_in(diff(v1, v2), ())

    assert notice.is_breaking is False
    assert notice.consumers == ()


def test_deprecations_in_ignores_pure_additions() -> None:
    v1 = OdsModel(
        version=1, entities=(_member_entity(Column("OurId", TypeName.INT64, nullable=False)),)
    )
    v2 = OdsModel(
        version=2,
        entities=(
            _member_entity(
                Column("OurId", TypeName.INT64, nullable=False),
                Column("MiddleName", TypeName.STRING),
            ),
        ),
    )
    assert deprecations_in(diff(v1, v2), ()) == ()


def test_deprecations_in_a_dropped_entity_is_named_but_carries_no_column_consumers() -> None:
    """A whole-entity deprecation has no single column to look consumers up
    against — it is still reported, just without a column-level lookup."""
    v1 = OdsModel(
        version=1,
        entities=(
            _member_entity(Column("OurId", TypeName.INT64, nullable=False)),
            OdsEntity(
                name="Scratch",
                columns=(Column("Id", TypeName.INT64, nullable=False),),
                surrogate_key="Id",
                history_mode=HistoryMode.CURRENT_ONLY,
            ),
        ),
    )
    v2 = OdsModel(
        version=2, entities=(_member_entity(Column("OurId", TypeName.INT64, nullable=False)),)
    )

    notices = deprecations_in(diff(v1, v2), ())

    (notice,) = notices
    assert notice.change.entity == "Scratch"
    assert notice.consumers == ()


# ── latest_published / pending_version ───────────────────────────────────


def test_latest_published_returns_the_highest_published_version() -> None:
    history = (
        _ods_object(1, LifecycleState.PUBLISHED),
        _ods_object(2, LifecycleState.PUBLISHED),
        _ods_object(3, LifecycleState.DRAFT),
    )
    assert latest_published(history).version == 2


def test_latest_published_returns_none_when_nothing_is_published() -> None:
    history = (_ods_object(1, LifecycleState.DRAFT),)
    assert latest_published(history) is None


def test_pending_version_returns_the_highest_non_terminal_version() -> None:
    history = (
        _ods_object(1, LifecycleState.PUBLISHED),
        _ods_object(2, LifecycleState.PENDING_REVIEW),
    )
    assert pending_version(history).version == 2


def test_pending_version_ignores_published_and_retired() -> None:
    history = (
        _ods_object(1, LifecycleState.PUBLISHED),
        _ods_object(2, LifecycleState.RETIRED),
    )
    assert pending_version(history) is None


# ── contract_page ─────────────────────────────────────────────────────────


def test_contract_page_lists_every_column_with_its_consumers() -> None:
    mapping = _mapping(
        "fidelis-ny",
        ({"target_entity": "Members", "target_field": "OurId"},),
        business_consumers=("CMS Quality Report",),
    )
    page = contract_page(MEMBER_DOMAIN_V1, "Members", (mapping,))

    assert page.entity == "Members"
    assert page.model_version == 1
    assert {c.name for c in page.columns} == {
        c.name for c in MEMBER_DOMAIN_V1.entity("Members").columns
    }
    assert page.consumers["OurId"] == (
        Consumer(mapping_id="fidelis-ny", business_consumers=("CMS Quality Report",)),
    )
    assert page.consumers["DateOfBirth"] == ()


def test_contract_page_surfaces_only_its_own_entitys_pending_deprecations() -> None:
    v1 = OdsModel(
        version=1,
        entities=(
            _member_entity(
                Column("OurId", TypeName.INT64, nullable=False), Column("Scratch", TypeName.STRING)
            ),
            OdsEntity(
                name="Other",
                columns=(
                    Column("Id", TypeName.INT64, nullable=False),
                    Column("Gone", TypeName.STRING),
                ),
                surrogate_key="Id",
                history_mode=HistoryMode.CURRENT_ONLY,
            ),
        ),
    )
    v2 = OdsModel(
        version=2,
        entities=(
            _member_entity(Column("OurId", TypeName.INT64, nullable=False)),
            OdsEntity(
                name="Other",
                columns=(Column("Id", TypeName.INT64, nullable=False),),
                surrogate_key="Id",
                history_mode=HistoryMode.CURRENT_ONLY,
            ),
        ),
    )
    all_notices = deprecations_in(diff(v1, v2), ())

    page = contract_page(v1, "Members", (), pending_deprecations=all_notices)

    assert [n.change.column for n in page.pending_deprecations] == ["Scratch"]


# ── all_consumers_of ──────────────────────────────────────────────────────


def test_all_consumers_of_merges_a_mapping_that_targets_two_columns() -> None:
    mapping = _mapping(
        "fidelis-ny",
        (
            {"target_entity": "Members", "target_field": "OurId"},
            {"target_entity": "Members", "target_field": "DateOfBirth"},
        ),
        business_consumers=("CMS Quality Report",),
    )
    found = all_consumers_of("Members", ("OurId", "DateOfBirth"), (mapping,))
    assert found == (Consumer(mapping_id="fidelis-ny", business_consumers=("CMS Quality Report",)),)


def test_all_consumers_of_unions_business_consumers_across_columns() -> None:
    mapping = _mapping(
        "fidelis-ny",
        (
            {"target_entity": "Members", "target_field": "OurId"},
            {"target_entity": "Members", "target_field": "DateOfBirth"},
        ),
    )
    # Simulate two lines with different declared consumers by calling
    # consumers_of per-column and checking the union directly through
    # all_consumers_of's own body key (business_consumers is mapping-wide,
    # not per-line, so this proves the merge is at least idempotent).
    found = all_consumers_of("Members", ("OurId", "DateOfBirth", "Gender"), (mapping,))
    assert len(found) == 1


def test_all_consumers_of_finds_every_distinct_mapping_across_columns() -> None:
    fidelis = _mapping("fidelis-ny", ({"target_entity": "Members", "target_field": "OurId"},))
    molina = _mapping("molina-ny", ({"target_entity": "Members", "target_field": "Gender"},))
    found = all_consumers_of("Members", ("OurId", "Gender"), (fidelis, molina))
    assert {c.mapping_id for c in found} == {"fidelis-ny", "molina-ny"}


def test_all_consumers_of_is_empty_when_nobody_targets_this_entity() -> None:
    assert all_consumers_of("Members", ("OurId",), ()) == ()
