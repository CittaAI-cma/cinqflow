"""Parse or reject — the subset, and what it refuses to pretend it understands.

    "structured output: parse-or-reject -> one bounded retry -> manual path"
    — docs/architecture/INVARIANTS.md, intelligence
"""

from __future__ import annotations

from typing import Any

import pytest

from cinqflow.core.intelligence.validate import SchemaError, validate

pytestmark = [pytest.mark.unit, pytest.mark.lane1]

ANSWER: dict[str, Any] = {
    "type": "object",
    "required": ["claims", "confidence"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "citation_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "unanswered": {"type": "array", "items": {"type": "string"}},
    },
}


def test_a_valid_answer_produces_no_errors() -> None:
    assert validate(
        ANSWER,
        {
            "claims": [{"text": "21,820 rows loaded", "citation_ids": ["recon:8842"]}],
            "confidence": "high",
            "unanswered": [],
        },
    ) == ()


def test_every_violation_is_reported_not_just_the_first() -> None:
    """One error at a time needs one retry per error, and the retry budget is one."""
    errors = validate(ANSWER, {"claims": [{}], "confidence": "certain"})
    assert len(errors) == 3
    assert "$.claims[0].text: required, and absent" in errors
    assert "$.claims[0].citation_ids: required, and absent" in errors
    assert any("'certain' is not one of" in e for e in errors)


def test_errors_are_paths_a_retry_can_act_on() -> None:
    (error,) = validate(ANSWER, {"claims": [], "confidence": "high", "unanswered": [7]})
    assert error == "$.unanswered[0]: expected string, got int"


def test_an_undeclared_property_is_a_violation() -> None:
    (error,) = validate(ANSWER, {"claims": [], "confidence": "high", "invented": True})
    assert error == "$.invented: not in the schema"


def test_a_boolean_is_not_an_integer() -> None:
    """True == 1 in Python. A schema that accepted it would let a model answer
    a count with a yes."""
    assert validate({"type": "integer"}, True) == ("$: expected integer, got boolean",)


def test_a_schema_outside_the_subset_is_refused_as_a_platform_bug() -> None:
    with pytest.raises(SchemaError, match="outside the supported subset"):
        validate({"type": "anyOf"}, {})


def test_a_schema_node_without_a_type_is_refused() -> None:
    with pytest.raises(SchemaError, match="states a type"):
        validate({"properties": {}}, {})


def test_an_array_schema_must_state_its_items() -> None:
    with pytest.raises(SchemaError, match="states its items"):
        validate({"type": "array"}, [1])
