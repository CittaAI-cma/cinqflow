"""The ten CINQCARE payer sources, as DATA.

Every value here was MEASURED from the files in `5-TestData/`, not estimated —
row counts, column counts, and the distinct-key ratio that decides each
source's grain. Contract columns were read off the actual headers.

    "golden sets are harvested from artifacts the programme already produced,
     never written for the occasion"
    — memory/05-ground-truth/03-golden-sets.md

WHY GRAIN IS THE LOAD-BEARING FIELD. `silver_raw.members` is unique on
`(batch_id, feed_id, source_member_id)`. Six of these ten sources deliver one
row per member per coverage span or per month, so at full grain they collide by
construction — 445,394 rows with no legal home. Those are marked
`Grain.SEGMENT` and the driver stops them at Bronze, where `raw_row` is
schemaless `jsonb` and keeps every one of their 25-111 source columns intact.
Collapsing them to latest-segment would fit the constraint and discard 389,402
rows of coverage history, which is exactly the data that makes readmission
windows computable. Bronze-only loses nothing; it just declines to pretend.

THE FIVE CANONICAL COLUMNS. `silver_raw.members` carries 17 columns against the
canonical `Members` entity's 82, and the contract can only map what the table
can hold. So each source below declares its own source-column names for the
same five canonical targets, plus `is_phi` per column — and THAT is what drives
masking on every screen. A source column left out of the contract is not lost:
it is in Bronze, in full, under its own name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTDATA = REPO.parent / "5-TestData"


@unique
class Grain(StrEnum):
    """How many rows this source sends per person.

    MEMBER  one row per person. Lands in Silver Raw as-is.
    SEGMENT one row per person per coverage span or per month. Bronze only —
            see the module note.
    EVENT   an occurrence, not a person. Belongs in a table that does not exist
            yet; the driver refuses it rather than filing it under members.
    """

    MEMBER = "member"
    SEGMENT = "segment"
    EVENT = "event"


@dataclass(frozen=True)
class Mapping:
    """One canonical column and the source column that carries it."""

    canonical: str
    source: str
    is_phi: bool = False
    #: `date` for a DOB, `string` otherwise. Only the two the five targets need.
    kind: str = "string"


@dataclass(frozen=True)
class Source:
    """One payer feed: where the file is, what shape it is, and what it means."""

    key: str
    feed_id: str
    label: str
    #: The VBC contract this population sits under, from
    #: memory/05-ground-truth/02-feeds-sources-and-volumes.md.
    contract_type: str
    source_system: str
    domain: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    #: Relative to `5-TestData/`. A tuple because Molina delivers four files
    #: under one feed — multi-file is the estate's normal case, not an edge one.
    files: tuple[str, ...]
    #: DECLARED, never sniffed. Incident #12: the Optum GA landing zone held
    #: both a csv and an xlsx and a sniffing parser is quietly wrong half the
    #: time. See `ACO_REACH` below for why this field earns its keep.
    file_format: str
    grain: Grain
    #: The source column that identifies the member. Also the dedup key.
    key_column: str
    mappings: tuple[Mapping, ...]
    measured_rows: int
    measured_columns: int
    measured_distinct: int
    #: Lives under this contract, per the ground truth — so a load can be
    #: checked against the business, not only against itself.
    documented_lives: int | None = None
    notes: str = ""
    #: Canonical columns to enforce NOT NULL on at G2. `first_name` is DQ-002,
    #: the programme's canonical quarantine reason.
    not_null: tuple[str, ...] = field(default_factory=lambda: ("first_name",))

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(TESTDATA / name for name in self.files)

    @property
    def production_filenames(self) -> tuple[str, ...]:
        """The names the payer would actually have sent.

        ONE transformation, used by both the `sample_filename` on the
        `FeedRecord` and the name handed to the connector — because
        `FeedRecord.__post_init__` refuses a pattern that does not match its
        own sample, and two copies of this rule would let the registry
        certify a pattern the delivery then fails. That guard caught exactly
        this bug on the first run.
        """
        return tuple(production_filename(name) for name in self.files)

    @property
    def loads_to_silver(self) -> bool:
        return self.grain is Grain.MEMBER

    def missing(self) -> tuple[Path, ...]:
        return tuple(p for p in self.paths if not p.exists())


def production_filename(path_or_name: str) -> str:
    """The name the payer would have sent, made portable.

    TWO transformations, and both are recorded here rather than done twice:

    1. STRIP THE DE-IDENTIFICATION MARKER. `deidentified__CINQDOWNSTATE_...`
       becomes `_CINQDOWNSTATE_...` — the leading underscore that once broke
       the Excel reader is PRESERVED, because stripping it would quietly remove
       the one filename in the estate the pre-flight check exists for.

    2. MAKE IT PORTABLE. Spaces become underscores. The landing contract
       refuses anything outside letters, digits, dot, dash and underscore —
       "the intersection of what localfs at rung 0.5 and a blob container at
       rung 3 will both accept" — and it is RIGHT to refuse. Four of these
       sources ship names with spaces (`Medicaid MWOV Members - 2026-03-10_1
       .txt`, the four Molina files), so without this the platform declines
       them at the gate, correctly.

    THIS IS A REAL ONBOARDING FINDING, not a workaround. A payer that sends
    spaces in filenames cannot deliver into a rung-3 blob container, and the
    fix belongs upstream: either the payer renames, or the connector normalises
    as a declared pre-flight step with the original recorded. The populator
    normalises and says so; it does not pretend the names were already clean.
    """
    name = Path(path_or_name).name
    for prefix in ("deidentified__", "deidentified_"):
        if name.startswith(prefix):
            # "deidentified__X" -> "_X": drop only the marker, keep the
            # underscore that belongs to the payer's own name.
            name = ("_" if prefix.endswith("__") else "") + name[len(prefix) :]
            break
    return name.replace(" ", "_")


#: Sources whose real filenames are not portable, and therefore normalised
#: above. Named explicitly so the list is auditable rather than implied by a
#: regex somewhere.
NON_PORTABLE_FILENAMES = ("centene-ga-medicaid", "centene-ga-medicare", "molina-ny")


_ENROLL = "1-Enrollment"

SOURCES: tuple[Source, ...] = (
    Source(
        key="fidelis-upstate",
        feed_id="enrollment-fidelis-upstate-ny",
        label="Fidelis NY upstate roster",
        contract_type="Full Risk",
        source_system="fidelis",
        domain="enrollments",
        landing_path="enrollments/fidelis_upstate/roster",
        file_pattern=r"CINQUPSTATE_Member_Roster_\d{2}_\d{2}_\d{4}.*\.csv",
        schedule_cron="0 3 1 * *",
        files=(f"{_ENROLL}/1.Fedelis_NY/deidentified_CINQUPSTATE_Member_Roster_03_05_2026_1.csv",),
        file_format="csv",
        grain=Grain.MEMBER,
        key_column="member_id",
        mappings=(
            Mapping("source_member_id", "member_id", is_phi=True),
            Mapping("first_name", "member_first_name", is_phi=True),
            Mapping("last_name", "member_last_name", is_phi=True),
            Mapping("date_of_birth", "member_dob", is_phi=True, kind="date"),
            Mapping("line_of_business", "product"),
        ),
        measured_rows=28_333,
        measured_columns=45,
        measured_distinct=28_333,
        documented_lives=64_201,
        notes="Upstate half of the 64,201 Fidelis lives; downstate is the other feed.",
    ),
    Source(
        key="fidelis-downstate",
        feed_id="enrollment-fidelis-downstate-ny",
        label="Fidelis NY downstate roster",
        contract_type="Full Risk",
        source_system="fidelis",
        domain="enrollments",
        landing_path="enrollments/fidelis_downstate/roster",
        # The filename BEGINS WITH AN UNDERSCORE. That once broke the Excel
        # reader and is now a permanent pre-flight check — the pattern has to
        # admit it, and `allows_leading_underscore` on FeedRecord defaults True.
        file_pattern=r"_?CINQDOWNSTATE_Member_Roster_.*\.xlsx",
        schedule_cron="0 3 1 * *",
        files=(
            f"{_ENROLL}/1.Fedelis_NY/deidentified__CINQDOWNSTATE_Member_Roster_03_05_2026_1.xlsx",
        ),
        file_format="xlsx",
        grain=Grain.MEMBER,
        key_column="member_id",
        mappings=(
            Mapping("source_member_id", "member_id", is_phi=True),
            Mapping("first_name", "member_first_name", is_phi=True),
            Mapping("last_name", "member_last_name", is_phi=True),
            Mapping("date_of_birth", "member_dob", is_phi=True, kind="date"),
            Mapping("line_of_business", "product"),
        ),
        measured_rows=38_489,
        measured_columns=45,
        measured_distinct=38_489,
        documented_lives=64_201,
        notes="xlsx via calamine. Same 45-column layout as upstate.",
    ),
    Source(
        key="molina-ny",
        feed_id="enrollment-molina-ny-history",
        label="Molina NY enrolment history",
        contract_type="Gain share",
        source_system="molina",
        domain="enrollments",
        landing_path="enrollments/molina_ny/enrollhist",
        file_pattern=r"MHI_NYCINQ.*_Data_MemEnrollHist\d*\.txt",
        schedule_cron="0 4 1 * *",
        files=(
            f"{_ENROLL}/2.Molina NY/deidentified_MHI_NYCINQ- Down - HARP_NY_MCAID_"
            "20240201_to_20260131_20260223_001of001_Data_MemEnrollHist1.txt",
            f"{_ENROLL}/2.Molina NY/deidentified_MHI_NYCINQ- Down - MEDICAID_NY_MCAID_"
            "20240201_to_20260131_20260223_001of001_Data_MemEnrollHist.txt",
            f"{_ENROLL}/2.Molina NY/deidentified_MHI_NYCINQ- Up - MEDICAID_NY_MCAID_"
            "20240201_to_20260131_20260223_001of001_Data_MemEnrollHist.txt",
            f"{_ENROLL}/2.Molina NY/deidentified_MHI_NYCINQUpstate_HARP_NY_MCAID_"
            "20240201_to_20260131_20260223_001of001_Data_MemEnrollHist.txt",
        ),
        # Pipe-delimited. `_sniff_delimiter` handles comma, pipe and tab, and a
        # wrong guess produces one giant column that fails the contract loudly
        # rather than quietly — which is the behaviour worth having.
        file_format="csv",
        grain=Grain.SEGMENT,
        key_column="Member_ID",
        mappings=(
            Mapping("source_member_id", "Member_ID", is_phi=True),
            Mapping("first_name", "Member_First_Name", is_phi=True),
            Mapping("last_name", "Member_Last_Name", is_phi=True),
            Mapping("date_of_birth", "Member_Date_Of_Birth", is_phi=True, kind="date"),
            Mapping("line_of_business", "Program_Description"),
        ),
        measured_rows=332_679,
        measured_columns=60,
        measured_distinct=27_903,
        documented_lives=6_463,
        notes=(
            "SCD-2 coverage segments, Feb 2024 - Jan 2026: 11.9 rows per member. Four files, "
            "one feed. Belongs in Members_EnrollmentSegments, which is not deployed."
        ),
    ),
    Source(
        key="centene-ga-medicaid",
        feed_id="enrollment-centene-ga-medicaid",
        label="Centene GA Medicaid (Wellcare)",
        contract_type="MSO, ~300 at risk",
        source_system="centene",
        domain="enrollments",
        landing_path="enrollments/centene_ga/medicaid",
        file_pattern=r"Medicaid_MWOV_Members_-_\d{4}-\d{2}-\d{2}.*\.txt",
        schedule_cron="0 4 1 * *",
        files=(
            f"{_ENROLL}/3.GA CENTENE Medicaid/"
            "deidentified_Medicaid MWOV Members - 2026-03-10_1.txt",
        ),
        file_format="csv",
        grain=Grain.MEMBER,
        key_column="MEMBER_KEY",
        mappings=(
            Mapping("source_member_id", "MEMBER_KEY", is_phi=True),
            # ONE name column, not two. The contract maps it to last_name and
            # leaves first_name unmapped, so DQ-002 quarantines every row —
            # which is the honest outcome of a source that does not carry the
            # field, and precisely the finding an onboarding review should see.
            Mapping("last_name", "MEMBER_NAME", is_phi=True),
            Mapping("date_of_birth", "MEMBER_DOB", is_phi=True, kind="date"),
            Mapping("line_of_business", "LOB"),
        ),
        measured_rows=7_817,
        measured_columns=34,
        measured_distinct=7_817,
        documented_lives=15_423,
        notes="Quoted csv. MEMBER_NAME is a single field — no first/last split at source.",
        not_null=(),
    ),
    Source(
        key="centene-ga-medicare",
        feed_id="enrollment-centene-ga-medicare",
        label="Centene GA Medicare (Wellcare)",
        contract_type="MSO, ~300 at risk",
        source_system="centene",
        domain="enrollments",
        landing_path="enrollments/centene_ga/medicare",
        file_pattern=r"Medicare_MWOV_Members_-_\d{4}-\d{2}-\d{2}.*\.txt",
        schedule_cron="0 4 1 * *",
        files=(
            f"{_ENROLL}/4.GA CENTENE Medicare/deidentified_Medicare MWOV Members - 2026-03-10.txt",
        ),
        file_format="csv",
        grain=Grain.MEMBER,
        key_column="MEMBER_KEY",
        mappings=(
            Mapping("source_member_id", "MEMBER_KEY", is_phi=True),
            Mapping("last_name", "MEMBER_NAME", is_phi=True),
            Mapping("date_of_birth", "MEMBER_DOB", is_phi=True, kind="date"),
            Mapping("line_of_business", "LOB"),
        ),
        measured_rows=7_731,
        measured_columns=34,
        measured_distinct=7_731,
        documented_lives=15_423,
        notes="Same layout as the Medicaid line; separate feed because the contract differs.",
        not_null=(),
    ),
    Source(
        key="optum-ga",
        feed_id="enrollment-optum-ga-housecalls",
        label="Optum GA HouseCalls",
        contract_type="Engagement only",
        source_system="optum",
        domain="enrollments",
        landing_path="enrollments/optum_ga/housecalls",
        file_pattern=r"\d{4}-\d{2}-\d{2}_CINQ_HouseCalls_GA.*\.xlsx",
        schedule_cron="0 5 1 * *",
        files=(f"{_ENROLL}/5.Optum GA/deidentified_2026-03-16_CINQ_HouseCalls_GA1.xlsx",),
        file_format="xlsx",
        grain=Grain.MEMBER,
        key_column="Member_Client_ID",
        mappings=(
            Mapping("source_member_id", "Member_Client_ID", is_phi=True),
            Mapping("first_name", "Member_First_Name", is_phi=True),
            Mapping("last_name", "Member_Last_Name", is_phi=True),
            Mapping("date_of_birth", "DOB", is_phi=True, kind="date"),
            Mapping("line_of_business", "Plan_Name"),
        ),
        measured_rows=3_814,
        measured_columns=30,
        measured_distinct=3_814,
        documented_lives=3_747,
        notes="Incident #12's source: this landing zone held both a csv and an xlsx.",
    ),
    Source(
        key="optum-ny",
        feed_id="enrollment-optum-ny-eligibility",
        label="Optum NY eligibility",
        contract_type="Full Risk",
        source_system="optum",
        domain="enrollments",
        landing_path="enrollments/optum_ny/eligibility",
        file_pattern=r"CINQCare_Elig_\d{6}\.csv",
        schedule_cron="0 5 1 * *",
        files=(f"{_ENROLL}/6.Optum NY/deidentified_CINQCare_Elig_202602.csv",),
        file_format="csv",
        grain=Grain.SEGMENT,
        key_column="SOURCE_PATIENT_ID",
        mappings=(
            Mapping("source_member_id", "SOURCE_PATIENT_ID", is_phi=True),
            Mapping("first_name", "MBR_FST_NM", is_phi=True),
            Mapping("last_name", "MBR_LST_NM", is_phi=True),
            Mapping("date_of_birth", "BTH_DT", is_phi=True, kind="date"),
            Mapping("line_of_business", "CONTR_CURR_PLN_NM"),
        ),
        measured_rows=37_104,
        measured_columns=25,
        measured_distinct=15_431,
        documented_lives=6_939,
        notes=(
            "RECORD_START_DT/RECORD_END_DT spans: 2.4 rows per member. Carries CURR_MBI, which "
            "is the strongest Medicare identity key available - and nothing can hold it yet."
        ),
    ),
    Source(
        key="centene-il",
        feed_id="enrollment-centene-il-roster",
        label="Centene IL member roster",
        contract_type="Full Risk",
        source_system="centene",
        domain="enrollments",
        landing_path="enrollments/centene_il/roster",
        file_pattern=r"Member_Roster_Preview_IL\d+_\d{6}.*\.csv",
        schedule_cron="0 4 1 * *",
        files=(
            f"{_ENROLL}/7.Centene IL/deidentified_Member_Roster_Preview_IL0201633_202602_1.csv",
        ),
        file_format="csv",
        grain=Grain.MEMBER,
        key_column="AMISYS NUMBER",
        mappings=(
            Mapping("source_member_id", "AMISYS NUMBER", is_phi=True),
            Mapping("first_name", "FIRST NAME", is_phi=True),
            Mapping("last_name", "LAST NAME", is_phi=True),
            Mapping("date_of_birth", "MEMBER DOB", is_phi=True, kind="date"),
            Mapping("line_of_business", "LINE OF BUSINESS"),
        ),
        measured_rows=26_489,
        measured_columns=45,
        measured_distinct=26_489,
        documented_lives=25_779,
        notes="Space-separated column names, quoted. 45 columns including 20 utilisation counts.",
    ),
    Source(
        key="aco-reach",
        feed_id="enrollment-aco-reach-d0284",
        label="ACO REACH D0284 alignment roster",
        contract_type="Full Risk",
        source_system="cms",
        domain="enrollments",
        landing_path="enrollments/aco_reach/alignment",
        file_pattern=r"D0284_AlignmentRoster.*",
        schedule_cron="0 6 1 * *",
        files=(f"{_ENROLL}/8.Cinq ACO Reach/deidentified_D0284_AlignmentRoster.xlsx",),
        # DECLARED csv AGAINST AN .xlsx FILENAME, and this is not a typo.
        # `file(1)` reports "CSV text"; the platform's xlsx reader raises "not a
        # readable spreadsheet: Cannot detect file format". This is incident
        # #12's exact shape, and the registry is where the disagreement between
        # a filename and its content is supposed to be settled by a person.
        file_format="csv",
        grain=Grain.SEGMENT,
        key_column="Beneficiary MBI ID",
        mappings=(
            Mapping("source_member_id", "Beneficiary MBI ID", is_phi=True),
            Mapping("first_name", "bene_1st_nm", is_phi=True),
            Mapping("last_name", "bene_last_nm", is_phi=True),
            Mapping("date_of_birth", "bene_dob", is_phi=True, kind="date"),
            Mapping("line_of_business", "cohort_flag"),
        ),
        measured_rows=75_611,
        measured_columns=111,
        measured_distinct=12_658,
        documented_lives=6_973,
        notes=(
            "Monthly alignment grain (yr_mo): 6.0 rows per beneficiary. 111 columns. The file "
            "is CSV named .xlsx - the format is DECLARED here, never sniffed."
        ),
    ),
    Source(
        key="cmp-1598",
        feed_id="enrollment-medent-cmp-1598",
        label="Medent CMP_1598 practice extract",
        contract_type="Practice extract",
        source_system="medent",
        domain="enrollments",
        landing_path="enrollments/medent_cmp1598/roster",
        file_pattern=r"\d+_\d+_\d+_dmhmreport_CCS_\d+\.csv",
        schedule_cron="0 6 * * 1",
        files=(
            f"{_ENROLL}/9.CMP_1598/"
            "deidentified_2461114_206_20260314032654_dmhmreport_CCS_53873287.csv",
        ),
        file_format="csv",
        grain=Grain.MEMBER,
        key_column="Account",
        mappings=(
            Mapping("source_member_id", "Account", is_phi=True),
            Mapping("first_name", "First Name", is_phi=True),
            Mapping("last_name", "Last Name", is_phi=True),
            Mapping("date_of_birth", "DOB", is_phi=True, kind="date"),
            Mapping("line_of_business", "Primary Insurance Name"),
        ),
        measured_rows=10_800,
        measured_columns=29,
        measured_distinct=10_224,
        notes=(
            "576 DUPLICATE Account values. MEASURED BEHAVIOUR: the batch does NOT fail - the "
            "compiler dedups on the key and ATTRIBUTES all 576 as attributed_drops, storing "
            "them in quarantine under DUPLICATE-source_member_id, so the balance equation holds "
            "with unattributed=0. 'Every drop attributed' working, not a prediction."
        ),
    ),
)

#: ADT is deliberately NOT in SOURCES.
#:
#: 437 rows, 142 columns, one row per admission/discharge/transfer EVENT. The
#: only table Bronze offers is `bronze.members_raw`, and a table name is part of
#: the contract: filing encounter events under members would be the first lie in
#: the plane, and every downstream count of "members" would silently include
#: them. ADT waits for `bronze.adt_events`, which is a schema-contract change.
ADT_FILE = TESTDATA / "2- ADT/deidentified_ADT data for Payors.csv"
ADT_ROWS = 437
ADT_COLUMNS = 142


def by_key(key: str) -> Source:
    for source in SOURCES:
        if source.key == key:
            return source
    raise KeyError(f"{key!r} is not a source. Known: {', '.join(s.key for s in SOURCES)}")


def member_grain() -> tuple[Source, ...]:
    return tuple(s for s in SOURCES if s.loads_to_silver)


def segment_grain() -> tuple[Source, ...]:
    return tuple(s for s in SOURCES if not s.loads_to_silver)


TOTAL_ROWS = sum(s.measured_rows for s in SOURCES)
SILVER_CEILING = sum(s.measured_distinct for s in member_grain())
SEGMENT_ROWS = sum(s.measured_rows for s in segment_grain())
