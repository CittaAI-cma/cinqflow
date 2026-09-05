"""The profiler is deterministic arithmetic. These tests pin that down - the v1
facts, and the v2 facts (PR-5): role hints, null ratio, bounds, top values,
constant, sentinels, time coverage, and what PHI never carries."""

from __future__ import annotations

import itertools

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import ParseError, parse
from cinqflow.settings import Settings
from cinqflow.workflow.models import mask_facts
from tests.conftest import SAMPLES

MOLINA_TXT = (
    SAMPLES.parent
    / "2.Molina NY"
    / "deidentified_MHI_NYCINQ- Up - MEDICAID_NY_MCAID_20240201_to_20260131_20260223_001of001"
    "_Data_MemEnrollHist.txt"
)


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path)


@pytest.fixture
def molina_head_bytes() -> bytes:
    """The pipe-delimited Molina enrollment history: header plus the first 5,000
    rows. Hints are per-column patterns, so a bounded head is a fair golden
    input and keeps the test in seconds (the whole file is 174k rows)."""
    if not MOLINA_TXT.exists():
        pytest.skip(f"sample not present: {MOLINA_TXT}")
    with MOLINA_TXT.open("rb") as fh:
        return b"".join(itertools.islice(fh, 5001))


def _by_name(content: bytes, s: Settings):
    facts = profiler.profile(parse(content, "csv"), s)
    return facts, {c.name: c for c in facts.columns}


# ------------------------------------------------------------------ v1 facts


def test_profiles_the_actual_columns_and_rows(small_csv_bytes, s):
    facts = profiler.profile(parse(small_csv_bytes, "csv"), s)

    assert facts.row_count == 3
    assert [c.name for c in facts.columns] == [
        "member_id",
        "member_first_name",
        "member_dob",
        "member_sex",
        "product",
    ]
    by_name = {c.name: c for c in facts.columns}
    assert by_name["member_dob"].null_count == 1
    assert by_name["member_dob"].inferred_type == "date"
    assert by_name["product"].distinct_count == 2
    assert by_name["member_id"].distinct_count == 3


def test_profile_id_is_stable_for_identical_bytes(small_csv_bytes, s):
    first = profiler.profile(parse(small_csv_bytes, "csv"), s)
    second = profiler.profile(parse(small_csv_bytes, "csv"), s)
    assert profiler.profile_id(first) == profiler.profile_id(second)


def test_profile_id_changes_when_data_changes(small_csv_bytes, s):
    changed = small_csv_bytes.replace(b"DANIELLE", b"DANIELA")
    a = profiler.profile_id(profiler.profile(parse(small_csv_bytes, "csv"), s))
    b = profiler.profile_id(profiler.profile(parse(changed, "csv"), s))
    assert a != b


def test_candidate_key_is_the_unique_complete_column(small_csv_bytes, s):
    facts = profiler.profile(parse(small_csv_bytes, "csv"), s)
    assert ["member_id"] in facts.candidate_keys
    # member_dob has a null, so it cannot be a key
    assert ["member_dob"] not in facts.candidate_keys


def test_phi_candidates_come_from_column_names(small_csv_bytes, s):
    facts = profiler.profile(parse(small_csv_bytes, "csv"), s)
    assert set(facts.phi_candidates) >= {
        "member_id",
        "member_first_name",
        "member_dob",
        "member_sex",
    }
    assert "product" not in facts.phi_candidates


def test_duplicate_rows_are_counted(s):
    content = b"a,b\n1,2\n1,2\n3,4\n"
    facts = profiler.profile(parse(content, "csv"), s)
    assert facts.row_count == 3
    assert facts.duplicate_rows == 1


def test_type_inference_across_shapes(s):
    content = (
        b"n,d,ts,f,b,c\n"
        b"1,2026-01-01,2026-01-01 10:00,1.5,yes,AB-12\n"
        b"2,2026-01-02,2026-01-02 11:00,2.5,no,CD-34\n"
    )
    by_name = {c.name: c for c in profiler.profile(parse(content, "csv"), s).columns}
    assert by_name["n"].inferred_type == "int"
    assert by_name["d"].inferred_type == "date"
    assert by_name["ts"].inferred_type == "timestamp"
    assert by_name["f"].inferred_type == "decimal"
    assert by_name["b"].inferred_type == "bool"
    assert by_name["c"].inferred_type == "code"


def test_timestamps_without_a_leading_zero_hour_are_timestamps(s):
    content = b"ts\n2025-09-01 0:00:00\n2026-01-01 0:00:00\n"
    column = profiler.profile(parse(content, "csv"), s).columns[0]
    assert column.inferred_type == "timestamp"
    assert (column.min, column.max) == ("2025-09-01", "2026-01-01")


def test_empty_file_is_a_parse_error(s):
    with pytest.raises(ParseError):
        parse(b"", "csv")


# ------------------------------------------------------- v2: role hints (§7.1)


def test_profiler_version_is_two():
    assert profiler.PROFILER_VERSION == "2"


