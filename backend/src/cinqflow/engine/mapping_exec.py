"""The mapping executor: plain Python over a validated spec.

No model is reachable from this module, and nothing here is generated or eval'd -
a spec is a closed vocabulary of named operations, so executing it is a lookup and
a function call. The same code serves preview (Stage 5, with per-field detail) and
the full run (Stage 6, which needs only the mapped rows and the counts).

Every value a source row carries is a string, because Bronze preserved it verbatim.
Casting therefore happens here, once, against the canonical declared type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from cinqflow.workflow.models import MappingField, MappingSpec

#: What happened to one field in one row.
#: ok         - mapped cleanly
#: defaulted  - source was null and the field supplies a default
#: null       - source was null and null is allowed through
#: failure    - a transform or cast could not be applied
#: quarantined- a value_map had no entry and the rule says quarantine
#: rejected   - source was null and the field says reject the row
OUTCOMES = ("ok", "defaulted", "null", "failure", "quarantined", "rejected")

#: Python format codes are what parse_date accepts. A spec written with a
#: human-facing mask (MM/DD/YYYY) is translated rather than refused, because the
#: mapping documents in docs/ are written that way.
_MASKS = [
    ("YYYY", "%Y"),
    ("YY", "%y"),
    ("MM", "%m"),
    ("DD", "%d"),
    ("HH24", "%H"),
    ("HH", "%H"),
    ("MI", "%M"),
    ("SS", "%S"),
]
_TRUE = frozenset({"true", "t", "yes", "y", "1"})
_FALSE = frozenset({"false", "f", "no", "n", "0"})


def normalise_date_format(mask: str) -> str:
    if "%" in mask:
        return mask
    translated = mask
    for token, code in _MASKS:
        translated = translated.replace(token, code)
    return translated


class TransformFailure(Exception):
    """Carries the rule that failed, so the reason is attributable."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


# --------------------------------------------------------------------- transforms


def _apply_transform(value: str, mapping: MappingField) -> str:
    step = mapping.transform
    if step is None:
        return value

    op, args = step.op, step.args
    if op == "trim":
        return value.strip()
    if op == "upper":
        return value.upper()
    if op == "lower":
        return value.lower()
    if op == "concat":
        return f"{value}{args.get('with', '')}"
    if op == "substring":
        try:
            start = int(args.get("start", "0"))
        except ValueError:
            raise TransformFailure("substring", "start must be an integer") from None
        length = args.get("length")
        if length:
            try:
                return value[start : start + int(length)]
            except ValueError:
                raise TransformFailure("substring", "length must be an integer") from None
        return value[start:]
    if op == "parse_date":
        mask = normalise_date_format(args.get("format", ""))
        try:
            return datetime.strptime(value.strip(), mask).date().isoformat()
        except ValueError:
            raise TransformFailure(
                "parse_date", f"'{value}' does not match format {args.get('format')}"
            ) from None
    if op == "cast":
        return _apply_cast(value, args.get("to", "string"), mapping)
    # validate_spec refuses unknown ops, so reaching here is a programming error.
    raise TransformFailure(op, f"unsupported transform '{op}'")


def _apply_cast(value: str, cast: str, mapping: MappingField) -> str:
    """Casting returns the canonical string form; the adapter binds real types."""
    text = value.strip()
    if cast == "string":
        return value
    if cast == "int":
        try:
            return str(int(text))
        except ValueError:
            raise TransformFailure("cast", f"'{value}' is not an integer") from None
    if cast == "decimal":
        try:
            return str(Decimal(text))
        except InvalidOperation:
            raise TransformFailure("cast", f"'{value}' is not a decimal") from None
    if cast == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return "true"
        if lowered in _FALSE:
            return "false"
        raise TransformFailure("cast", f"'{value}' is not a boolean")
    if cast in ("date", "timestamp"):
        candidate = text.replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            if cast == "date":
                return parsed.date().isoformat()
            # The offset is written out rather than implied. A date-only value in a
            # timestamp column would otherwise land at midnight in whatever timezone
            # the session happened to have, which is a property of the connection
            # rather than of the data.
            return parsed.replace(tzinfo=UTC).isoformat()
        raise TransformFailure(
            "cast", f"'{value}' is not a {cast} (expected ISO, or use parse_date)"
        )
    raise TransformFailure("cast", f"unsupported cast '{cast}'")


# ------------------------------------------------------------------------ results


@dataclass
class FieldOutcome:
    source: str
    target: str
    source_value: str | None
    mapped_value: str | None
    outcome: str
    reason: str | None = None
    rule: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "source_value": self.source_value,
            "mapped_value": self.mapped_value,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass
class RowOutcome:
    row_number: int
    outcome: str
    fields: list[FieldOutcome] = field(default_factory=list)
    #: target -> mapped value; what Stage 6 would write for this row.
    mapped: dict[str, str | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "row_number": self.row_number,
            "outcome": self.outcome,
            "fields": [f.as_dict() for f in self.fields],
        }


