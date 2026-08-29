"""Parse or reject. There is no third option, and no "mostly valid".

    "structured output: parse-or-reject -> one bounded retry -> manual path"
    — docs/architecture/INVARIANTS.md, intelligence

A deliberately small JSON-Schema subset — object, array, string, number,
integer, boolean, null, `required`, `enum` — because the platform authors every
schema it validates. A full validator would let a prompt author write a schema
whose failures nobody can read; this one refuses anything it does not
understand, which keeps the schemas inside the subset by construction.

Errors are PATHS, not prose. `claims[0].citation_ids` tells the retry exactly
what to fix; "validation failed" gives the model nothing to act on and turns
the one bounded retry into a coin toss.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ValueError):
    """The SCHEMA is unusable — a platform bug, not a model failure."""


def validate(schema: dict[str, Any], value: Any, *, path: str = "$") -> tuple[str, ...]:
    """Every violation, as a path and a reason. Empty means valid.

    Every violation, not the first: a model given one error at a time needs one
    retry per error, and the retry budget is one.
    """
    declared = schema.get("type")
    if declared is None:
        raise SchemaError(f"{path}: every schema node states a type")
    if declared not in _TYPES:
        raise SchemaError(
            f"{path}: {declared!r} is outside the supported subset "
            f"({', '.join(sorted(_TYPES))})"
        )

    expected = _TYPES[declared]
    # bool is a subclass of int in Python; a boolean where an integer belongs
    # is a real mismatch and must not slip through on that technicality.
    if declared in {"number", "integer"} and isinstance(value, bool):
        return (f"{path}: expected {declared}, got boolean",)
    if not isinstance(value, expected):
        return (f"{path}: expected {declared}, got {type(value).__name__}",)

    errors: list[str] = []
    allowed = schema.get("enum")
    if allowed is not None and value not in allowed:
        errors.append(f"{path}: {value!r} is not one of {allowed}")

    if isinstance(value, dict) and declared == "object":
        properties: dict[str, Any] = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required, and absent")
        for name, child in properties.items():
            if name in value:
                errors.extend(validate(child, value[name], path=f"{path}.{name}"))
        if not schema.get("additionalProperties", False):
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name}: not in the schema")

    elif isinstance(value, list) and declared == "array":
        items = schema.get("items")
        if items is None:
            raise SchemaError(f"{path}: an array schema states its items")
        for index, entry in enumerate(value):
            errors.extend(validate(items, entry, path=f"{path}[{index}]"))

    return tuple(errors)
