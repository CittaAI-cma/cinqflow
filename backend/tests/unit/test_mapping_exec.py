"""The executor: every transform, cast and rule, and every failure attributed."""

from __future__ import annotations

import pytest

from cinqflow.engine.mapping_exec import (
    execute_field,
    execute_spec,
    normalise_date_format,
    spec_fingerprint,
)
from cinqflow.workflow.models import MappingField, MappingSpec, Transform


def field(**kwargs) -> MappingField:
    return MappingField(**{"source": "col", "target": "members.first_name", **kwargs})


def spec(*fields: MappingField) -> MappingSpec:
    return MappingSpec(target_table="silver_raw.members", fields=list(fields))


# ------------------------------------------------------------------- transforms


@pytest.mark.parametrize(
    "op,args,value,expected",
    [
        ("trim", {}, "  Danielle  ", "Danielle"),
        ("upper", {}, "danielle", "DANIELLE"),
        ("lower", {}, "DANIELLE", "danielle"),
        ("concat", {"with": "-NY"}, "FCNY", "FCNY-NY"),
        ("substring", {"start": "0", "length": "4"}, "96863747295", "9686"),
        ("substring", {"start": "5"}, "96863747295", "747295"),
        ("parse_date", {"format": "%m/%d/%Y"}, "11/04/1997", "1997-11-04"),
        ("parse_date", {"format": "MM/DD/YYYY"}, "11/04/1997", "1997-11-04"),
    ],
)
def test_named_transforms_do_what_they_say(op, args, value, expected):
    outcome = execute_field(
        field(transform=Transform(op=op, args=args), cast="string"), {"col": value}
    )
    assert outcome.outcome == "ok"
    assert outcome.mapped_value == expected


def test_human_date_masks_are_translated_not_refused():
    """The mapping documents in docs/ are written MM/DD/YYYY, not %m/%d/%Y."""
    assert normalise_date_format("MM/DD/YYYY") == "%m/%d/%Y"
    assert normalise_date_format("YYYY-MM-DD") == "%Y-%m-%d"
    assert normalise_date_format("%Y-%m-%d") == "%Y-%m-%d"  # already a format


