"""The constrained mapping representation: validation, derivation, diff.

Deterministic and model-free. A spec is data - a closed vocabulary of named
operations - so Stage 5 can execute it without ever running generated code, and
so an invalid spec is refused at save time rather than discovered at write time.

Validation answers to governed knowledge (`knowledge/canonical.py`), never to
whatever the model proposed.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.knowledge.canonical import CanonicalModel
from cinqflow.workflow.models import (
    MappingField,
    MappingSpec,
    Proposal,
    Transform,
)

#: Stage 4 scope, per templates.md 1.6. Anything else fails validation.
#: Stage 4 scope minus `value_map`: the field's own `value_map` table is the
#: mechanism, and a transform of the same name did nothing while validating
#: cleanly - a rule that silently does nothing is worse than one that is refused.
ALLOWED_OPS = frozenset(
    {"parse_date", "trim", "upper", "lower", "concat", "substring", "cast"}
)

#: Ops that were once accepted, and what to use instead. Refused with a pointer
#: rather than an unhelpful list.
RETIRED_OPS: dict[str, str] = {
    "value_map": "set the field's own value_map table (and on_unmapped_value) instead",
}
#: Required arguments per op; absent means the op takes none.
REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "parse_date": ("format",),
    "concat": ("with",),
    "substring": ("start",),
    "cast": ("to",),
}
ALLOWED_CASTS = frozenset({"string", "int", "decimal", "date", "timestamp", "bool"})
ALLOWED_ON_NULL = frozenset({"reject", "default", "pass"})
ALLOWED_ON_UNMAPPED = frozenset({"quarantine", "pass", "null"})

#: `on_null` rules that mean nothing without `default`, and `on_unmapped_value`
#: rules that mean nothing without a `value_map`. Named here rather than written
#: inline in `validate_spec` below, because the studio publishes them (see the
#: `vocabulary` payload in `api/routers/mapping_versions.py`) so its editor can
#: require the box a rule needs at the moment a dropdown selects that rule.
#: A dependency the UI can only learn about by being refused is a dependency
#: the analyst discovers as a rejected save.
ON_NULL_NEEDS_DEFAULT = frozenset({"default"})
ON_UNMAPPED_NEEDS_VALUE_MAP = ALLOWED_ON_UNMAPPED - {"pass"}

#: Which casts can satisfy a declared canonical type. A spec that would hand a
#: string to a TIMESTAMP column is a defect the analyst should see now.
CAST_FOR_TYPE: dict[str, frozenset[str]] = {
    "string": frozenset({"string"}),
    "int64": frozenset({"int"}),
    "int": frozenset({"int"}),
    "decimal": frozenset({"decimal", "int"}),
    "date": frozenset({"date"}),
    "timestamp": frozenset({"timestamp", "date"}),
    "bool": frozenset({"bool"}),
}


@dataclass(frozen=True)
class SpecError:
    """A field-level error. `field_index` is -1 for spec-level problems."""

    field_index: int
    source: str
    attribute: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "field_index": self.field_index,
            "source": self.source,
            "attribute": self.attribute,
            "message": self.message,
        }


class InvalidSpec(Exception):
    """Raised on save. Carries every error, so the UI can annotate each field."""

    def __init__(self, errors: list[SpecError]) -> None:
        super().__init__(f"{len(errors)} validation error(s)")
        self.errors = errors

    def as_list(self) -> list[dict[str, object]]:
        return [error.as_dict() for error in self.errors]


def validate_spec(spec: MappingSpec, canonical: CanonicalModel) -> list[SpecError]:
    """Every reason this spec could not be executed, at once."""
    errors: list[SpecError] = []

    if not spec.fields:
        errors.append(SpecError(-1, "", "fields", "a mapping needs at least one field"))

    if canonical.legal_targets and spec.target_table:
        table = spec.target_table.split(".")[-1]
        if table not in canonical.primary_keys:
            errors.append(
                SpecError(
                    -1,
                    "",
                    "target_table",
                    f"'{spec.target_table}' is not an entity in the canonical model "
                    f"({', '.join(canonical.tables)})",
                )
            )

    seen_sources: dict[str, int] = {}
    seen_targets: dict[str, int] = {}

    for index, mapping in enumerate(spec.fields):
        source = mapping.source

        if not source.strip():
            errors.append(SpecError(index, source, "source", "source column is required"))
        elif source in seen_sources:
            errors.append(
                SpecError(
                    index,
                    source,
                    "source",
                    f"'{source}' is already mapped at field {seen_sources[source] + 1}",
                )
            )
        else:
            seen_sources[source] = index

        # --- target: governed knowledge decides -----------------------------
        target = mapping.target.strip()
        if not target:
            errors.append(SpecError(index, source, "target", "target field is required"))
        elif canonical.legal_targets and target not in canonical.legal_targets:
            hint = ""
            leaf = target.split(".")[-1]
            if leaf in canonical.contested:
                hint = " - the canonical model records this field as contested/absent"
            elif leaf in canonical.system_populated:
                hint = " - this column is populated by the platform, not by a mapping"
            errors.append(
                SpecError(
                    index,
                    source,
                    "target",
                    f"'{target}' is not a field in the canonical model{hint}",
                )
            )
        elif target in seen_targets:
            errors.append(
                SpecError(
                    index,
                    source,
                    "target",
                    f"'{target}' is already mapped from "
                    f"'{spec.fields[seen_targets[target]].source}'; one source per target",
                )
            )
        else:
            seen_targets[target] = index

        # --- cast: must be able to satisfy the declared type ----------------
        if mapping.cast not in ALLOWED_CASTS:
            errors.append(
                SpecError(
                    index,
                    source,
                    "cast",
                    f"'{mapping.cast}' is not a supported cast "
                    f"({', '.join(sorted(ALLOWED_CASTS))})",
                )
            )
        else:
            declared = canonical.type_of(target)
            acceptable = CAST_FOR_TYPE.get(declared or "", frozenset())
            if declared and acceptable and mapping.cast not in acceptable:
                errors.append(
                    SpecError(
                        index,
                        source,
                        "cast",
                        f"'{target}' is declared {declared}; cast '{mapping.cast}' cannot "
                        f"satisfy it (use {' or '.join(sorted(acceptable))})",
                    )
                )

        # --- transform: named op from the closed vocabulary -----------------
        if mapping.transform is not None:
            op = mapping.transform.op
            if op in RETIRED_OPS:
                errors.append(
                    SpecError(
                        index,
                        source,
                        "transform",
                        f"the '{op}' transform is no longer supported - "
                        f"{RETIRED_OPS[op]}",
                    )
                )
            elif op not in ALLOWED_OPS:
                errors.append(
                    SpecError(
                        index,
                        source,
                        "transform",
                        f"'{op}' is not a supported transform "
                        f"({', '.join(sorted(ALLOWED_OPS))})",
                    )
                )
            else:
                for required in REQUIRED_ARGS.get(op, ()):
                    if not str(mapping.transform.args.get(required, "")).strip():
                        errors.append(
                            SpecError(
                                index,
                                source,
                                "transform",
                                f"transform '{op}' requires argument '{required}'",
                            )
                        )

        # --- null and value handling ----------------------------------------
        if mapping.on_null not in ALLOWED_ON_NULL:
            errors.append(
                SpecError(
                    index,
                    source,
                    "on_null",
                    f"'{mapping.on_null}' is not supported "
                    f"({', '.join(sorted(ALLOWED_ON_NULL))})",
                )
            )
        if mapping.on_null in ON_NULL_NEEDS_DEFAULT and mapping.default is None:
            errors.append(
                SpecError(index, source, "default", "on_null 'default' needs a default value")
            )
        if mapping.on_unmapped_value not in ALLOWED_ON_UNMAPPED:
            errors.append(
                SpecError(
                    index,
                    source,
                    "on_unmapped_value",
                    f"'{mapping.on_unmapped_value}' is not supported "
                    f"({', '.join(sorted(ALLOWED_ON_UNMAPPED))})",
                )
            )
        if mapping.on_unmapped_value in ON_UNMAPPED_NEEDS_VALUE_MAP and not mapping.value_map:
            errors.append(
                SpecError(
                    index,
                    source,
                    "on_unmapped_value",
                    "on_unmapped_value only applies when a value_map is set",
                )
            )

    return errors


def assert_valid(spec: MappingSpec, canonical: CanonicalModel) -> None:
    errors = validate_spec(spec, canonical)
    if errors:
        raise InvalidSpec(errors)


def spec_from_proposal(proposal: Proposal, canonical: CanonicalModel) -> MappingSpec:
    """Seed a draft from an AI proposal.

    Only candidates with a legal target are carried over: ambiguous, unknown and
    invalid entries are deliberately left out, so the analyst adds them by
    deciding rather than by accepting a guess. Where two columns contested one
    target, neither is carried.
    """
    contested = {
        target
        for target in {f.target for f in proposal.content.fields if f.target}
        if sum(1 for f in proposal.content.fields if f.target == target) > 1
    }

    fields: list[MappingField] = []
    for candidate in proposal.content.fields:
        if candidate.status != "candidate" or not candidate.target:
            continue
        if candidate.target in contested or candidate.target not in canonical.legal_targets:
            continue

        declared = canonical.type_of(candidate.target) or "string"
        cast = _default_cast(declared)
        transform = (
            Transform(op=candidate.transform.op, args=dict(candidate.transform.args))
            if candidate.transform
            else None
        )
        fields.append(
            MappingField(
                source=candidate.source,
                target=candidate.target,
                cast=cast,
                transform=transform,
                edited=False,
            )
        )

    fields.sort(key=lambda f: f.target)
    primary = _primary_table(fields, canonical)
    return MappingSpec(target_table=primary, fields=fields)


def _default_cast(declared: str) -> str:
    """The cast that matches the declared type, where one exists.

    `timestamp` accepts both `timestamp` and `date`; seeding the declared type
    keeps the draft closest to what the canonical column actually is.
    """
    acceptable = CAST_FOR_TYPE.get(declared, frozenset({"string"}))
    if declared in acceptable:
        return declared
    return sorted(acceptable)[0] if acceptable else "string"


def _primary_table(fields: list[MappingField], canonical: CanonicalModel) -> str:
    """The entity most fields land in; falls back to the model's first table."""
    tally: dict[str, int] = {}
    for mapping in fields:
        table = mapping.target.split(".")[0] if "." in mapping.target else ""
        if table:
            tally[table] = tally.get(table, 0) + 1
    if tally:
        best = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return f"silver_raw.{best}"
    return f"silver_raw.{canonical.tables[0]}" if canonical.tables else "silver_raw.members"


