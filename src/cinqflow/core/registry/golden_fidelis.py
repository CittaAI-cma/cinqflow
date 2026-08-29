"""The Fidelis downstate roster — the one scenario every Wave-0 demo agrees on.

    "drop the roster, watch it flow with 5 quarantined rows and a balanced
     ledger; drop it again and watch it refused; kill it at Silver Raw and
     restart cleanly"
    — memory/06-product/00-epics-and-stories.md, Wave-0 exit

This is the SAME feed, contract and rule `tests/pipeline/test_golden_roster.py`
proves byte-exact — pulled into `src/` so `cinqflow ingest` (a real run against
the real Postgres plane) and the test suite (a run inside a rolled-back
transaction) exercise identically-shaped metadata rather than two golden
scenarios drifting apart. The values themselves are inert data, not logic; what
matters is that both callers read them from one place.
"""

from __future__ import annotations

from cinqflow.core.compiler import compile_feed
from cinqflow.core.compiler.plan import LogicalPlan
from cinqflow.core.registry.contract import (
    ContractColumn,
    DqRule,
    SchemaContract,
    Severity,
    not_null,
)
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName

FEED_VERSION = 1

FEED = FeedRecord(
    feed_id="fidelis-downstate-roster",
    domain="enrollments",
    source_system="fidelis",
    file_format="csv",
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.csv",
    schedule_cron="0 3 1 * *",
    sample_filename="_CINQDOWNSTATE_Member_Roster_202608.csv",
    min_size_bytes=100,
    max_size_bytes=30_000_000,
)

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn(
            "source_member_id", TypeName.STRING, nullable=False, source_name="MemberID", is_phi=True
        ),
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("last_name", TypeName.STRING, source_name="Last_Name", is_phi=True),
        ContractColumn("date_of_birth", TypeName.DATE, source_name="DOB", is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING, source_name="LOB"),
    ),
    key_columns=("source_member_id",),
)

DQ_002: DqRule = not_null(
    "DQ-002",
    "first_name",
    name="Member First Name Not Null",
    severity=Severity.HIGH,
    description="Required for member outreach, care coordination and CMS submissions",
    glossary_id="BG-002",
)

RULES: tuple[DqRule, ...] = (DQ_002,)

PLAN: LogicalPlan = compile_feed(
    feed=FEED, feed_version=FEED_VERSION, contract=CONTRACT, rules=RULES
)

#: `MemberID,First_Name,Last_Name,DOB,LOB` — the header CONTRACT's source_names expect.
COLUMNS: tuple[str, ...] = ("MemberID", "First_Name", "Last_Name", "DOB", "LOB")


def roster_csv(*, rows: int = 200, null_names: int = 5, bad_dates: int = 0) -> bytes:
    """A synthetic roster from the REAL layout. Zero member-derived values.

    Deterministic and header-stable, so `null_names` rows land in quarantine
    under DQ-002 every time — that determinism is what makes "5 quarantined
    rows, every run" a claim rather than a hope. `bad_dates` seeds an
    out-of-range DOB (1753-01-01, the SQL Server epoch floor — a real incident
    class) on the rows immediately after the null-first-name rows.
    """
    lines = [",".join(COLUMNS)]
    for index in range(1, rows + 1):
        first = "" if index <= null_names else f"FIRST{index:05d}"
        dob = "17530101" if null_names < index <= null_names + bad_dates else "19900101"
        lines.append(f"MBR{index:06d},{first},LAST{index:05d},{dob},MEDICAID")
    return ("\n".join(lines) + "\n").encode()


def filename(business_date: str) -> str:
    """business_date: 'YYYY-MM-DD' -> `_CINQDOWNSTATE_Member_Roster_YYYYMM.csv`"""
    yyyymm = business_date.replace("-", "")[:6]
    return f"_CINQDOWNSTATE_Member_Roster_{yyyymm}.csv"


def landing_key(business_date: str) -> str:
    return (
        f"{FEED.landing_path}/incoming/{business_date}/{filename(business_date)}"
    )
