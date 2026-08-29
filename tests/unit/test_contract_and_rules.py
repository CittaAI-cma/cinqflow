"""The schema contract, drift classification, casting and DQ rules — G2's work."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cinqflow.core.registry.contract import (
    CastFailureError,
    ContractColumn,
    DqRule,
    DriftKind,
    SchemaContract,
    Severity,
    cast_value,
    compare_to_contract,
    not_null,
)
from cinqflow.core.schema_spec import TypeName

pytestmark = pytest.mark.unit

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn(
            "source_member_id", TypeName.STRING, nullable=False, source_name="MemberID", is_phi=True
        ),
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("date_of_birth", TypeName.DATE, source_name="DOB", is_phi=True),
        ContractColumn("effective_date", TypeName.DATE, source_name="EffDate"),
    ),
)


# ── severity decides fate, which is why a rule is not a boolean ──────────────
@pytest.mark.parametrize(
    ("severity", "quarantines"),
    [
        (Severity.CRITICAL, True),
        (Severity.HIGH, True),
        (Severity.MEDIUM, False),
        (Severity.LOW, False),
    ],
)
def test_severity_decides_whether_a_failing_row_is_quarantined(
    severity: Severity, quarantines: bool
) -> None:
    """ "which rules run, at which severity" — the difference between "this
    record is unusable" and "this record is imperfect".

    Quarantining everything would empty a roster over a missing middle name.
    """
    assert severity.quarantines is quarantines


# ── drift, classified by MEANING ─────────────────────────────────────────────
def test_a_reordered_file_is_harmless_because_columns_are_read_by_name() -> None:
    """Column order genuinely changes between payer deliveries. A
    position-based reader would fail a perfectly good file."""
    findings = compare_to_contract(("DOB", "MemberID", "First_Name", "EffDate"), CONTRACT)
    assert [f.kind for f in findings] == [DriftKind.REORDERED]
    assert not any(f.blocks_batch for f in findings)


def test_an_identical_file_produces_no_findings() -> None:
    assert compare_to_contract(CONTRACT.source_columns, CONTRACT) == ()


def test_an_added_column_is_ignored_not_dropped() -> None:
    """A payer adding a field is not an incident. Recording it as ADDED means
    someone can decide to contract it later, rather than never knowing."""
    (finding,) = compare_to_contract((*CONTRACT.source_columns, "NewPayerField"), CONTRACT)
    assert finding.kind is DriftKind.ADDED
    assert finding.blocks_batch is False
    assert "ignored, not dropped" in finding.detail


def test_a_missing_required_column_blocks_the_batch() -> None:
    """The mapping cannot run without it, so the batch stops HERE rather than
    loading a roster with no member ids."""
    findings = compare_to_contract(("First_Name", "DOB", "EffDate"), CONTRACT)
    (removed,) = [f for f in findings if f.kind is DriftKind.REMOVED]
    assert removed.column == "MemberID"
    assert removed.blocks_batch is True
    assert "not nullable" in removed.detail


def test_a_missing_optional_column_does_not_block() -> None:
    """Failing on this would make every payer's optional-field change an
    incident, and operations would learn to ignore the alert."""
    findings = compare_to_contract(("MemberID", "DOB", "EffDate"), CONTRACT)
    (removed,) = [f for f in findings if f.kind is DriftKind.REMOVED]
    assert removed.column == "First_Name"
    assert removed.blocks_batch is False


# ── casting ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["19900101", "1990-01-01", "1/1/1990", "01/01/1990"])
def test_the_date_normalizer_treats_every_format_as_the_same_date(raw: str) -> None:
    """THE canonical unit test of the testing pyramid:

    "the date normalizer treats 19900101 and 01/01/1990 identically"

    It lives in core/ precisely so it is identical on both planes — a
    normalizer inside a compute adapter would be two normalizers.
    """
    column = ContractColumn("date_of_birth", TypeName.DATE)
    assert cast_value(raw, column) == date(1990, 1, 1)


def test_an_impossible_legacy_date_is_attributed_not_loaded() -> None:
    """Incident #8: service months of '1000-01' and '1753-01' in the legacy
    estate. A date that PARSES is not the same as a date that is POSSIBLE."""
    column = ContractColumn("service_date", TypeName.DATE)
    with pytest.raises(CastFailureError, match="plausible range"):
        cast_value("17530101", column)


def test_a_cast_failure_names_the_column_and_the_value() -> None:
    """The drop ledger needs both. "a cast failed" is not an attributed drop."""
    with pytest.raises(CastFailureError) as caught:
        cast_value("not-a-date", ContractColumn("date_of_birth", TypeName.DATE))
    assert "date_of_birth" in str(caught.value)
    assert "not-a-date" in str(caught.value)


def test_an_empty_value_is_null_when_the_column_allows_it() -> None:
    assert cast_value("   ", ContractColumn("first_name", TypeName.STRING)) is None


def test_an_empty_value_in_a_required_column_is_a_cast_failure() -> None:
    with pytest.raises(CastFailureError, match="not nullable"):
        cast_value("", ContractColumn("source_member_id", TypeName.STRING, nullable=False))


def test_decimals_keep_their_exactness() -> None:
    """ "pandas silently widens an integer column containing nulls to float" is
    why Decimal is used here: money must not go through binary floating point."""
    value = cast_value("-48000.00", ContractColumn("paid", TypeName.DECIMAL, precision=18, scale=2))
    assert value == Decimal("-48000.00")
    assert isinstance(value, Decimal)


@pytest.mark.parametrize(("raw", "expected"), [("Y", True), ("no", False), ("1", True)])
def test_the_estate_s_boolean_spellings_all_cast(raw: str, expected: bool) -> None:
    assert cast_value(raw, ContractColumn("is_active", TypeName.BOOL)) is expected


# ── DQ rules ─────────────────────────────────────────────────────────────────
def test_dq_002_is_the_canonical_quarantine_reason() -> None:
    """DQ-002 · Member First Name Not Null · Completeness / Mandatory Field ·
    Enrollment · Members.First_Name · Severity HIGH · Glossary BG-002.

    Taken verbatim from the 110-rule golden set, because a rule invented for a
    test measures nothing.
    """
    rule = not_null(
        "DQ-002",
        "first_name",
        name="Member First Name Not Null",
        severity=Severity.HIGH,
        description="Required for member outreach, care coordination, and CMS submissions",
        glossary_id="BG-002",
    )
    assert rule.passes({"first_name": "ARUN"}) is True
    assert rule.passes({"first_name": ""}) is False
    assert rule.passes({"first_name": "   "}) is False
    assert rule.passes({}) is False
    assert rule.severity.quarantines is True
    assert str(rule.citation) == "rule:DQ-002"


def test_a_rule_names_the_columns_it_reads() -> None:
    """The quarantine summary reports column NAMES and never row contents, so a
    rule that did not declare its columns could not be reported safely."""
    rule = not_null("DQ-002", "first_name", name="n", severity=Severity.HIGH, description="d")
    assert rule.columns == ("first_name",)


def test_a_rule_carries_its_plain_english_description() -> None:
    """This is the shape CF-V1-E7-01's NL -> rule agent must produce. Getting
    it right now means the agent proposes into something reviewable."""
    rule = DqRule(
        rule_id="DQ-014",
        name="Effective Date Before End Date",
        description="A coverage segment cannot end before it begins",
        severity=Severity.CRITICAL,
        columns=("effective_date", "end_date"),
        predicate=lambda row: (
            row.get("end_date") is None or row["effective_date"] <= row["end_date"]  # type: ignore[operator]
        ),
    )
    assert rule.passes({"effective_date": date(2026, 1, 1), "end_date": date(2026, 12, 31)})
    assert not rule.passes({"effective_date": date(2026, 12, 31), "end_date": date(2026, 1, 1)})
