"""The schema contract and the DQ rules — what `validate`, `cast` and
`evaluate_rules` actually run.

    G2 schema + DQ   bronze -> silver_raw
      checks: [drift_classified_by_meaning, contract_enforced,
               dq_rules_by_severity]
    — docs/architecture/plates/06-the-medallion-spine-and-its-gates.md

In Wave 0 the contract and the rules are AUTHORED BY A HUMAN and stored as
governed objects. Wave 1's agents propose them (CF-V1-E5-02 schema inference,
CF-V1-E7-01 NL -> rule); this module is what those proposals will eventually
produce, which is why it is worth getting the shape right now rather than
later: an agent that proposes into a shape nobody designed produces something
nobody can review.

Severity is the whole reason a rule is not a boolean. From the 110 legacy DQ
rules: 38 Critical, 45 High, 24 Medium, 3 Low — and what a severity DOES is
decide whether a failing row is quarantined or merely flagged. A rule engine
without severity either loses good data or admits bad data.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.schema_spec import TypeName


@unique
class Severity(StrEnum):
    """What a failing row's fate is. The 110-rule set uses all four."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def quarantines(self) -> bool:
        """Critical and High REJECT the row; Medium and Low WARN and pass it.

        The distinction is the difference between "this record is unusable" and
        "this record is imperfect". Quarantining everything would empty a
        roster over a missing middle name.
        """
        return self in {Severity.CRITICAL, Severity.HIGH}


@dataclass(frozen=True)
class ContractColumn:
    """One column under contract: its name, type and whether it may be null."""

    name: str
    type: TypeName
    nullable: bool = True
    # The source's name for it, when they differ. This IS the mapping in Wave 0:
    # a rename, declared as data. Wave 1's mapping studio adds transforms.
    source_name: str | None = None
    is_phi: bool = False
    precision: int | None = None
    scale: int | None = None
    date_formats: tuple[str, ...] = ()

    @property
    def reads_from(self) -> str:
        return self.source_name or self.name


@dataclass(frozen=True)
class SchemaContract:
    """The approved shape of a feed's file. Versioned, like everything."""

    feed_id: str
    version: int
    columns: tuple[ContractColumn, ...]
    # Which columns identify a record WITHIN a batch. A payer sending the same
    # member twice in one file is an ordinary delivery fault, not an outage —
    # so it must become an attributed drop rather than a constraint violation
    # that fails the whole roster.
    key_columns: tuple[str, ...] = ()

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.CONTRACT, subject=self.feed_id, version=self.version)

    @property
    def source_columns(self) -> tuple[str, ...]:
        return tuple(c.reads_from for c in self.columns)

    def column(self, name: str) -> ContractColumn:
        for candidate in self.columns:
            if candidate.name == name or candidate.reads_from == name:
                return candidate
        raise KeyError(f"{self.feed_id}@v{self.version} has no column {name!r}")


@unique
class DriftKind(StrEnum):
    """Drift, classified BY MEANING rather than by structure.

    This distinction is why the Govern screen can say "both names carry the
    same business concept; a structural diff would have called this a dropped
    column plus a new one and failed the batch". Structure sees two events;
    meaning sees one rename.
    """

    NONE = "none"
    ADDED = "added"  # a new column: additive, safe to accept and ignore
    REMOVED = "removed"  # a contracted column is gone: breaks the mapping
    RENAMED = "renamed"  # same concept, new name: needs a human, not a failure
    REORDERED = "reordered"  # harmless — we read by name, never by position
    #: W1-32 — additive AND contract-unknown, and no line in the feed's own
    #: PUBLISHED mapping reads it either: coverage drift `classify()` can only
    #: see once it is handed the mapping, not just the contract.
    UNMAPPED_COLUMN = "unmapped_column"


@dataclass(frozen=True)
class DriftFinding:
    kind: DriftKind
    column: str
    detail: str
    blocks_batch: bool