def test_a_transform_failure_names_its_rule_and_reason():
    outcome = execute_field(
        field(transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"})),
        {"col": "13/45/1990"},
    )
    assert outcome.outcome == "failure"
    assert outcome.rule == "parse_date"
    assert outcome.mapped_value is None
    assert "does not match format" in outcome.reason
    assert outcome.source_value == "13/45/1990"  # the offending value is kept


# ------------------------------------------------------------------------ casts


@pytest.mark.parametrize(
    "cast,value,expected",
    [
        ("string", "M001", "M001"),
        ("int", " 42 ", "42"),
        ("decimal", "3.50", "3.50"),
        ("bool", "Yes", "true"),
        ("bool", "no", "false"),
        ("date", "1997-11-04", "1997-11-04"),
        ("timestamp", "1997-11-04", "1997-11-04T00:00:00+00:00"),
    ],
)
def test_casts_produce_canonical_forms(cast, value, expected):
    target = "members.dnc" if cast == "bool" else "members.first_name"
    outcome = execute_field(field(target=target, cast=cast), {"col": value})
    assert outcome.outcome == "ok"
    assert outcome.mapped_value == expected


@pytest.mark.parametrize(
    "cast,value", [("int", "abc"), ("decimal", "1.2.3"), ("bool", "maybe"), ("date", "not-a-date")]
)
def test_cast_failures_are_attributed_to_the_cast_rule(cast, value):
    outcome = execute_field(field(cast=cast), {"col": value})
    assert outcome.outcome == "failure"
    assert outcome.rule == "cast"
    assert value in outcome.reason


# --------------------------------------------------------- null and value rules


def test_on_null_reject_marks_the_row_rejected():
    outcome = execute_field(field(on_null="reject"), {"col": ""})
    assert outcome.outcome == "rejected"
    assert outcome.rule == "on_null"


def test_on_null_default_supplies_the_default():
    outcome = execute_field(field(on_null="default", default="unknown"), {"col": None})
    assert outcome.outcome == "defaulted"
    assert outcome.mapped_value == "unknown"


def test_on_null_pass_lets_null_through():
    outcome = execute_field(field(on_null="pass"), {"col": ""})
    assert outcome.outcome == "null"
    assert outcome.mapped_value is None


def test_a_missing_column_is_treated_as_null_not_as_an_error():
    outcome = execute_field(field(on_null="pass"), {})
    assert outcome.outcome == "null"


def test_value_map_translates_known_values():
    outcome = execute_field(
        field(target="members.sex", value_map={"M": "male", "F": "female"}), {"col": "F"}
    )
    assert outcome.mapped_value == "female"


def test_unmapped_value_can_quarantine_the_row():
    outcome = execute_field(
        field(target="members.sex", value_map={"M": "male"}, on_unmapped_value="quarantine"),
        {"col": "X"},
    )
    assert outcome.outcome == "quarantined"
    assert outcome.rule == "on_unmapped_value"
    assert "not in the value map" in outcome.reason


def test_unmapped_value_can_pass_through_unchanged():
    outcome = execute_field(
        field(target="members.sex", value_map={"M": "male"}, on_unmapped_value="pass"),
        {"col": "X"},
    )
    assert outcome.outcome == "ok"
    assert outcome.mapped_value == "X"


def test_unmapped_value_can_become_null():
    outcome = execute_field(
        field(target="members.sex", value_map={"M": "male"}, on_unmapped_value="null"),
        {"col": "X"},
    )
    assert outcome.outcome == "null"
    assert outcome.mapped_value is None


# -------------------------------------------------------------- whole-spec runs


ROWS = [
    {"member_id": "M1", "member_dob": "1997-11-04", "member_sex": "F"},
    {"member_id": "M2", "member_dob": "13/45/1990", "member_sex": "M"},
    {"member_id": "", "member_dob": "2000-01-01", "member_sex": "M"},
    {"member_id": "M4", "member_dob": "2001-02-03", "member_sex": "X"},
]
SPEC = spec(
    field(source="member_id", target="members.source_system_id", on_null="reject"),
    field(
        source="member_dob",
        target="members.date_of_birth",
        cast="timestamp",
        transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"}),
    ),
    field(
        source="member_sex",
        target="members.sex",
        value_map={"M": "male", "F": "female"},
        on_unmapped_value="quarantine",
    ),
)


def test_each_row_gets_the_worst_outcome_of_its_fields():
    result = execute_spec(SPEC, ROWS)
    assert [r.outcome for r in result.rows] == ["ok", "failure", "rejected", "quarantined"]
    assert [r.row_number for r in result.rows] == [1, 2, 3, 4]


def test_counts_account_for_every_row():
    counts = execute_spec(SPEC, ROWS).counts
    assert counts["rows_previewed"] == 4
    assert (
        counts["rows_ok"]
        + counts["rows_failure"]
        + counts["rows_rejected"]
        + counts["rows_quarantined"]
        == 4
    )


def test_failures_are_grouped_by_source_and_rule():
    assert execute_spec(SPEC, ROWS).failures_by_rule == {
        "member_dob:parse_date": 1,
        "member_id:on_null": 1,
        "member_sex:on_unmapped_value": 1,
    }


def test_null_or_invalid_is_reported_per_canonical_target():
    assert execute_spec(SPEC, ROWS).null_or_invalid == {
        "members.date_of_birth": 1,
        "members.sex": 1,
        "members.source_system_id": 1,
    }


def test_affected_sources_names_the_columns_needing_attention():
    assert execute_spec(SPEC, ROWS).affected_sources == {
        "member_dob": 1,
        "member_id": 1,
        "member_sex": 1,
    }


def test_only_clean_rows_are_writable():
    """Stage 6 will load these; failed, rejected and quarantined rows are not here."""
    writable = execute_spec(SPEC, ROWS).writable_rows
    assert len(writable) == 1
    assert writable[0]["members.source_system_id"] == "M1"
    assert writable[0]["members.sex"] == "female"


def test_defaults_and_permitted_nulls_do_not_make_a_row_a_problem():
    result = execute_spec(
        spec(
            field(source="a", target="members.first_name", on_null="pass"),
            field(source="b", target="members.language", on_null="default", default="English"),
        ),
        [{"a": "", "b": ""}],
    )
    assert result.rows[0].outcome == "ok"
    assert result.rows[0].mapped["members.language"] == "English"


def test_detail_can_be_omitted_for_the_full_run():
    """Stage 6 runs the same code without materialising per-field records."""
    result = execute_spec(SPEC, ROWS, detail=False)
    assert all(r.fields == [] for r in result.rows)
    assert result.counts["rows_previewed"] == 4
    assert len(result.writable_rows) == 1


# ------------------------------------------------------------------ determinism


def test_the_same_spec_over_the_same_rows_is_byte_identical():
    first = execute_spec(SPEC, ROWS)
    second = execute_spec(SPEC, ROWS)
    assert [r.as_dict() for r in first.rows] == [r.as_dict() for r in second.rows]
    assert first.counts == second.counts
    assert first.failures_by_rule == second.failures_by_rule


def test_the_executor_imports_no_model():
    """Determinism is structural: nothing in the module can reach an LLM."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src/cinqflow/engine/mapping_exec.py"
    text = source.read_text()
    for forbidden in ("intelligence", "langgraph", "anthropic", "AgentRuntime", "LlmClient"):
        assert forbidden not in text


def test_spec_fingerprint_changes_when_the_spec_does():
    a = spec(field(source="x", target="members.first_name"))
    b = spec(field(source="x", target="members.last_name"))
    assert spec_fingerprint(a) == spec_fingerprint(a)
    assert spec_fingerprint(a) != spec_fingerprint(b)
    # an identical spec built separately has the same identity
    same = spec(field(source="x", target="members.first_name"))
    assert spec_fingerprint(a) == spec_fingerprint(same)


def test_spec_fingerprint_ignores_provenance_that_does_not_execute():
    """`edited` and `note` are provenance about a mapping, not part of it.

    `execute_field` reads neither, so a spec that differs only in them produces a
    byte-identical Silver row - and a preview of one is a true preview of the
    other. Hashing them made claiming ownership of a field, or writing down why a
    mapping is right, invalidate a current preview and close G2: the two acts the
    gate most wants an analyst to perform were the two that cost a worker round
    trip.
    """
    plain = spec(field(source="x", target="members.first_name"))
    owned = spec(field(source="x", target="members.first_name", edited=True))
    noted = spec(field(source="x", target="members.first_name", note="agreed with the payer"))

    assert spec_fingerprint(plain) == spec_fingerprint(owned)
    assert spec_fingerprint(plain) == spec_fingerprint(noted)

    # ...while anything the executor does read still changes it.
    recast = spec(field(source="x", target="members.first_name", cast="int"))
    assert spec_fingerprint(plain) != spec_fingerprint(recast)