@dataclass
class ExecutionResult:
    rows: list[RowOutcome] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = {"rows_previewed": len(self.rows)}
        for row in self.rows:
            key = f"rows_{row.outcome}"
            tally[key] = tally.get(key, 0) + 1
        tally.setdefault("rows_ok", 0)
        return tally

    @property
    def failures_by_rule(self) -> dict[str, int]:
        """`source:rule` -> count. Every failure is attributed, never swallowed."""
        tally: dict[str, int] = {}
        for row in self.rows:
            for outcome in row.fields:
                if outcome.outcome in ("failure", "quarantined", "rejected") and outcome.rule:
                    key = f"{outcome.source}:{outcome.rule}"
                    tally[key] = tally.get(key, 0) + 1
        return dict(sorted(tally.items()))

    @property
    def null_or_invalid(self) -> dict[str, int]:
        """Canonical target -> rows that would receive no usable value."""
        tally: dict[str, int] = {}
        for row in self.rows:
            for outcome in row.fields:
                if outcome.mapped_value is None:
                    tally[outcome.target] = tally.get(outcome.target, 0) + 1
        return dict(sorted(tally.items()))

    @property
    def affected_sources(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for row in self.rows:
            for outcome in row.fields:
                if outcome.outcome in ("failure", "quarantined", "rejected"):
                    tally[outcome.source] = tally.get(outcome.source, 0) + 1
        return dict(sorted(tally.items()))

    @property
    def writable_rows(self) -> list[dict[str, str | None]]:
        """Rows Stage 6 could load. Rejected and failed rows are not writable."""
        return [row.mapped for row in self.rows if row.outcome in ("ok", "defaulted", "null")]


#: Row outcome precedence: the worst thing that happened to any of its fields.
_PRECEDENCE = {"rejected": 4, "failure": 3, "quarantined": 2, "defaulted": 1, "null": 1, "ok": 0}


def execute_field(mapping: MappingField, row: dict[str, str]) -> FieldOutcome:
    """One source column through one field's rules. Pure and total: it always
    returns an outcome rather than raising, so no row can fail silently."""
    raw = row.get(mapping.source)
    source_value = raw if raw is not None else None
    is_null = source_value is None or source_value.strip() == ""

    if is_null:
        if mapping.on_null == "reject":
            return FieldOutcome(
                mapping.source, mapping.target, source_value, None, "rejected",
                "source is empty and this field rejects the row", "on_null",
            )
        if mapping.on_null == "default":
            return FieldOutcome(
                mapping.source, mapping.target, source_value, mapping.default, "defaulted",
                f"source is empty; default '{mapping.default}' applied", None,
            )
        return FieldOutcome(
            mapping.source, mapping.target, source_value, None, "null",
            "source is empty; null allowed through", None,
        )

    value = source_value
    if mapping.value_map:
        if value in mapping.value_map:
            value = mapping.value_map[value]
        elif mapping.on_unmapped_value == "quarantine":
            return FieldOutcome(
                mapping.source, mapping.target, source_value, None, "quarantined",
                f"'{source_value}' is not in the value map", "on_unmapped_value",
            )
        elif mapping.on_unmapped_value == "null":
            return FieldOutcome(
                mapping.source, mapping.target, source_value, None, "null",
                f"'{source_value}' is not in the value map; set to null",
                "on_unmapped_value",
            )
        # "pass" leaves the original value in place

    try:
        value = _apply_transform(value, mapping)
        value = _apply_cast(value, mapping.cast, mapping)
    except TransformFailure as failure:
        return FieldOutcome(
            mapping.source, mapping.target, source_value, None, "failure",
            failure.message, failure.rule,
        )

    return FieldOutcome(mapping.source, mapping.target, source_value, value, "ok")


def execute_spec(
    spec: MappingSpec, rows: list[dict[str, str]], *, detail: bool = True
) -> ExecutionResult:
    """Run the whole spec over rows.

    `detail=True` (preview) keeps every field outcome so the analyst can see the
    mapping working. `detail=False` (the full run in Stage 6) keeps only the mapped
    values and the counts, so a large batch does not materialise per-field records.
    """
    result = ExecutionResult()
    for index, row in enumerate(rows, start=1):
        outcomes = [execute_field(mapping, row) for mapping in spec.fields]
        worst = max((_PRECEDENCE[o.outcome] for o in outcomes), default=0)
        row_outcome = next(
            (name for name, rank in _PRECEDENCE.items() if rank == worst and name != "null"),
            "ok",
        )
        if worst == 1:
            row_outcome = "ok"  # defaults and permitted nulls are not problems
        result.rows.append(
            RowOutcome(
                row_number=index,
                outcome=row_outcome,
                fields=outcomes if detail else [],
                mapped={o.target: o.mapped_value for o in outcomes},
            )
        )
    return result


#: Field outcomes that mean the row cannot be written as mapped.
BLOCKING = ("failure", "quarantined", "rejected")


def group_by_entity(mapped: dict[str, str | None]) -> dict[str, dict[str, str | None]]:
    """`{"members.first_name": "ANN"}` -> `{"members": {"first_name": "ANN"}}`.

    One roster row legitimately populates several canonical entities, so writing it
    means fanning it out. Targets without a table prefix are ignored rather than
    guessed at; `validate_spec` refuses them long before this.
    """
    entities: dict[str, dict[str, str | None]] = {}
    for target, value in mapped.items():
        table, _, field_name = target.partition(".")
        if not field_name:
            continue
        entities.setdefault(table, {})[field_name] = value
    return entities


def is_empty(values: dict[str, str | None], *, ignoring: frozenset[str] = frozenset()) -> bool:
    """True when nothing but ignored columns carries a value.

    A roster with no email should not produce an empty `members_emails` row; the
    absence of a child record is not a record of an absence.
    """
    return all(v in (None, "") for name, v in values.items() if name not in ignoring)


def row_reasons(row: RowOutcome) -> list[dict[str, object]]:
    """Why this row was refused, per field. Quarantine stores exactly this."""
    return [
        {
            "source": outcome.source,
            "target": outcome.target,
            "rule": outcome.rule,
            "outcome": outcome.outcome,
            "reason": outcome.reason,
        }
        for outcome in row.fields
        if outcome.outcome in BLOCKING
    ]


#: Field attributes a preview does not depend on, because `execute_field` never
#: reads them: `edited` records who owns the decision, `note` records why it was
#: made. Both are provenance about the mapping, not part of it.
_NON_EXECUTING = ("edited", "note")


def spec_fingerprint(spec: MappingSpec) -> str:
    """Identifies the exact spec a preview describes.

    A preview stops being current the moment the draft changes, and comparing this
    fingerprint is how that is known - no preview row is ever deleted or rewritten.

    Hashed over the spec's *execution projection*: everything `execute_field`
    actually reads, and nothing else. Hashing the whole document instead made the
    two most governance-valuable acts on the mapping screen cost a worker round
    trip and close G2 - ticking "Mine" to claim a field, or writing down why a
    value_map is correct, changed the fingerprint while the mapped output stayed
    byte-identical. A preview is evidence of what this spec *does*, so what it
    does is what identifies it.

    Changing what is hashed makes previews taken before this change read as
    stale, which is the safe direction: they are re-run, never wrongly accepted.
    """
    import hashlib
    import json

    projection = {
        "target_table": spec.target_table,
        "fields": [
            {k: v for k, v in field.model_dump().items() if k not in _NON_EXECUTING}
            for field in spec.fields
        ],
    }
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


#: How a preview's rows are chosen. Both are deterministic, because a preview that
#: could not be reproduced would not be evidence of anything.
#:
#: first  - the first N rows of the batch. Cheap, and what a file's own head shows.
#: spread - every k-th row across the whole batch, k = rows_in_batch // N. Costs the
#:          same and says more: a clean first window says as much about the window
#:          as about the mapping.
SAMPLE_STRATEGIES = ("first", "spread")
DEFAULT_STRATEGY = "spread"

_SELECTOR = re.compile(r"^(first|spread)_(\d+)$")


def sample_selector(limit: int, strategy: str = DEFAULT_STRATEGY) -> str:
    """How the sample was chosen. Recorded on the artifact for reproducibility."""
    if strategy not in SAMPLE_STRATEGIES:
        raise ValueError(f"unknown sample strategy: {strategy}")
    return f"{strategy}_{limit}"


def read_selector(selector: str) -> tuple[str, int]:
    """`spread_200` -> ("spread", 200). Refuses anything it did not write."""
    match = _SELECTOR.match(selector)
    if match is None:
        raise ValueError(f"unrecognised selector: {selector}")
    return match.group(1), int(match.group(2))


def sample_stride(strategy: str, *, limit: int, rows_in_batch: int) -> int:
    """How far apart the sampled rows sit. 1 means consecutive."""
    if strategy != "spread" or limit <= 0 or rows_in_batch <= limit:
        return 1
    return rows_in_batch // limit


def expected_entity_rows(
    result: ExecutionResult, *, primary: str, member_key: str
) -> dict[str, int]:
    """How many rows each entity should receive, counted from field outcomes.

    Deliberately derived from the outcomes rather than from `group_by_entity` and
    `is_empty`, so it is an independent second opinion: if the fan-out or the
    empty-row rule loses an entity's rows, these two counts disagree and the run
    fails instead of quietly writing less than it read.
    """
    expected: dict[str, int] = {}
    for row in result.rows:
        if row.outcome in BLOCKING:
            continue
        seen: set[str] = set()
        for outcome in row.fields:
            table, _, field_name = outcome.target.partition(".")
            if not field_name or outcome.mapped_value in (None, ""):
                continue
            # A child row carrying nothing but the propagated member key is not a
            # record; for the primary entity the identifier *is* the record.
            if table != primary and field_name == member_key:
                continue
            seen.add(table)
        for table in seen:
            expected[table] = expected.get(table, 0) + 1
    return expected
