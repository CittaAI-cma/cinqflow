"""CF-V3-E10-01 — the member domain, harvested from `Enrollment_Lake_Models.xlsx`.

The fixtures under test are REAL COLUMNS, harvested field-for-field from the
`SilverODS` sheet of the client's own workbook — the same evidentiary standard
`test_glossary.py` holds BG-004 to. A test written against invented columns
would prove the machinery parses something; this proves it deploys what the
client actually designed.

TWO REAL DISCREPANCIES surfaced during harvest, both resolved once (see
`ods_model.py`'s module docstring for the full reasoning) and both recorded
here as `MEMBER_DOMAIN_DISCREPANCIES` rather than silently applied:

  1. `DateOfBirth` (and every other date-only concept): the workbook says
     `datetime`; `silver_raw.members.date_of_birth`, already deployed, says
     DATE for the identical concept.
  2. `BatchId`: the workbook says `int`; every `batch_id` already deployed
     across control tables, bronze and silver_raw says STRING.

PARTIAL HARVEST, STATED RATHER THAN HIDDEN. `Enrollment_Lake_Models.xlsx`
carries nine entities (Members plus eight satellites); the Claims and ADT
workbooks carry their own domains again, each with sheets literally named
"(New)" beside the originals — the draft-vs-final conflict the story
describes, waiting to be diffed. This harvest covers Members and
Members_Addresses: the spine, and one satellite, which is enough to prove the
surrogate-key/source-key/history-mode pattern end to end. The remaining seven
satellites and the Claims/ADT domains are the next harvest, and repeat this
exact pattern — see the module docstring below for the concrete next step.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import (
    ChangeKind,
    HistoryMode,
    OdsEntity,
    OdsModel,
    as_governed,
    diff,
    from_governed,
    refuse_undecided,
    render,
)
from cinqflow.core.registry.ods_model_member_domain import (
    MEMBER_DOMAIN_DISCREPANCIES,
    MEMBER_DOMAIN_V1,
)
from cinqflow.core.schema_spec import Column, TypeName

pytestmark = pytest.mark.unit

AUTHOR = Actor(subject="engineer@cinqcare.test", actor_type=ActorType.HUMAN)


def test_the_partial_harvest_covers_exactly_the_spine_and_one_satellite() -> None:
    assert {e.name for e in MEMBER_DOMAIN_V1.entities} == {"Members", "Members_Addresses"}


def test_members_is_the_spine_current_only_keyed_by_our_id() -> None:
    members = MEMBER_DOMAIN_V1.entity("Members")
    assert members.surrogate_key == "OurId"
    assert members.history_mode is HistoryMode.CURRENT_ONLY
    assert members.satellite_of is None


def test_members_addresses_is_effective_dated_and_names_its_parent() -> None:
    addresses = MEMBER_DOMAIN_V1.entity("Members_Addresses")
    assert addresses.surrogate_key == "AddressRecordId"
    assert addresses.history_mode is HistoryMode.EFFECTIVE_DATED
    assert addresses.satellite_of == "Members"
    assert "SourceSystemId" in addresses.source_key_columns


@pytest.mark.parametrize("column", ["FirstName", "LastName", "DateOfBirth", "GuardianEmail"])
def test_member_pii_is_flagged_phi(column: str) -> None:
    assert MEMBER_DOMAIN_V1.entity("Members").column(column).is_phi


@pytest.mark.parametrize("column", ["Address1", "City", "Zip"])
def test_address_pii_is_flagged_phi(column: str) -> None:
    assert MEMBER_DOMAIN_V1.entity("Members_Addresses").column(column).is_phi


def test_the_harvested_column_counts_match_the_workbook_exactly() -> None:
    """Members carries 33 columns and Members_Addresses 27 in the source
    sheet — a silent drop during transcription is exactly the failure a count
    assertion catches."""
    assert len(MEMBER_DOMAIN_V1.entity("Members").columns) == 33
    assert len(MEMBER_DOMAIN_V1.entity("Members_Addresses").columns) == 27


def test_date_of_birth_resolves_to_date_not_the_workbooks_datetime() -> None:
    assert MEMBER_DOMAIN_V1.entity("Members").column("DateOfBirth").type is TypeName.DATE


def test_a_true_point_in_time_event_stays_a_timestamp() -> None:
    """RecordCreationDate is a system event, not a business date — the
    datetime -> DATE decision applies to date-SHAPED concepts, not every
    datetime column the workbook happens to type that way."""
    assert (
        MEMBER_DOMAIN_V1.entity("Members").column("RecordCreationDate").type
        is TypeName.TIMESTAMP_UTC
    )


@pytest.mark.parametrize("entity_name", ["Members", "Members_Addresses"])
def test_batch_id_resolves_to_string_not_the_workbooks_int(entity_name: str) -> None:
    entity = MEMBER_DOMAIN_V1.entity(entity_name)
    assert entity.column("BatchId").type is TypeName.STRING
    assert entity.column("BatchId").nullable is False


def test_every_harvested_discrepancy_is_decided() -> None:
    """The model this harvest PRODUCES is deployable — refuse_undecided must
    not raise against it, which is the property the gate exists to prove."""
    refuse_undecided(MEMBER_DOMAIN_DISCREPANCIES)


def test_the_discrepancies_name_both_the_workbook_and_the_deployed_precedent() -> None:
    by_column = {d.column: d for d in MEMBER_DOMAIN_DISCREPANCIES}
    assert "DateOfBirth" in by_column
    assert "BatchId" in by_column
    for discrepancy in by_column.values():
        source_names = {name for name, _ in discrepancy.sources}
        assert any("workbook" in name for name in source_names)
        assert discrepancy.decision is not None
        assert discrepancy.decision.decided_by.actor_type is ActorType.HUMAN


def test_the_model_renders_without_error_and_keeps_the_effective_dating() -> None:
    schema = render(MEMBER_DOMAIN_V1)
    table = schema.table("Members_Addresses")
    assert "EffectiveStartDate" in {c.name for c in table.columns}
    assert table.primary_key == ("AddressRecordId",)


def test_the_harvested_model_round_trips_through_its_governed_body() -> None:
    obj = as_governed(MEMBER_DOMAIN_V1, author=AUTHOR)
    assert from_governed(obj) == MEMBER_DOMAIN_V1


def test_diffing_the_harvest_against_itself_finds_nothing_to_report() -> None:
    result = diff(MEMBER_DOMAIN_V1, MEMBER_DOMAIN_V1)
    assert result.changes == ()


def test_a_future_version_adding_a_column_reports_it_added_not_removed() -> None:
    members = MEMBER_DOMAIN_V1.entity("Members")
    grown = OdsEntity(
        name=members.name,
        surrogate_key=members.surrogate_key,
        history_mode=members.history_mode,
        source_key_columns=members.source_key_columns,
        columns=(*members.columns, Column("NormalizedRiskScore", TypeName.STRING)),
    )
    v2 = OdsModel(version=2, entities=(grown, MEMBER_DOMAIN_V1.entity("Members_Addresses")))
    result = diff(MEMBER_DOMAIN_V1, v2)
    assert any(
        c.column == "NormalizedRiskScore" and c.kind is ChangeKind.ADDED for c in result.added
    )
    assert result.removed == ()