def compare_to_contract(
    arrived: tuple[str, ...], contract: SchemaContract
) -> tuple[DriftFinding, ...]:
    """Compare what arrived against what was approved.

    Reading BY NAME rather than by position is what makes REORDERED harmless —
    and column order genuinely does change between payer deliveries, so a
    position-based reader would fail a perfectly good file.
    """
    expected = set(contract.source_columns)
    actual = set(arrived)
    findings: list[DriftFinding] = []

    for missing in sorted(expected - actual):
        column = contract.column(missing)
        findings.append(
            DriftFinding(
                kind=DriftKind.REMOVED,
                column=missing,
                detail=(
                    f"{missing!r} is under contract but absent from the file"
                    + ("" if column.nullable else " — and it is not nullable")
                ),
                # A missing REQUIRED column breaks the mapping; a missing
                # optional one is a warning. Failing on both would make every
                # payer's optional-field change an incident.
                blocks_batch=not column.nullable,
            )
        )

    for added in sorted(actual - expected):
        findings.append(
            DriftFinding(
                kind=DriftKind.ADDED,
                column=added,
                detail=f"{added!r} is in the file but not under contract — ignored, not dropped",
                blocks_batch=False,
            )
        )

    if not findings and tuple(arrived) != contract.source_columns:
        findings.append(
            DriftFinding(
                kind=DriftKind.REORDERED,
                column="*",
                detail="the same columns arrived in a different order — harmless, read by name",
                blocks_batch=False,
            )
        )
    return tuple(findings)


# ── casting ──────────────────────────────────────────────────────────────────
class CastFailureError(ValueError):
    """A value that does not fit its contracted type.

    Incident #8: negative pharmacy amounts, and impossible service months
    ('1000-01', '1753-01'). Legacy type debt is real and is being carried
    forward, so a cast failure has to be an ATTRIBUTED drop rather than a
    crash — the row leaves the pipeline with a reason attached.
    """


_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def cast_value(raw: str, column: ContractColumn) -> object | None:
    """Cast one value, or raise CastFailureError naming the column and the value.

    The date normalizer is the canonical unit test in the testing pyramid:
    19900101 and 01/01/1990 are the same date, and that must be true
    IDENTICALLY on both planes — which is why it lives in core/ and not in a
    compute adapter.
    """
    text = raw.strip()
    if not text:
        if column.nullable:
            return None
        raise CastFailureError(f"{column.name} is not nullable, and the value is empty")

    match column.type:
        case TypeName.STRING:
            return text
        case TypeName.INT64:
            try:
                return int(text)
            except ValueError:
                raise CastFailureError(f"{column.name}: {text!r} is not a whole number") from None
        case TypeName.DECIMAL:
            try:
                return Decimal(text)
            except InvalidOperation:
                raise CastFailureError(f"{column.name}: {text!r} is not a decimal") from None
        case TypeName.BOOL:
            lowered = text.lower()
            if lowered in {"true", "t", "y", "yes", "1"}:
                return True
            if lowered in {"false", "f", "n", "no", "0"}:
                return False
            raise CastFailureError(f"{column.name}: {text!r} is not a boolean")
        case TypeName.DATE:
            return _cast_date(text, column)
        case TypeName.TIMESTAMP_UTC:
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                raise CastFailureError(f"{column.name}: {text!r} is not an ISO timestamp") from None
        case _:
            return text


def _cast_date(text: str, column: ContractColumn) -> date:
    for pattern in (_COMPACT_DATE, _ISO_DATE):
        if match := pattern.match(text):
            return _build_date(int(match[1]), int(match[2]), int(match[3]), text, column)
    if match := _US_DATE.match(text):
        return _build_date(int(match[3]), int(match[1]), int(match[2]), text, column)
    raise CastFailureError(
        f"{column.name}: {text!r} is not a recognised date (YYYYMMDD, YYYY-MM-DD or M/D/YYYY)"
    )


def _build_date(year: int, month: int, day: int, text: str, column: ContractColumn) -> date:
    try:
        parsed = date(year, month, day)
    except ValueError as exc:
        raise CastFailureError(f"{column.name}: {text!r} is not a real date ({exc})") from None
    # Incident #8: service months of 1000-01 and 1753-01 in the legacy estate.
    # A date that parses is not the same as a date that is possible.
    if not 1900 <= parsed.year <= 2100:
        raise CastFailureError(
            f"{column.name}: {text!r} is outside the plausible range 1900-2100 — "
            "legacy type debt, attributed rather than loaded"
        )
    return parsed


# ── data quality rules ───────────────────────────────────────────────────────
Predicate = Callable[[dict[str, object]], bool]