def diff_specs(before: MappingSpec | None, after: MappingSpec) -> dict[str, object]:
    """What changed, by source column. Used for draft-vs-approved and for
    showing which fields the analyst has taken ownership of."""
    previous = {f.source: f for f in (before.fields if before else [])}
    current = {f.source: f for f in after.fields}

    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    changed: list[dict[str, object]] = []

    for source in sorted(set(previous) & set(current)):
        was, now = previous[source], current[source]
        attributes: dict[str, object] = {}
        for attribute in ("target", "cast", "on_null", "default", "on_unmapped_value"):
            old, new = getattr(was, attribute), getattr(now, attribute)
            if old != new:
                attributes[attribute] = {"from": old, "to": new}
        if (was.transform.model_dump() if was.transform else None) != (
            now.transform.model_dump() if now.transform else None
        ):
            attributes["transform"] = {
                "from": was.transform.model_dump() if was.transform else None,
                "to": now.transform.model_dump() if now.transform else None,
            }
        if was.value_map != now.value_map:
            attributes["value_map"] = {"from": was.value_map, "to": now.value_map}
        if attributes:
            changed.append({"source": source, "attributes": attributes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "analyst_edited": sorted(f.source for f in after.fields if f.edited),
        "from_proposal": sorted(f.source for f in after.fields if not f.edited),
        "unchanged": len(set(previous) & set(current)) - len(changed),
    }