@pytest.mark.parametrize(
    ("header", "values", "hint"),
    [
        # technical wins over everything, even a timestamp
        ("created_at", ["2026-01-01 10:00", "2026-01-02 10:00"], "technical"),
        ("record_hash", ["a1", "b2"], "technical"),
        ("source_system", ["x", "x"], "technical"),
        # date by type, and a period stored as a number
        ("service_date", ["2026-01-01", "2026-01-02"], "date"),
        ("member_month", ["202402", "202403"], "date"),
        # identifier by name token (even when not unique), and by unique code-like
        # values / candidate-key membership
        ("member_id", ["A", "B", "A"], "identifier"),
        ("provider_npi", ["1", "2", "1"], "identifier"),
        ("tin", ["11", "22", "11"], "identifier"),
        ("ref", ["AB-1", "AB-2", "AB-3"], "identifier"),
        # ...but a label column is never an identifier by uniqueness alone: with
        # two distinct values it is a dimension
        ("member_phone_number", ["5551234567", "5559876543"], "dimension"),
        # measure: a quantity, not a label (values repeat, so no column is a key)
        ("paid_amount", ["10.5", "20.25", "10.5"], "measure"),
        ("member_age", ["8", "6", "8"], "measure"),
        ("member_zip_code", ["14235", "14278", "14235"], "dimension"),
        ("mobile_number", ["2004403363", "9998893127", "2004403363"], "dimension"),
        # dimension: few distinct values
        ("product", ["TANF Adult", "TANF Child", "TANF Adult"], "dimension"),
        ("is_active", ["yes", "no", "yes"], "dimension"),
        # no evidence at all
        ("segtype", ["", ""], "unclassified"),
    ],
)
def test_hint_rules_on_synthetic_columns(header, values, hint, s):
    content = (header + "\n" + "\n".join(values) + "\n").encode()
    column = profiler.profile(parse(content, "csv"), s).columns[0]
    assert column.hint == hint, (header, column.inferred_type, column.hint)


def test_destination_is_not_an_identifier_because_it_contains_tin(s):
    column = profiler.profile(parse(b"destination\nNY\nNY\nSC\n", "csv"), s).columns[0]
    assert column.hint == "dimension"


def test_high_cardinality_free_text_is_unclassified(s):
    # 150 distinct values over 200 rows: not a key, not few enough to be a dimension.
    rows = "\n".join(f"street {i % 150} apt" for i in range(200))
    column = profiler.profile(parse(f"note\n{rows}\n".encode(), "csv"), s).columns[0]
    assert column.hint == "unclassified"


# ---------------------------------------------- v2: statistics and sentinels


def test_null_ratio_constant_and_top_values(s):
    content = b"plan,amount\nA,1\nA,2\n,3\nA,\n"
    _, by_name = _by_name(content, s)
    plan, amount = by_name["plan"], by_name["amount"]
    assert plan.null_ratio == 0.25
    assert plan.constant is True
    assert [(t.value, t.count) for t in plan.top_values] == [("A", 3)]
    assert amount.constant is False
    assert (amount.min, amount.max) == ("1", "3")


def test_top_values_are_capped_and_ordered_by_count(s):
    values = [f"v{i}" for i in range(15) for _ in range(i + 1)]  # v14 most frequent
    content = ("k\n" + "\n".join(values) + "\n").encode()
    column = profiler.profile(parse(content, "csv"), s).columns[0]
    assert len(column.top_values) == profiler.TOP_VALUES_CAP == 10
    assert column.top_values[0].value == "v14"
    assert [t.count for t in column.top_values] == sorted(
        (t.count for t in column.top_values), reverse=True
    )


def test_bounds_are_typed_and_exclude_sentinels(s):
    content = b"d,n\n2026-01-05,5\n9999-12-31,0000\n01/15/2024,-2\n1900-01-01,9999\n"
    _, by_name = _by_name(content, s)
    assert (by_name["d"].min, by_name["d"].max) == ("2024-01-15", "2026-01-05")
    assert by_name["d"].sentinel_count == 2
    assert (by_name["n"].min, by_name["n"].max) == ("-2", "5")
    assert by_name["n"].sentinel_count == 2  # 0000 and 9999


def test_strings_have_no_bounds(s):
    column = profiler.profile(parse(b"name\nzed\nalpha\n", "csv"), s).columns[0]
    assert (column.min, column.max) == (None, None)


def test_time_coverage_spans_the_data_dates_but_not_phi_or_technical(s):
    content = (
        b"member_dob,coverage_start,coverage_end,created_at\n"
        b"1961-11-14,2024-02-01,2024-02-29,2026-03-01 08:00\n"
        b"1970-01-01,2025-06-01,2026-01-31,2026-03-01 08:00\n"
    )
    facts, by_name = _by_name(content, s)
    assert by_name["member_dob"].hint == "date" and by_name["member_dob"].phi_candidate
    assert by_name["created_at"].hint == "technical"
    assert facts.time_coverage is not None
    assert facts.time_coverage.columns == ["coverage_start", "coverage_end"]
    assert (facts.time_coverage.min, facts.time_coverage.max) == ("2024-02-01", "2026-01-31")