@dataclass(frozen=True)
class DqRule:
    """One approved rule. Plain-English description plus an executable check.

    The 110 legacy rules each already pair a natural-language description with
    executable SQL and a glossary link — which is exactly this shape, and
    exactly what CF-V1-E7-01's NL -> rule agent must produce.
    """

    rule_id: str
    name: str
    description: str
    severity: Severity
    columns: tuple[str, ...]
    predicate: Predicate = field(repr=False, default=lambda row: True)
    glossary_id: str | None = None

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.rule_id)

    def passes(self, row: dict[str, object]) -> bool:
        return self.predicate(row)


def not_null(
    rule_id: str,
    column: str,
    *,
    name: str,
    severity: Severity,
    description: str,
    glossary_id: str | None = None,
) -> DqRule:
    """The most common rule shape in the legacy set: Completeness / Mandatory.

    DQ-002 — "Member First Name Not Null" — is this, and it is the canonical
    quarantine reason throughout the stories.
    """
    return DqRule(
        rule_id=rule_id,
        name=name,
        description=description,
        severity=severity,
        columns=(column,),
        predicate=lambda row: str(row.get(column) or "").strip() != "",
        glossary_id=glossary_id,
    )


# ── contracts and rules as governed objects ──────────────────────────────────
#
# A contract and its rules travel the same lifecycle as a feed, for the same
# reason: the engine reads PUBLISHED metadata, so an unapproved contract cannot
# be enforced and an unapproved rule cannot quarantine anybody's rows. Storing
# them as governed objects is what makes "which contract version was this batch
# loaded against?" answerable a year later.


def contract_as_governed(
    contract: SchemaContract,
    *,
    author: Actor,
    created_ts: datetime | None = None,
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.CONTRACT,
        object_id=contract.feed_id,
        # The contract already carries its version. Accepting a second one here
        # would let a caller store v3's columns under v1's number.
        version=contract.version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body={
            "key_columns": list(contract.key_columns),
            "columns": [
                {
                    "name": column.name,
                    "type": column.type.value,
                    "nullable": column.nullable,
                    "source_name": column.source_name,
                    "is_phi": column.is_phi,
                    "precision": column.precision,
                    "scale": column.scale,
                    "date_formats": list(column.date_formats),
                }
                for column in contract.columns
            ],
        },
    )


def from_governed(obj: GovernedObject) -> SchemaContract:
    if obj.object_type is not ObjectType.CONTRACT:
        raise ValueError(f"{obj.object_type} is not a schema contract")
    return SchemaContract(
        feed_id=obj.object_id,
        version=obj.version,
        columns=tuple(
            ContractColumn(
                name=column["name"],
                type=TypeName(column["type"]),
                nullable=bool(column.get("nullable", True)),
                source_name=column.get("source_name"),
                is_phi=bool(column.get("is_phi", False)),
                precision=column.get("precision"),
                scale=column.get("scale"),
                date_formats=tuple(column.get("date_formats", ())),
            )
            for column in obj.body.get("columns", [])
        ),
        key_columns=tuple(obj.body.get("key_columns", ())),
    )


def rules_as_governed(
    feed_id: str,
    rules: Sequence[DqRule],
    *,
    author: Actor,
    version: int = 1,
    created_ts: datetime | None = None,
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.DQ_RULE,
        object_id=feed_id,
        version=version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body={
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "name": rule.name,
                    "description": rule.description,
                    "severity": rule.severity.value,
                    "columns": list(rule.columns),
                    "glossary_id": rule.glossary_id,
                }
                for rule in rules
            ]
        },
    )


def rules_from_governed(obj: GovernedObject) -> tuple[DqRule, ...]:
    """Rebuild the rules' METADATA. The predicate is deliberately not restored.

    A predicate is executable code; a governed object is data. Rehydrating a
    callable from a registry row would mean the registry could change what runs
    without anyone approving code — so the reconstructed rules describe
    themselves for explanation and citation, and the pipeline is handed real
    predicates by the compiler.
    """
    if obj.object_type is not ObjectType.DQ_RULE:
        raise ValueError(f"{obj.object_type} is not a rule set")
    return tuple(
        DqRule(
            rule_id=rule["rule_id"],
            name=rule.get("name", ""),
            description=rule.get("description", ""),
            severity=Severity(rule.get("severity", "low")),
            columns=tuple(rule.get("columns", ())),
            glossary_id=rule.get("glossary_id"),
        )
        for rule in obj.body.get("rules", [])
    )
