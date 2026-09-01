"""CF-V3-E8-05 — the canonical mapping: `silver_raw.members` to
`MEMBER_DOMAIN_V1`'s `Members`.

    "Apply approved canonical mappings and identity crosswalks ..."
    — CF-V3-E8-05

NO NEW GOVERNED OBJECT TYPE. A Silver-Raw-to-Silver-ODS mapping is, at the
row-transform level, exactly the same question a Landing-to-Silver-Raw
mapping already answers: which source columns populate which target field,
with what transform. `core.mapping.FeedMapping`/`MappingLine`/`apply_to`
already say that, are already wired into production execution
(`core.compiler.execute._cast_and_map`), and already travel the same
Draft->Published lifecycle every mapping does, routed to the Data Steward
(plate 14). Reusing the type is the "plug and play" the platform asks for;
inventing a second mapping object for the same shape would not be.

The one thing that differs from a feed mapping is what `feed_id` MEANS: a
feed mapping is scoped to one payer's file; this one is scoped to the
CANONICAL layer, shared by every feed that lands into `silver_raw.members` —
`silver_raw` already normalized column names and types across payers, which
is the entire point of that layer. `feed_id="canonical:members"` is a
convention, not a real feed, the same way `object_id="silver_ods"` names the
one ODS model rather than a real feed (`core.registry.ods_model.
as_governed`).

WHAT IS HONESTLY UNMAPPED, AND WHY. `silver_raw.members`
(`core.schema_spec.SILVER_RAW_SCHEMA`) is Wave 0's literal — eleven business
columns plus the audit six. The REAL harvested `Members` entity (32 columns,
`ods_model_member_domain.py`) is far richer, because it is the client's own
design, not a schema invented to match what Silver Raw happens to carry
today. Every column Silver Raw genuinely has is mapped; every column it does
not is marked UNMAPPED with a reason naming exactly that gap — the same
"partial harvest, stated rather than hidden" discipline
`ods_model_member_domain.py` uses for the eight satellites it has not
harvested yet. `line_of_business`/`effective_date`/`end_date` describe
COVERAGE, not the member — they belong to `Members_EnrollmentSegments`,
named in that module's docstring as not yet harvested, not to `Members`.

`OurId` AND `RecordHash` ARE MAPPED HERE FOR DOCUMENTATION, NOT EXECUTION.
Surrogate-key assignment (reuse a legacy id, mint a fresh one) and the
change-detection hash are both STATEFUL or computed-after-the-fact —
`core.ods_load.assign_surrogate_key`/`compute_record_hash` — and cannot be a
`Transform`, which is parameters only, never code. The lines below declare
WHERE each value originates so a reviewer sees the whole picture; the loader
(`workers.ods_load`) always computes their actual values itself, regardless
of what `apply_to` would return for them.
"""

from __future__ import annotations

from cinqflow.core.mapping import FeedMapping, MappingLine

#: The feed-independent, canonical-layer convention `core.impact`'s
#: `ReferenceSpec("feed_id", ObjectType.FEED)` does not resolve against any
#: real FEED object — deliberately: this mapping is platform-wide, the same
#: way `silver_ods` is platform-wide for the ODS model itself.
CANONICAL_MEMBERS_FEED_ID = "canonical:members"

_NOT_YET_HARVESTED = (
    "silver_raw.members (Wave 0's literal schema) does not carry this field yet — "
    "MEMBER_DOMAIN_V1 is the client's own richer design, harvested ahead of the source."
)
_COMPUTED_BY_THE_LOADER = (
    "computed by the loader, not sourced or authored — see core.ods_load and this "
    "module's own docstring."
)


def _direct(target_field: str, source_column: str, *, notes: str = "") -> MappingLine:
    return MappingLine(
        target_entity="Members",
        target_field=target_field,
        source_columns=(source_column,),
        notes=notes,
    )


def _platform_supplied(target_field: str) -> MappingLine:
    return MappingLine(target_entity="Members", target_field=target_field, platform_supplied=True)


def _unmapped(target_field: str, reason: str = _NOT_YET_HARVESTED) -> MappingLine:
    return MappingLine(target_entity="Members", target_field=target_field, unmapped_reason=reason)


#: Version 1 — proven against the two entities `MEMBER_DOMAIN_V1` deploys.
#: `Members_Addresses` has no canonical mapping yet: no source table carries
#: address data anywhere in the currently deployed Silver Raw layer (only
#: `members` exists), so authoring one would map from data that does not
#: exist rather than the client's own real feed.
MEMBER_MAPPING_V1 = FeedMapping(
    feed_id=CANONICAL_MEMBERS_FEED_ID,
    version=1,
    lines=(
        _direct(
            "OurId",
            "_internal_member_id",
            notes="Surrogate key: the legacy OurId when the crosswalk carries one, otherwise "
            "minted fresh by the loader — core.ods_load.assign_surrogate_key, never this line's "
            "own transform.",
        ),
        _direct("LinkId", "_verato_person_id"),
        _direct("FirstName", "first_name"),
        _direct("LastName", "last_name"),
        _unmapped("MiddleName"),
        _unmapped("Suffix"),
        _direct("DateOfBirth", "date_of_birth"),
        _direct("Gender", "gender"),
        _unmapped("Race"),
        _unmapped("Ethnicity"),
        _unmapped("Language"),
        _unmapped("Location"),
        _unmapped("CareManagementProgram"),
        _unmapped("LastContact"),
        _unmapped("DualStatusCode"),
        _unmapped("DeathDate"),
        _platform_supplied("RecordCreationDate"),
        _unmapped("SecureID"),
        _unmapped("DNC"),
        _unmapped("GuardianFirstName"),
        _unmapped("GuardianLastName"),
        _unmapped("GuardianPhone"),
        _unmapped("GuardianEmail"),
        _direct("IsActive", "is_active"),
        _unmapped("RecordHash", _COMPUTED_BY_THE_LOADER),
        _unmapped("SourceSystemIdType"),
        _platform_supplied("FeedName"),
        _direct("SourceSystem", "source_system"),
        _platform_supplied("CreatedBy"),
        _platform_supplied("CreatedAt"),
        _platform_supplied("UpdatedBy"),
        _platform_supplied("UpdatedAt"),
        _direct("BatchId", "batch_id"),
    ),
)