def test_no_date_columns_means_no_time_coverage(small_csv_bytes, s):
    # member_dob is the only date and it is PHI.
    facts = profiler.profile(parse(small_csv_bytes, "csv"), s)
    assert facts.time_coverage is None


# ------------------------------------------------------------------- PHI


def test_phi_columns_carry_no_values_at_all(small_csv_bytes, s):
    facts, by_name = _by_name(small_csv_bytes, s)
    dob = by_name["member_dob"]
    assert dob.phi_candidate
    assert dob.top_values == [] and dob.min is None and dob.max is None
    assert by_name["product"].top_values  # non-PHI keeps its frequencies

    # Defensive layer: even a hand-built PHI column loses everything on the way out.
    forged = facts.model_copy(
        update={
            "columns": [
                c.model_copy(
                    update={
                        "sample_values": ["1997-11-04"],
                        "top_values": [{"value": "1997-11-04", "count": 1}],
                        "min": "1997-11-04",
                        "max": "2013-11-04",
                    }
                )
                if c.name == "member_dob"
                else c
                for c in facts.columns
            ]
        }
    )
    masked = {c.name: c for c in mask_facts(forged).columns}
    assert masked["member_dob"].sample_values == []
    assert masked["member_dob"].top_values == []
    assert masked["member_dob"].min is None and masked["member_dob"].max is None
    assert masked["product"].top_values == by_name["product"].top_values


# ---------------------------------------------------------------- golden sets


def test_real_roster_csv_profiles(roster_csv_bytes, s):
    """The actual de-identified Fidelis upstate roster from the reference corpus."""
    facts, by_name = _by_name(roster_csv_bytes, s)
    assert facts.row_count == 28333
    assert len(facts.columns) == 45
    names = [c.name for c in facts.columns]
    assert names[0] == "vbp_id"
    assert names[-1] == "enrollment_date"
    assert ["member_id"] in facts.candidate_keys
    assert "member_dob" in facts.phi_candidates

    # Hints, per the §7.1 rules, on the real columns.
    assert by_name["member_id"].hint == "identifier" and by_name["member_id"].phi_candidate
    assert by_name["medicaid_id"].hint == "identifier"
    assert by_name["provider_npi"].hint == "identifier"
    assert by_name["tin"].hint == "identifier"
    assert by_name["member_dob"].hint == "date"
    assert by_name["enrollment_date"].hint == "date"  # `2025-09-01 0:00:00` timestamps
    assert by_name["member_age"].hint == "measure"
    assert (by_name["member_age"].min, by_name["member_age"].max) == ("0", "97")
    assert by_name["product"].hint == "dimension"
    assert by_name["product"].top_values[0].value == "TANF Child"
    assert by_name["member_state"].hint == "dimension"
    assert by_name["member_phone_number"].hint != "identifier"
    assert by_name["vbp_id"].constant is True

    coverage = facts.time_coverage
    assert coverage is not None
    assert "member_dob" not in coverage.columns  # PHI dates are not the data's period
    assert {"enrollment_date", "coverage_end_date"} <= set(coverage.columns)
    assert coverage.min <= "2021-01-04" and coverage.max == "2027-10-31"


def test_real_molina_txt_head_profiles(molina_head_bytes, s):
    """Pipe-delimited enrollment history (many rows per member): identifiers by
    name, not by uniqueness; the period from the coverage dates, not from the
    de-identified dates of birth and death."""
    facts, by_name = _by_name(molina_head_bytes, s)
    assert facts.row_count == 5000
    assert len(facts.columns) == 60
    assert facts.candidate_keys == []  # history grain: no single unique column

    assert by_name["Member_ID"].hint == "identifier" and by_name["Member_ID"].phi_candidate
    assert by_name["Medicaid_State_ID"].hint == "identifier"
    assert by_name["Health_Plan_State_code"].hint == "dimension"
    assert by_name["Health_Plan_State_code"].constant is True
    assert by_name["Member_Date_Of_Birth"].hint == "date"
    assert by_name["Member_Date_Of_Death"].phi_candidate
    assert by_name["Member_Month"].hint == "date"
    assert by_name["Member_Mobile_Number"].hint != "measure"
    assert by_name["Member_Health_Plan_Product"].hint == "dimension"
    empty = by_name["SegType"]
    assert (empty.null_ratio, empty.hint) == (1.0, "unclassified")
    assert (by_name["Coverage_Effective_Date"].min, by_name["Coverage_Term_Date"].max) == (
        "2024-02-01",
        "2026-01-31",
    )

    coverage = facts.time_coverage
    assert coverage is not None
    assert "Member_Date_Of_Death" not in coverage.columns
    assert "Member_Date_Of_Birth" not in coverage.columns
    assert (coverage.min, coverage.max) == ("2024-02-01", "2026-01-31")


def test_real_roster_xlsx_profiles(roster_xlsx_bytes, s):
    facts = profiler.profile(parse(roster_xlsx_bytes, "xlsx"), s)
    assert len(facts.columns) == 45
    assert facts.row_count > 0
    assert facts.sheets[0].name == "Sheet1"
