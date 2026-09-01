"""CF-V0-E8-08 — the Payer Source Simulator.

    "I want a simulator that ROLE-PLAYS EVERY SOURCE WE CANNOT ACCESS —
     generating synthetic files from the real layouts, on registry schedules,
     through any delivery protocol, with the seeded failure library injectable
     on demand, so that integration is rehearsed continuously before any access
     exists, and connecting a real source becomes a profile change plus a
     conformance run, never new engine code."

    role: first_class_component
    constraint: synthetic values only — real layouts, never real members
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

This is a PRODUCT COMPONENT, not a test fixture (ADR-0011). The distinction
matters in two directions:

  • Every Wave 0-3 demo is simulator-driven end to end, with ZERO hand-placed
    files. A demo that needs a human to drop a file is a demo that hides the
    connector.
  • It climbs the data-complexity ladder alongside the platform: delimited ->
    xlsx -> multi-file header/line -> FHIR NDJSON -> HL7-derived JSON ->
    fixed-width. Wave 0 needs the first two.

REAL LAYOUTS, SYNTHETIC VALUES. The layouts come from artifacts we already hold
— mapping workbooks, the BCDA dictionary, CCLF layouts, ADT samples. The values
are generated. Development holds zero PHI by constraint (ADR-0016), and that is
a freedom rather than a limitation: we can test destructively, log everything
and share environments with no compliance exposure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum, unique


@unique
class Injection(StrEnum):
    """The seeded failure library, injectable on demand.

    injects: [late, truncated, drifted_schema, duplicate_month,
              underscore_filename, bad_encoding]

    Every one of these is a DOCUMENTED HISTORICAL INCIDENT or a documented
    failure class. "The platform is never allowed to re-learn an old lesson",
    and a lesson that cannot be re-injected is a lesson nobody is checking.
    """

    NONE = "happy"
    LATE = "late"  # arrives after the SLA window
    TRUNCATED = "truncated"  # a partial delivery
    DRIFTED_SCHEMA = "drifted_schema"  # a contracted column vanishes
    DUPLICATE_MONTH = "duplicate_month"  # incident #4: the Feb-2025 roster
    UNDERSCORE_FILENAME = "underscore_filename"  # incident #1: broke the Excel reader
    BAD_ENCODING = "bad_encoding"  # latin-1 bytes in a utf-8 feed
    DUPLICATE_MEMBER = "duplicate_member"  # the same member twice in one file


@dataclass(frozen=True)
class Layout:
    """A feed's real shape. Columns and how to fill them — never what to fill
    them WITH from a real member."""

    feed_id: str
    columns: tuple[str, ...]
    key_column: str
    landing_path: str
    filename_template: str
    file_format: str = "csv"


# The Fidelis downstate roster, from `Source Details.xlsx`:
#   _CINQDOWNSTATE_Member_Roster_*.xslx | enrollments/fidelis_downstate/ | Monthly | 5-30 MB
# Note the leading underscore. It is real, it is in production, and it is the
# filename that once broke the Excel reader (incident #1).
FIDELIS_DOWNSTATE_ROSTER = Layout(
    feed_id="fidelis-downstate-roster",
    columns=("MemberID", "First_Name", "Last_Name", "DOB", "Gender", "LOB", "EffDate", "EndDate"),
    key_column="MemberID",
    landing_path="enrollments/fidelis_downstate/roster",
    filename_template="_CINQDOWNSTATE_Member_Roster_{yyyymm}.csv",
)


@dataclass(frozen=True)
class Delivery:
    """One generated file, and where it belongs."""

    key: str
    content: bytes
    business_date: date
    injection: Injection = Injection.NONE
    arrives_at: datetime | None = None

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass
class PayerSimulator:
    """Generates deliveries from a layout. Deterministic, given a seed.

    Determinism is not a convenience here: the golden pipeline compares
    BYTE-EXACT expected outputs including the exact quarantine rows, so a
    generator that varied between runs would make the golden set unusable.
    """

    layout: Layout = FIDELIS_DOWNSTATE_ROSTER
    seed: int = 20260801
    rows: int = 200
    null_first_names: int = 5
    _random: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._random = random.Random(self.seed)

    def deliver(
        self,
        *,
        business_date: date,
        injection: Injection = Injection.NONE,
        rows: int | None = None,
    ) -> Delivery:
        """Produce one delivery, with an optional seeded failure."""
        self._random.seed(self.seed + business_date.toordinal())
        row_count = rows if rows is not None else self.rows

        columns = list(self.layout.columns)
        if injection is Injection.DRIFTED_SCHEMA:
            # A contracted column simply stops arriving. Structurally the file
            # is fine, which is why this needs drift detection rather than a
            # size or parse check.
            columns.remove("First_Name")

        records = [self._row(index, columns) for index in range(1, row_count + 1)]

        if injection is Injection.DUPLICATE_MEMBER:
            records.append(records[41])  # the same member, again, in one file

        text = ",".join(columns) + "\n" + "".join(",".join(r) + "\n" for r in records)

        if injection is Injection.TRUNCATED:
            # A partial delivery: the header and a handful of rows. It PARSES
            # perfectly, which is precisely why the size bound exists — a
            # roster at a tenth of its size quietly halves a member population.
            text = "\n".join(text.splitlines()[:4]) + "\n"

        if injection is Injection.BAD_ENCODING:
            # A real accented name, encoded latin-1 in a feed declared utf-8.
            # This is what actually arrives: a payer's export tool using the
            # platform default rather than the agreed encoding. The bytes are
            # valid latin-1 and INVALID utf-8, so the parser must reject with a
            # stated reason rather than decode with replacements — Bronze is
            # append-only, and a mojibaked member name there is permanent.
            text = text.replace("FIRST000042", "JOSÉ MARÍA", 1)
            content = text.encode("latin-1")
        else:
            content = text.encode("utf-8")

        filename = self.layout.filename_template.format(yyyymm=business_date.strftime("%Y%m"))
        if injection is Injection.UNDERSCORE_FILENAME:
            # Force a leading underscore onto a feed that has not declared one.
            filename = filename if filename.startswith("_") else f"_{filename}"

        arrives_at = datetime.combine(business_date, datetime.min.time(), tzinfo=UTC)
        if injection is Injection.LATE:
            # Past the arrival window. Nothing is wrong with the FILE — which
            # is why lateness is an SLA signal rather than a rejection.
            arrives_at += timedelta(days=3, hours=7)

        return Delivery(
            key=(f"{self.layout.landing_path}/incoming/{business_date.isoformat()}/{filename}"),
            content=content,
            business_date=business_date,
            injection=injection,
            arrives_at=arrives_at,
        )

    def deliver_duplicate(self, previous: Delivery) -> Delivery:
        """Incident #4: the same month's roster, delivered twice.

        Byte-identical content under a DIFFERENT NAME, because that is how a
        re-send actually arrives — and a name-based dedup would miss it.
        """
        return Delivery(
            key=previous.key.replace(".csv", "_RESEND.csv"),
            content=previous.content,
            business_date=previous.business_date,
            injection=Injection.DUPLICATE_MONTH,
            arrives_at=previous.arrives_at,
        )

    def _row(self, index: int, columns: list[str]) -> list[str]:
        """One synthetic member. Real layout, invented person.

        Names are generated from the index rather than from a name library, so
        the output is reproducible AND obviously synthetic — nobody can mistake
        FIRST000042 for a real member, which matters when a screenshot of a
        quarantine view ends up in a ticket.
        """
        values = {
            "MemberID": f"MBR{index:06d}",
            "First_Name": "" if index <= self.null_first_names else f"FIRST{index:06d}",
            "Last_Name": f"LAST{index:06d}",
            "DOB": self._birth_date(index),
            "Gender": self._random.choice(("M", "F", "U")),
            "LOB": self._random.choice(("MEDICAID", "MEDICARE", "DUAL")),
            "EffDate": "20260101",
            "EndDate": "",
        }
        return [values.get(column, "") for column in columns]

    def _birth_date(self, index: int) -> str:
        year = 1935 + (index % 70)
        month = 1 + (index % 12)
        day = 1 + (index % 28)
        return f"{year:04d}{month:02d}{day:02d}"
