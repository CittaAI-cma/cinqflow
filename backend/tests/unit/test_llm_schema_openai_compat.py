"""Every LLM-facing response schema must be representable in OpenAI's
Structured Outputs strict mode, checked without ever calling the API.

Regression coverage for a real incident: `LlmTransform.args` was typed
`dict[str, str]`, which Pydantic renders as an open
`{"type": "object", "additionalProperties": {...}}` schema with no fixed
`properties`. OpenAI's strict-mode converter rejects that shape outright:

    400 - "'required' is required to be supplied and to be an array
    including every key in properties. Extra required key 'args' supplied."

This didn't surface in any existing test because every test double
(`complete_json`) returns Python dicts directly - none of them go through
OpenAI's actual schema conversion. The failure only showed up live, against
a real batch, mid-demo. This test walks the JSON Schema Pydantic generates
and flags the exact shape OpenAI can't accept, so a future field typed as an
open dict fails here instead of on the next production LLM call.
"""

from __future__ import annotations

from typing import Any

from cinqflow.intelligence.schemas import InterpretationResponse, MappingProposalResponse


def _find_open_dict_schemas(node: Any, defs: dict, path: str = "$") -> list[str]:
    """Depth-first search for `{"type": "object", "additionalProperties": {...}}`
    nodes - the shape `dict[str, X]` produces and OpenAI's strict mode can't
    represent, since it has no fixed `properties`/`required` pair."""
    violations: list[str] = []
    if not isinstance(node, dict):
        return violations

    if "$ref" in node:
        ref_name = node["$ref"].rsplit("/", 1)[-1]
        return _find_open_dict_schemas(defs.get(ref_name, {}), defs, path)

    if (
        node.get("type") == "object"
        and isinstance(node.get("additionalProperties"), dict)
        and "properties" not in node
    ):
        violations.append(path)

    for key in ("properties",):
        for name, sub in node.get(key, {}).items():
            violations += _find_open_dict_schemas(sub, defs, f"{path}.{name}")

    if "items" in node:
        violations += _find_open_dict_schemas(node["items"], defs, f"{path}[]")

    for combinator in ("anyOf", "allOf", "oneOf"):
        for i, sub in enumerate(node.get(combinator, [])):
            violations += _find_open_dict_schemas(sub, defs, f"{path}.{combinator}[{i}]")

    return violations


def _assert_openai_strict_compatible(model) -> None:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    violations = _find_open_dict_schemas(schema, defs)
    assert not violations, (
        f"{model.__name__} has open dict[...] schema(s) at {violations} - "
        f"OpenAI's Structured Outputs strict mode rejects these; use a "
        f"list of {{key, value}} objects instead (see LlmTransform.args)"
    )


def test_mapping_proposal_response_is_openai_strict_compatible():
    _assert_openai_strict_compatible(MappingProposalResponse)


def test_interpretation_response_is_openai_strict_compatible():
    _assert_openai_strict_compatible(InterpretationResponse)
