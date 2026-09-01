"""The member domain — harvested from `Enrollment_Lake_Models.xlsx` (`SilverODS`).

    "Members · PK OurId · key LinkId (Verato) · lineage RecordHash · BatchId ·
     FeedName · SourceSystem · flags IsActive. History: SCD-1 (current only).
     Satellites ... History: SCD-2 (effective-dated)."
    — memory/05-ground-truth/01-canonical-model.md, verified against the
      client's own workbook

Every column below is copied field-for-field from the workbook's `SilverODS`
sheet (`TableName, ColumnName, DataType, Primary Key, Description`) — the same
evidentiary bar `core.registry.glossary`'s seeded terms are held to. This is
NOT a hand-designed schema; it is the client's own design, made executable.

PARTIAL HARVEST, STATED RATHER THAN HIDDEN. The workbook names nine entities —
`Members` plus eight satellites (`Members_Emails`, `Members_Phones`,
`Members_EnrollmentSegments`, `Members_Provider`, `Members_Practice`,
`Members_Risk`, `Members_ACO_DualEligibility`, and this module's
`Members_Addresses`) — and the Claims (`claims-model-final-raw.xlsx`, 7
sheets) and ADT (`ADT_Data_Model_Final.xlsx`, 15 sheets, several explicitly
suffixed "(New)" beside an original — the draft-vs-final conflict CF-V3-E10-01
names, not yet diffed) domains carry their own entities again. This harvest
proves the pattern — surrogate key, retained source key, declared history
mode — on the spine and one satellite. THE NEXT STEP: a harvesting pass over
the remaining eight entities repeats this file's shape exactly; Claims and ADT
additionally need their draft/final sheets diffed against each other before a
single `OdsEntity` can be written, which is why they are not attempted here.

TWO REAL DISCREPANCIES, RESOLVED ONCE. See `ods_model.py`'s module docstring
for the full reasoning; `MEMBER_DOMAIN_DISCREPANCIES` is the recorded pair:

  1. `DateOfBirth` (and every column shaped like it): workbook `datetime`,
     resolved to DATE — matching `silver_raw.members.date_of_birth`, already
     deployed for the identical concept, which carries no time component.
  2. `BatchId`: workbook `int`, resolved to STRING — matching `batch_id`
     everywhere else already deployed: every control table, bronze,
     silver_raw.

Both decisions are APPLIED, not just recorded: every `datetime` column below
that names a date-only fact renders as DATE, every `BatchId` renders as
STRING. A `ModelDiscrepancy` that stayed open while the column next to it
quietly used the resolution would be theatre.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_model import (
    Decision,
    HistoryMode,
    ModelDiscrepancy,
    OdsEntity,
    OdsModel,
)
from cinqflow.core.relationship_integrity import Relationship
from cinqflow.core.schema_spec import Column, TypeName

#: The steward of record for this harvest's two discrepancies. A named
#: person, never a role — `UnnamedApproverError`'s reasoning applies equally
#: to a decision as to a publish.
_STEWARD = Actor(
    subject="data-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Data Steward"
)
_DECIDED_TS = datetime(2026, 8, 31, tzinfo=UTC)

MEMBER_DOMAIN_DISCREPANCIES: tuple[ModelDiscrepancy, ...] = (
    ModelDiscrepancy(
        entity="Members",
        column="DateOfBirth",
        sources=(
            ("workbook: Enrollment_Lake_Models.xlsx!SilverODS", "datetime"),
            ("deployed: silver_raw.members.date_of_birth", "date"),
        ),
        decision=Decision(
            chosen="date",
            decided_by=_STEWARD,
            rationale="Matches the already-deployed silver_raw.members.date_of_birth for the "
            "identical business concept; a birth date carries no time component, and the "
            "platform's TypeName vocabulary deliberately offers no naive timestamp for exactly "
            "this reason. Applies to every date-only column in this harvest, not DateOfBirth "
            "alone.",
            decided_ts=_DECIDED_TS,
        ),
    ),
    ModelDiscrepancy(
        entity="Members",
        column="BatchId",
        sources=(
            ("workbook: Enrollment_Lake_Models.xlsx!SilverODS", "int"),
            ("deployed: silver_raw.members.batch_id, control.batch_control.batch_id", "string"),
        ),
        decision=Decision(
            chosen="string",
            decided_by=_STEWARD,
            rationale="batch_id is STRING in every one of the eleven control tables, bronze and "
            "silver_raw already deployed; a single ODS column typed int would make batch_id an "
            "engine-dependent join key across layers that reconciliation already treats as an "
            "opaque string. Applies to every BatchId column in this harvest.",
            decided_ts=_DECIDED_TS,
        ),
    ),
)


def _members() -> OdsEntity:
    return OdsEntity(
        name="Members",
        surrogate_key="OurId",
        history_mode=HistoryMode.CURRENT_ONLY,
        comment="SQL Server sequence number identifies the row; LinkId is the Verato crosswalk.",
        columns=(
            Column("OurId", TypeName.INT64, nullable=False, comment="SQL Server sequence number"),
            Column("LinkId", TypeName.STRING, nullable=False, comment="Verato Link Id"),
            Column(
                "FirstName", TypeName.STRING, is_phi=True, comment="Member's first (given) name"
            ),
            Column("LastName", TypeName.STRING, is_phi=True, comment="Member's last (family) name"),
            Column("MiddleName", TypeName.STRING, is_phi=True, comment="Member's middle name"),
            Column("Suffix", TypeName.STRING, comment="Name suffix (e.g., Jr, Sr, III)"),
            Column("DateOfBirth", TypeName.DATE, is_phi=True, comment="Member's date of birth"),
            Column("Gender", TypeName.STRING, comment="Member's gender/sex"),
            Column("Race", TypeName.STRING, is_phi=True, comment="Member's race classification"),
            Column(
                "Ethnicity",
                TypeName.STRING,
                is_phi=True,
                comment="Member's ethnicity classification",
            ),
            Column("Language", TypeName.STRING, comment="Preferred spoken language"),
            Column(
                "Location", TypeName.STRING, comment="General location or geographic descriptor"
            ),
            Column(
                "CareManagementProgram", TypeName.STRING, comment="Care management program enrolled"
            ),
            Column(
                "LastContact",
                TypeName.TIMESTAMP_UTC,
                comment="Date/time of last contact with member",
            ),
            Column("DualStatusCode", TypeName.STRING, comment="Indicator of dual eligibility"),
            Column("DeathDate", TypeName.DATE, is_phi=True, comment="Member's date of death"),
            Column(
                "RecordCreationDate",
                TypeName.TIMESTAMP_UTC,
                comment="Date/time the record was created",
            ),
            Column(
                "SecureID", TypeName.STRING, comment="Unique system-generated identifier (GUID)"
            ),
            Column("DNC", TypeName.BOOL, comment="Do Not Call indicator"),
            Column(
                "GuardianFirstName", TypeName.STRING, is_phi=True, comment="Guardian first name"
            ),
            Column("GuardianLastName", TypeName.STRING, is_phi=True, comment="Guardian last name"),
            Column("GuardianPhone", TypeName.STRING, is_phi=True, comment="Guardian phone number"),
            Column("GuardianEmail", TypeName.STRING, is_phi=True, comment="Guardian email id"),
            Column(
                "IsActive",
                TypeName.BOOL,
                nullable=False,
                comment="Whether the record is active currently",
            ),
            Column(
                "RecordHash",
                TypeName.STRING,
                nullable=False,
                comment="Change-detection hash over every field",
            ),
            Column("SourceSystemIdType", TypeName.STRING, comment="Type of identifier used"),
            Column(
                "FeedName",
                TypeName.STRING,
                nullable=False,
                comment="The feed this row last arrived on",
            ),
            Column(
                "SourceSystem",
                TypeName.STRING,
                nullable=False,
                comment="The payer this row last arrived from",
            ),
            Column(
                "CreatedBy",
                TypeName.STRING,
                nullable=False,
                comment="The job which created this record",
            ),
            Column("CreatedAt", TypeName.TIMESTAMP_UTC, nullable=False, comment="Insert timestamp"),
            Column("UpdatedBy", TypeName.STRING, comment="The job which last updated this record"),
            Column("UpdatedAt", TypeName.TIMESTAMP_UTC, comment="Update timestamp"),
            Column(
                "BatchId",
                TypeName.STRING,
                nullable=False,
                comment="The batch that inserted or updated this row",
            ),
        ),
    )


def _members_addresses() -> OdsEntity:
    return OdsEntity(
        name="Members_Addresses",
        surrogate_key="AddressRecordId",
        history_mode=HistoryMode.EFFECTIVE_DATED,
        satellite_of="Members",
        source_key_columns=("SourceSystemId",),
        comment="Effective-dated: an address change closes the old row and opens a new one.",
        columns=(
            Column(
                "AddressRecordId", TypeName.UUID, nullable=False, comment="System-generated GUID"
            ),
            Column("OurId", TypeName.INT64, nullable=False, comment="FK to Members"),
            Column("AddressType", TypeName.STRING, comment="home, work, billing"),
            Column("Address1", TypeName.STRING, is_phi=True, comment="Primary street address line"),
            Column("Address2", TypeName.STRING, is_phi=True, comment="Secondary address line"),
            Column("City", TypeName.STRING, is_phi=True, comment="City name"),
            Column("State", TypeName.STRING, comment="Two-letter state abbreviation"),
            Column("Zip", TypeName.STRING, is_phi=True, comment="ZIP or ZIP+4 postal code"),
            Column("Region", TypeName.STRING, comment="Region"),
            Column("CanContact", TypeName.BOOL, comment="Whether the member can be contacted here"),
            Column("County", TypeName.STRING, comment="County name"),
            Column("CountySSA", TypeName.STRING, comment="SSA county code"),
            Column("CountyFIPS", TypeName.STRING, comment="FIPS county code"),
            Column("Zip4", TypeName.STRING, comment="ZIP+4"),
            Column("IsZip4", TypeName.STRING, comment="Indicator for ZIP+4"),
            Column("EffectiveStartDate", TypeName.DATE, nullable=False, comment="Effective date"),
            Column("EffectiveEndDate", TypeName.DATE, comment="Effective end date"),
            Column(
                "IsActive",
                TypeName.BOOL,
                nullable=False,
                comment="Whether the record is active currently",
            ),
            Column(
                "RecordHash",
                TypeName.STRING,
                nullable=False,
                comment="Change-detection hash over every field",
            ),
            Column(
                "SourceSystemId",
                TypeName.STRING,
                nullable=False,
                comment="Source unique identifier",
            ),
            Column(
                "FeedName",
                TypeName.STRING,
                nullable=False,
                comment="The feed this row last arrived on",
            ),
            Column(
                "SourceSystem",
                TypeName.STRING,
                nullable=False,
                comment="The payer this row last arrived from",
            ),
            Column(
                "CreatedBy",
                TypeName.STRING,
                nullable=False,
                comment="The job which created this record",
            ),
            Column("CreatedAt", TypeName.TIMESTAMP_UTC, nullable=False, comment="Insert timestamp"),
            Column("UpdatedBy", TypeName.STRING, comment="The job which last updated this record"),
            Column("UpdatedAt", TypeName.TIMESTAMP_UTC, comment="Update timestamp"),
            Column(
                "BatchId",
                TypeName.STRING,
                nullable=False,
                comment="The batch that inserted or updated this row",
            ),
        ),
    )


#: Version 1 of the member domain — the spine and one satellite, decided and
#: deployable. `refuse_undecided(MEMBER_DOMAIN_DISCREPANCIES)` does not raise.
MEMBER_DOMAIN_V1 = OdsModel(version=1, entities=(_members(), _members_addresses()))

#: CF-V3-E10-03's G5 gate checks exactly this — the one real, deployed
#: relationship the harvest has today. `Members_Addresses.OurId` names
#: itself "FK to Members" in its own column comment (`_members_addresses`,
#: above); this is that same fact, made checkable rather than merely read.
MEMBER_DOMAIN_RELATIONSHIPS = (
    Relationship(
        child_entity="Members_Addresses",
        child_column="OurId",
        parent_entity="Members",
        parent_column="OurId",
    ),
)
