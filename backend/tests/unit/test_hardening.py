"""The deterministic half of the post-Stage-6 hardening.

Each test here pins a behaviour that a stage report listed as a gap, so the gap
cannot quietly reopen.
"""

from __future__ import annotations

import pytest

from cinqflow.engine.mapping_exec import (
    DEFAULT_STRATEGY,
    SAMPLE_STRATEGIES,
    FieldOutcome,
    RowOutcome,
    _apply_cast,
    execute_spec,
    expected_entity_rows,
    read_selector,
    sample_selector,
    sample_stride,
)
from cinqflow.engine.mapping_spec import ALLOWED_OPS, RETIRED_OPS, validate_spec
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.workflow.models import MappingField, MappingSpec, Transform

MEMBER_KEY = "source_system_id"


def _outcome(target: str, mapped: str | None, kind: str = "ok") -> FieldOutcome:
    return FieldOutcome("src", target, "raw", mapped, kind)


def _result(*rows: RowOutcome):
    from cinqflow.engine.mapping_exec import ExecutionResult

    return ExecutionResult(rows=list(rows))


# --------------------------------------------------- timestamps carry their offset


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1997-11-04", "1997-11-04T00:00:00+00:00"),
        ("1997-11-04 08:30:00", "1997-11-04T08:30:00+00:00"),
        ("1997-11-04T08:30:00", "1997-11-04T08:30:00+00:00"),
    ],
)
def test_a_timestamp_cast_states_its_offset(value: str, expected: str):
    """A date-only value in a timestamp column must not land at midnight in
    whichever timezone the connection happened to be using."""
    field = MappingField(source="s", target="members.date_of_birth", cast="timestamp")
    assert _apply_cast(value, "timestamp", field) == expected


def test_a_date_cast_stays_a_plain_date():
    field = MappingField(source="s", target="members.date_of_birth", cast="date")
    assert _apply_cast("1997-11-04", "date", field) == "1997-11-04"


# ------------------------------------------------------- the value_map op is gone


def test_the_value_map_transform_is_refused_with_a_pointer(settings):
    """It validated cleanly and did nothing, which is worse than being refused."""
    assert "value_map" not in ALLOWED_OPS
    assert "value_map" in RETIRED_OPS

    from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider

    canonical = load_canonical(YamlKnowledgeProvider(settings), "enrollment")
    spec = MappingSpec(
        target_table="silver_raw.members",
        fields=[
            MappingField(
                source="member_sex",
                target="members.sex",
                transform=Transform(op="value_map", args={"F": "female"}),
            )
        ],
    )
    errors = [e for e in validate_spec(spec, canonical) if e.attribute == "transform"]
    assert len(errors) == 1
    assert "no longer supported" in errors[0].message
    assert "value_map table" in errors[0].message


def test_the_model_may_not_propose_it_either():
    """The graph's vocabulary mirrors the spec's, so the AI cannot suggest a
    transform the analyst would be unable to save."""
    from cinqflow.intelligence.graphs import recommend_mapping

    assert "value_map" not in recommend_mapping.ALLOWED_OPS
    assert recommend_mapping.ALLOWED_OPS <= ALLOWED_OPS


# ------------------------------------------------------------ sampling strategies


def test_the_default_sample_is_spread_across_the_batch():
    """A clean first window says as much about the window as about the mapping."""
    assert DEFAULT_STRATEGY == "spread"
    assert sample_selector(200) == "spread_200"
    assert sample_selector(200, "first") == "first_200"
    assert read_selector("spread_200") == ("spread", 200)
    with pytest.raises(ValueError, match="unknown sample strategy"):
        sample_selector(200, "random")
    with pytest.raises(ValueError, match="unrecognised selector"):
        read_selector("first_200; DROP TABLE")


def test_the_stride_covers_the_batch_without_running_past_it():
    assert sample_stride("spread", limit=200, rows_in_batch=28334) == 141
    assert 141 * 200 <= 28334 + 141  # the last sampled row is inside the batch
    # A batch no bigger than the sample is read whole, so spread == first.
    assert sample_stride("spread", limit=200, rows_in_batch=4) == 1
    assert sample_stride("first", limit=200, rows_in_batch=28334) == 1


def test_both_strategies_are_named_and_deterministic():
    assert set(SAMPLE_STRATEGIES) == {"first", "spread"}
    assert sample_selector(50, "spread") == sample_selector(50, "spread")


# --------------------------------------------- the independent per-entity count


def test_expected_rows_are_counted_per_entity():
    result = _result(
        RowOutcome(
            row_number=1,
            outcome="ok",
            fields=[
                _outcome("members.source_system_id", "M001"),
                _outcome("members.first_name", "ANN"),
                _outcome("members_addresses.city", "ALBANY"),
                _outcome("members_emails.email_address", None),
            ],
        ),
        RowOutcome(
            row_number=2,
            outcome="ok",
            fields=[
                _outcome("members.source_system_id", "M002"),
                _outcome("members_addresses.city", None),
            ],
        ),
    )
    expected = expected_entity_rows(result, primary="members", member_key=MEMBER_KEY)
    # Both rows are members; only the first has an address; neither has an email.
    assert expected == {"members": 2, "members_addresses": 1}


def test_a_member_with_only_an_identifier_is_still_a_member():
    """For the primary entity the identifier is the record; for a child, a row
    carrying nothing but the propagated key is not."""
    result = _result(
        RowOutcome(
            row_number=1,
            outcome="ok",
            fields=[
                _outcome("members.source_system_id", "M001"),
                _outcome("members_phones.source_system_id", "M001"),
                _outcome("members_phones.phone_number", None),
            ],
        )
    )
    assert expected_entity_rows(result, primary="members", member_key=MEMBER_KEY) == {
        "members": 1
    }


def test_refused_rows_expect_nothing():
    result = _result(
        RowOutcome(
            row_number=1,
            outcome="rejected",
            fields=[_outcome("members.source_system_id", None, "rejected")],
        )
    )
    assert expected_entity_rows(result, primary="members", member_key=MEMBER_KEY) == {}


def test_the_count_is_independent_of_the_writer_grouping():
    """It reads field outcomes, so a defect in group_by_entity/is_empty shows up as
    a disagreement rather than as silently fewer rows.

    Asserted over the names the compiled function actually references, not over its
    text: the docstring names both functions precisely to say it avoids them.
    """
    referenced = set(expected_entity_rows.__code__.co_names)
    assert "group_by_entity" not in referenced
    assert "is_empty" not in referenced


def test_execute_spec_still_numbers_its_own_rows_from_one():
    """The executor numbers the rows it was given; callers that sample supply the
    batch row number themselves."""
    spec = MappingSpec(
        target_table="silver_raw.members",
        fields=[MappingField(source="member_id", target="members.source_system_id")],
    )
    result = execute_spec(spec, [{"member_id": "M001"}, {"member_id": "M002"}])
    assert [row.row_number for row in result.rows] == [1, 2]
