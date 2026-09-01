"""CF-V3-E10-01/E10-02 — the canonical ODS model as versioned, governed truth.

    "the client's canonical model workbooks ... deployed as versioned, managed
     table definitions with surrogate keys, source-key retention and history
     handling, so that the model stops living in contested spreadsheets
     ('draft' vs 'final') and becomes one deployed, versioned truth"
    — CF-V3-E10-01

    "Resolve the draft-vs-final workbook discrepancies explicitly during
     review — every difference gets a decision, recorded."
    — CF-V3-E10-01, acceptance criteria

The design decision under test: the canonical ODS model is a GOVERNED OBJECT —
the same DRAFT -> APPROVED -> PUBLISHED lifecycle every other piece of platform
configuration already travels — rather than a second, hand-maintained Python
literal living beside schema_spec.SILVER_ODS_SCHEMA. `render()` is the one pure
function that turns an approved model into the same Schema/Table/Column
vocabulary every other data-layer table is declared in, so the SAME
conformance code that checks bronze and silver_raw checks silver_ods too.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import (
    ChangeKind,
    Decision,
    HistoryMode,
    MissingEffectiveDatingError,
    ModelDiscrepancy,
    OdsEntity,
    OdsModel,
    OdsModelError,
    UndecidedDiscrepancyError,
    as_governed,
    diff,
    from_governed,
    refuse_undecided,
    render,
)
from cinqflow.core.schema_spec import Column, TypeName

pytestmark = pytest.mark.unit

AUTHOR = Actor(
    subject="engineer@cinqcare.test", actor_type=ActorType.HUMAN, display_name="A Data Engineer"
)
STEWARD = Actor(
    subject="steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="A Data Steward"
)


def _members_v1() -> OdsEntity:
    return OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        source_key_columns=("SourceSystemIdType",),
        columns=(
            Column("OurId", TypeName.INT64, nullable=False),
            Column("LinkId", TypeName.STRING, nullable=False),
            Column("FirstName", TypeName.STRING, is_phi=True),
            Column("DateOfBirth", TypeName.DATE, is_phi=True),
            Column("SourceSystemIdType", TypeName.STRING),
            Column("BatchId", TypeName.STRING, nullable=False),
        ),
    )


def _addresses_v1() -> OdsEntity:
    return OdsEntity(
        name="Members_Addresses",
        surrogate_key="AddressRecordId",
        history_mode=HistoryMode.EFFECTIVE_DATED,
        satellite_of="Members",
        source_key_columns=("SourceSystemId",),
        columns=(
            Column("AddressRecordId", TypeName.UUID, nullable=False),
            Column("OurId", TypeName.INT64, nullable=False),
            Column("Address1", TypeName.STRING, is_phi=True),
            Column("EffectiveStartDate", TypeName.DATE, nullable=False),
            Column("EffectiveEndDate", TypeName.DATE),
            Column("SourceSystemId", TypeName.STRING, nullable=False),
            Column("BatchId", TypeName.STRING, nullable=False),
        ),
    )


# ── OdsEntity — the invariants a single entity must hold ─────────────────────


def test_an_entity_without_its_declared_surrogate_key_is_refused() -> None:
    with pytest.raises(OdsModelError, match="surrogate key"):
        OdsEntity(
            name="Members",
            surrogate_key="OurId",
            history_mode=HistoryMode.CURRENT_ONLY,
            columns=(Column("LinkId", TypeName.STRING),),
        )


def test_a_source_key_column_must_be_one_of_the_declared_columns() -> None:
    with pytest.raises(OdsModelError, match="source key"):
        OdsEntity(
            name="Members",
            surrogate_key="OurId",
            history_mode=HistoryMode.CURRENT_ONLY,
            source_key_columns=("NoSuchColumn",),
            columns=(Column("OurId", TypeName.INT64, nullable=False),),
        )


def test_an_effective_dated_entity_must_declare_its_effective_start_column() -> None:
    """ "History per entity is declared: current_only or effective_dated. Both
    are property-tested" — this is that property. An entity cannot be
    effective-dated by name alone; it has to carry the column that makes it so.
    """
    with pytest.raises(MissingEffectiveDatingError):
        OdsEntity(
            name="Members_Addresses",
            surrogate_key="AddressRecordId",
            history_mode=HistoryMode.EFFECTIVE_DATED,
            columns=(Column("AddressRecordId", TypeName.UUID, nullable=False),),
        )


def test_a_current_only_entity_needs_no_effective_dating_column() -> None:
    entity = _members_v1()
    assert entity.history_mode is HistoryMode.CURRENT_ONLY


def test_a_satellite_names_its_parent_entity() -> None:
    assert _addresses_v1().satellite_of == "Members"
    assert _members_v1().satellite_of is None


# ── OdsModel — versioned and fingerprinted ────────────────────────────────────


def test_versions_start_at_one() -> None:
    with pytest.raises(ValueError, match="version"):
        OdsModel(version=0, entities=(_members_v1(),))


def test_two_models_with_the_same_shape_fingerprint_identically() -> None:
    a = OdsModel(version=1, entities=(_members_v1(), _addresses_v1()))
    b = OdsModel(version=1, entities=(_addresses_v1(), _members_v1()))
    assert a.fingerprint == b.fingerprint, "entity order is not part of the shape"


def test_a_nullability_change_produces_a_different_fingerprint() -> None:
    a = OdsModel(version=1, entities=(_members_v1(),))
    changed = OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        source_key_columns=("SourceSystemIdType",),
        columns=(
            Column("OurId", TypeName.INT64, nullable=False),
            Column("LinkId", TypeName.STRING, nullable=True),  # was nullable=False
            Column("FirstName", TypeName.STRING, is_phi=True),
            Column("DateOfBirth", TypeName.DATE, is_phi=True),
            Column("SourceSystemIdType", TypeName.STRING),
            Column("BatchId", TypeName.STRING, nullable=False),
        ),
    )
    b = OdsModel(version=2, entities=(changed,))
    assert a.fingerprint != b.fingerprint


def test_a_model_pinned_by_a_named_fingerprint_is_a_falsifiable_claim() -> None:
    """A conformance report has to say WHICH version an engine was checked
    against, or a green result is unfalsifiable — same law as Schema.fingerprint."""
    model = OdsModel(version=1, entities=(_members_v1(),))
    assert len(model.fingerprint) == 32


# ── render — the same closed type vocabulary, so conformance just works ─────


def test_render_produces_a_silver_ods_schema() -> None:
    model = OdsModel(version=1, entities=(_members_v1(), _addresses_v1()))
    schema = render(model)
    assert schema.name == "silver_ods"
    assert {t.name for t in schema.tables} == {"Members", "Members_Addresses"}


def test_render_retains_every_source_key_beside_the_surrogate_key() -> None:
    """Model rule #1: surrogate keys generated; source identifiers always
    retained. A rendered table that dropped the source key would make a row
    untraceable back to the file it came from."""
    schema = render(OdsModel(version=1, entities=(_addresses_v1(),)))
    table = schema.table("Members_Addresses")
    assert table.primary_key == ("AddressRecordId",)
    assert "SourceSystemId" in {c.name for c in table.columns}


def test_a_rendered_table_carries_no_column_the_model_did_not_declare() -> None:
    """render() is a pure re-shaping, not an enrichment — it must not silently
    inject columns (e.g. a generic audit block) the harvested model never named."""
    schema = render(OdsModel(version=1, entities=(_members_v1(),)))
    table = schema.table("Members")
    assert {c.name for c in table.columns} == {
        "OurId",
        "LinkId",
        "FirstName",
        "DateOfBirth",
        "SourceSystemIdType",
        "BatchId",
    }


# ── the governed round trip — an ODS model is a GovernedObject like any other ─


def test_an_ods_model_round_trips_through_its_governed_body() -> None:
    model = OdsModel(version=3, entities=(_members_v1(), _addresses_v1()))
    obj = as_governed(model, author=AUTHOR, created_ts=datetime(2026, 8, 31, tzinfo=UTC))
    assert obj.object_type is ObjectType.ODS_MODEL
    assert obj.version == 3
    assert from_governed(obj) == model


def test_reading_a_governed_object_of_the_wrong_type_is_refused() -> None:
    from cinqflow.core.registry.contract import ContractColumn, SchemaContract, contract_as_governed

    contract = SchemaContract(
        feed_id="fidelis-downstate-roster",
        version=1,
        columns=(ContractColumn("member_id", TypeName.STRING, nullable=False),),
    )
    obj = contract_as_governed(contract, author=AUTHOR)
    with pytest.raises(OdsModelError, match="not an ODS model"):
        from_governed(obj)


# ── diff — what a downstream consumer needs to know between two versions ────


def test_diff_marks_a_new_column_as_added_and_nothing_else_as_removed() -> None:
    """CF-V3-E10-02's happy path, verbatim: v3 adds normalized payment columns
    and nothing consumed is marked Removed."""
    v2 = OdsModel(version=2, entities=(_members_v1(),))
    added_column = OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        source_key_columns=("SourceSystemIdType",),
        columns=(*_members_v1().columns, Column("NormalizedRiskScore", TypeName.STRING)),
    )
    v3 = OdsModel(version=3, entities=(added_column,))
    result = diff(v2, v3)
    assert result.from_version == 2
    assert result.to_version == 3
    assert any(
        c.column == "NormalizedRiskScore" and c.kind is ChangeKind.ADDED for c in result.added
    )
    assert result.removed == ()


def test_diff_flags_a_dropped_column_as_removed_with_its_old_shape() -> None:
    v1 = OdsModel(version=1, entities=(_members_v1(),))
    dropped = OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        source_key_columns=("SourceSystemIdType",),
        columns=tuple(c for c in _members_v1().columns if c.name != "FirstName"),
    )
    v2 = OdsModel(version=2, entities=(dropped,))
    result = diff(v1, v2)
    removed = [c for c in result.removed if c.column == "FirstName"]
    assert len(removed) == 1
    assert removed[0].was != ""


def test_diff_marks_a_retyped_column_as_changed_not_as_a_drop_and_an_add() -> None:
    v1 = OdsModel(version=1, entities=(_members_v1(),))
    retyped_entity = OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        source_key_columns=("SourceSystemIdType",),
        columns=tuple(
            Column("LinkId", TypeName.STRING, nullable=True) if c.name == "LinkId" else c
            for c in _members_v1().columns
        ),
    )
    v2 = OdsModel(version=2, entities=(retyped_entity,))
    result = diff(v1, v2)
    changed = [c for c in result.changes if c.column == "LinkId"]
    assert len(changed) == 1
    assert changed[0].kind is ChangeKind.RETYPED


def test_diff_reports_a_new_entity_and_a_deprecated_one() -> None:
    v1 = OdsModel(version=1, entities=(_members_v1(),))
    v2 = OdsModel(version=2, entities=(_members_v1(), _addresses_v1()))
    forward = diff(v1, v2)
    assert any(
        c.kind is ChangeKind.NEW_ENTITY and c.entity == "Members_Addresses" for c in forward.changes
    )
    backward = diff(v2, v1)
    assert any(
        c.kind is ChangeKind.DEPRECATED_ENTITY and c.entity == "Members_Addresses"
        for c in backward.changes
    )


def test_an_identical_model_produces_no_changes() -> None:
    model = OdsModel(version=1, entities=(_members_v1(), _addresses_v1()))
    result = diff(model, OdsModel(version=1, entities=model.entities))
    assert result.changes == ()


# ── the discrepancy gate — deploy waits for the steward's call ──────────────


def test_a_model_with_an_undecided_discrepancy_is_not_deployable() -> None:
    open_discrepancy = ModelDiscrepancy(
        entity="Members",
        column="DateOfBirth",
        sources=(("workbook", "datetime"), ("silver_raw.members.date_of_birth (deployed)", "date")),
    )
    with pytest.raises(UndecidedDiscrepancyError, match=r"Members\.DateOfBirth"):
        refuse_undecided((open_discrepancy,))


def test_a_decided_discrepancy_records_who_decided_and_why() -> None:
    decided = ModelDiscrepancy(
        entity="Members",
        column="DateOfBirth",
        sources=(("workbook", "datetime"), ("silver_raw.members.date_of_birth (deployed)", "date")),
        decision=Decision(
            chosen="date",
            decided_by=STEWARD,
            rationale="Matches the already-deployed silver_raw.members.date_of_birth for the "
            "identical business concept; a birth date carries no time component.",
            decided_ts=datetime(2026, 8, 31, tzinfo=UTC),
        ),
    )
    assert decided.is_decided
    refuse_undecided((decided,))  # does not raise


def test_a_decision_with_no_rationale_is_refused() -> None:
    with pytest.raises(OdsModelError, match="rationale"):
        Decision(
            chosen="date",
            decided_by=STEWARD,
            rationale="   ",
            decided_ts=datetime(2026, 8, 31, tzinfo=UTC),
        )
