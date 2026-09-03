"""The profiler is deterministic arithmetic. These tests pin that down."""

from __future__ import annotations

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import ParseError, parse
from cinqflow.settings import Settings


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path)


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


def test_empty_file_is_a_parse_error(s):
    with pytest.raises(ParseError):
        parse(b"", "csv")


def test_real_roster_csv_profiles(roster_csv_bytes, s):
    """The actual de-identified Fidelis upstate roster from the reference corpus."""
    facts = profiler.profile(parse(roster_csv_bytes, "csv"), s)
    assert facts.row_count == 28333
    assert len(facts.columns) == 45
    names = [c.name for c in facts.columns]
    assert names[0] == "vbp_id"
    assert names[-1] == "enrollment_date"
    assert ["member_id"] in facts.candidate_keys
    assert "member_dob" in facts.phi_candidates


def test_real_roster_xlsx_profiles(roster_xlsx_bytes, s):
    facts = profiler.profile(parse(roster_xlsx_bytes, "xlsx"), s)
    assert len(facts.columns) == 45
    assert facts.row_count > 0
    assert facts.sheets[0].name == "Sheet1"
