"""CF-V1-E7-01 — the rule as a SPEC, and why no model ever writes executable SQL.

    "NL rule authoring — plain English → SQL/PySpark + business-language
     explanation + confidence; both texts stored"
    "the 110 legacy rules are the labeled golden set, so accuracy is
     measurable from day one"
    — CF-V1-E7-01

    "one spec, two renderings, identical semantics"
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

THE STORY SAYS "plain English -> SQL/PySpark" AND THE MODEL PRODUCES NEITHER.

That is not a hedge, it is the design. A model asked to emit SQL that will run
against a healthcare estate is a model whose output is code, and code has to be
reviewed as code — by a steward whose job is knowing what a business term
means, not reading a dialect. Worse, it puts an executable string in a registry
row, which is the one shape this platform refuses everywhere else:
`rules_from_governed` already declines to rehydrate a predicate for exactly
this reason, and `core.mapping` carries no `expression` field for the same one.

So the model chooses a CHECK — a kind from a closed vocabulary, plus scalar
parameters — and the PLATFORM renders the SQL, the PySpark and the row
predicate from it. Three consequences, each of which is the point:

  1. INJECTION IS STRUCTURALLY IMPOSSIBLE. There is no path from model output
     to a query string. A `Check` cannot express `; DROP TABLE` because it
     cannot express anything but its own parameters, and every identifier is
     validated against `_IDENTIFIER` before it is rendered.
  2. THE THREE RENDERINGS AGREE BY CONSTRUCTION, which is plate 08's whole
     claim. A test asserts the local predicate and the rendered SQL classify
     the same rows the same way, on the same data.
  3. CF-V1-E7-04 BECOMES BUILDABLE. "Unsafe logic blocked from publication" is
     a filter over free-form SQL and a property of the type system over a
     closed check vocabulary. The unsupported rule is the one the model cannot
     express, and it lands in the technical-review queue by construction.

THE VOCABULARY IS HARVESTED FROM THE CLIENT'S 110 LEGACY RULES, whose sheet
already classifies every one of them: 30 Completeness, 30 Validity, 19
Consistency, 10 Timeliness, 10 Accuracy, 7 Integrity, 4 Uniqueness — and, under
those, 22 Mandatory Field, 13 Code Set, 7 Format, 7 Intra-Record, 7 Referential,
6 Range, and the rest. Those sub-dimensions ARE the check kinds; the eight below
cover them, and each one exists because a real rule needed it.

BOTH TEXTS ARE STORED, and the story is explicit about it. `stated` is what the
BA typed — their own words, kept verbatim, because it is what they will search
for and what the next person needs to know was meant. `explanation` is the
platform's rendering of what the check actually does. When those two disagree,
the rule is wrong, and keeping only one of them makes that undiscoverable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.registry.contract import Severity


class RuleError(RuntimeError):
    """A rule that could not be built, rendered or run."""


class UnsafeIdentifierError(RuleError):
    """A column or table name that is not an identifier.

    THE INJECTION BOUNDARY, and it is a type error rather than an escaping
    routine. Nothing in a rule is interpolated into SQL until it has passed
    here, and what passes is `[A-Za-z_][A-Za-z0-9_]*` — no quotes to escape, no
    semicolons to strip, no dialect to get subtly wrong.
    """


class UnsupportedRuleError(RuleError):
    """The rule cannot be expressed in the check vocabulary.

    A FIRST-CLASS OUTCOME, not a failure. CF-V1-E7-04 routes these to technical
    review: "never silent failure, never silent auto-apply". A platform that
    quietly approximated an inexpressible rule would be worse than one that
    said so.
    """


#: What may be interpolated into rendered SQL. Deliberately narrower than SQL's
#: own identifier grammar: the platform never needs a quoted identifier, and
#: accepting one would mean owning an escaping routine per dialect.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def safe_identifier(name: str) -> str:
    """The only door a name passes through on its way into a query."""
    if not _IDENTIFIER.fullmatch(name or ""):
        raise UnsafeIdentifierError(
            f"{name!r} is not an identifier. Column and table names are rendered into SQL, "
            "so they are checked rather than escaped — there is no quoting rule to get "
            "wrong if nothing but an identifier is ever accepted."
        )
    return name


@unique
class Dimension(StrEnum):
    """The client's own seven data-quality dimensions, from their rule sheet.

    Reporting, not execution — `Check` decides what runs. Kept because the
    client's 110 rules are already filed under these, and a platform that
    replaced their vocabulary with its own would make its own reports
    incomparable with the ones people already read.
    """

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    TIMELINESS = "timeliness"
    ACCURACY = "accuracy"
    INTEGRITY = "integrity"
    UNIQUENESS = "uniqueness"


@unique
class CheckKind(StrEnum):
    """What a rule actually tests. The whole vocabulary.

    Closed, and each member is here because a real rule in the client's sheet
    needed it — the counts are their sub-dimension tallies.
    """

    #: 22 "Mandatory Field" rules. DQ-002, the canonical quarantine reason.
    NOT_NULL = "not_null"
    #: 13 "Code Set" rules — a value from a finite published list.
    IN_SET = "in_set"
    #: 7 "Format" rules — an MBI, an NPI, a ZIP.
    MATCHES_PATTERN = "matches_pattern"
    #: 6 "Range" and 3 "Reasonableness" rules.
    BETWEEN = "between"
    #: 7 "Intra-Record" and the date-logic rules: discharge on or after
    #: admission, term date on or after effective date.
    COMPARE_COLUMNS = "compare_columns"
    #: 2 "Primary Key", 2 "Business Key", 4 Uniqueness.
    UNIQUE = "unique"
    #: 7 "Referential" rules — a claim's member must exist in the roster.
    EXISTS_IN = "exists_in"
    #: 5 "Currency", 2 "Staleness", 3 "SLA" — a date recent enough to be true.
    FRESHNESS = "freshness"

    @property
    def is_row_level(self) -> bool:
        """True when one row alone decides. `UNIQUE` needs the batch and
        `EXISTS_IN` needs another dataset, so neither can be a predicate — and
        pretending otherwise is how a preview reports a pass it never ran."""
        return self not in {CheckKind.UNIQUE, CheckKind.EXISTS_IN}


@unique
class Comparison(StrEnum):
    """The operators `COMPARE_COLUMNS` admits. Closed, like everything else."""

    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    EQ = "eq"
    NEQ = "neq"

    @property
    def sql(self) -> str:
        return {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "=", "neq": "<>"}[self.value]

    @property
    def words(self) -> str:
        return {
            "lt": "before",
            "lte": "on or before",
            "gt": "after",
            "gte": "on or after",
            "eq": "equal to",
            "neq": "different from",
        }[self.value]


#: Which parameters each kind REQUIRES and which it MAY carry. Data, like
#: `core.mapping._SHAPE`, so the rules are readable in one place and the
#: completeness test can enumerate them.
_SHAPE: dict[CheckKind, tuple[frozenset[str], frozenset[str]]] = {
    CheckKind.NOT_NULL: (frozenset(), frozenset()),
    CheckKind.IN_SET: (frozenset({"allowed"}), frozenset({"case_sensitive"})),
    CheckKind.MATCHES_PATTERN: (frozenset({"pattern"}), frozenset()),
    CheckKind.BETWEEN: (frozenset(), frozenset({"minimum", "maximum"})),
    CheckKind.COMPARE_COLUMNS: (frozenset({"other_column", "comparison"}), frozenset()),
    CheckKind.UNIQUE: (frozenset(), frozenset({"also_by"})),
    CheckKind.EXISTS_IN: (frozenset({"reference_table", "reference_column"}), frozenset()),
    CheckKind.FRESHNESS: (frozenset({"within_days"}), frozenset()),
}


@dataclass(frozen=True)
class Check:
    """What a rule tests, as parameters. NEVER as an expression.

    There is no `sql`, `expression` or `predicate` field, and a test asserts
    their absence. A model's output reaching this type cannot become a query.
    """

    kind: CheckKind
    column: str
    allowed: tuple[str, ...] = ()
    case_sensitive: bool = True
    pattern: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    other_column: str | None = None
    comparison: Comparison | None = None
    also_by: tuple[str, ...] = ()
    reference_table: str | None = None
    reference_column: str | None = None
    within_days: int | None = None

    def __post_init__(self) -> None:
        safe_identifier(self.column)
        for name in (self.other_column, self.reference_table, self.reference_column):
            if name is not None:
                safe_identifier(name)
        for name in self.also_by:
            safe_identifier(name)

        required, optional = _SHAPE[self.kind]
        present = {
            key
            for key in ("pattern", "minimum", "maximum", "other_column", "within_days")
            if getattr(self, key) is not None
        }
        if self.allowed:
            present.add("allowed")
        if self.comparison is not None:
            present.add("comparison")
        if self.also_by:
            present.add("also_by")
        if self.reference_table is not None:
            present.add("reference_table")
        if self.reference_column is not None:
            present.add("reference_column")
        if not self.case_sensitive:
            present.add("case_sensitive")

        if missing := required - present:
            raise RuleError(
                f"a {self.kind.value} check needs {', '.join(sorted(missing))} and none was "
                "given — a check that cannot run should not be storable"
            )
        if extra := present - required - optional:
            raise RuleError(
                f"a {self.kind.value} check has no use for {', '.join(sorted(extra))}. "
                "Carrying it would put a parameter on the review screen that changes nothing."
            )
        if self.kind is CheckKind.BETWEEN and self.minimum is None and self.maximum is None:
            raise RuleError(
                "a range check with neither a minimum nor a maximum passes everything — "
                "which is a rule that says nothing while looking like one that says something"
            )
        if self.kind is CheckKind.MATCHES_PATTERN and self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as bad:
                raise RuleError(f"{self.pattern!r} is not a usable pattern: {bad}") from None
        if self.kind is CheckKind.FRESHNESS and (self.within_days or 0) < 1:
            raise RuleError("a freshness window is at least one day")

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column this check reads. What the impact packet and the
        preview both need, and what a screen highlights."""
        names = [self.column, *self.also_by]
        if self.other_column:
            names.append(self.other_column)
        seen: dict[str, None] = {}
        for name in names:
            seen.setdefault(name, None)
        return tuple(seen)

    def explain(self) -> str:
        """THE PLATFORM'S OWN SENTENCE, in business language.

        Stored beside the BA's words rather than instead of them: when the two
        disagree, the rule is wrong, and keeping only one makes that
        undiscoverable. This one is generated from the check, so it cannot
        drift from what runs.
        """
        match self.kind:
            case CheckKind.NOT_NULL:
                return f"{self.column} must be present and not blank."
            case CheckKind.IN_SET:
                sensitivity = "" if self.case_sensitive else " (ignoring case)"
                listed = ", ".join(self.allowed[:8])
                more = f" and {len(self.allowed) - 8} more" if len(self.allowed) > 8 else ""
                return (
                    f"{self.column} must be one of {len(self.allowed)} allowed value(s)"
                    f"{sensitivity}: {listed}{more}."
                )
            case CheckKind.MATCHES_PATTERN:
                return f"{self.column} must match the shape {self.pattern}."
            case CheckKind.BETWEEN:
                if self.minimum is not None and self.maximum is not None:
                    return f"{self.column} must be between {self.minimum} and {self.maximum}."
                if self.minimum is not None:
                    return f"{self.column} must be at least {self.minimum}."
                return f"{self.column} must be at most {self.maximum}."
            case CheckKind.COMPARE_COLUMNS:
                how = self.comparison.words if self.comparison else "compared to"
                return f"{self.column} must be {how} {self.other_column}."
            case CheckKind.UNIQUE:
                across = (
                    f" within each combination of {', '.join(self.also_by)}"
                    if self.also_by
                    else " across the whole delivery"
                )
                return f"{self.column} must not repeat{across}."
            case CheckKind.EXISTS_IN:
                return (
                    f"Every {self.column} must already exist as "
                    f"{self.reference_table}.{self.reference_column}."
                )
            case CheckKind.FRESHNESS:
                return f"{self.column} must be within the last {self.within_days} days."


# ── the three renderings, from one spec ──────────────────────────────────────
#
# Plate 08's law. `render_sql` and `render_pyspark` produce the FAILING-ROW
# condition — what the client's own sheet does, whose every rule reads
# "SELECT ... WHERE <the thing that is wrong>" with "0 rows expected". Matching
# their shape means their 110 queries and these are comparable by eye.


def render_sql(check: Check, *, table: str) -> str:
    """The SQL that selects FAILING rows. Identifiers checked, values bound.

    Values are emitted as `?` placeholders rather than inlined, so even the
    parameters this platform controls never become query text. The bound values
    travel beside the query in `sql_parameters`.
    """
    column = safe_identifier(check.column)
    table = safe_identifier(table)
    condition, _ = _failing_condition(check, column, table=table)
    return f"SELECT * FROM {table} WHERE {condition}"  # noqa: S608 - identifiers validated above


def sql_parameters(check: Check) -> tuple[Any, ...]:
    """The values `render_sql`'s placeholders bind to, in order."""
    return _failing_condition(check, safe_identifier(check.column))[1]


def _failing_condition(
    check: Check, column: str, *, table: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """The condition selecting failing rows, and the values it binds.

    `table` is the SCOPE, and only a set-level check needs it: `UNIQUE` asks
    "does this value repeat *in this delivery*", which is a question no
    row-level predicate can phrase. It is optional here because
    `sql_parameters` wants the values and not the text — and where it is
    absent, the set-level arms return NO CONDITION rather than an unscoped
    one. That is deliberate. An unscoped `SELECT col GROUP BY col` is legal
    SQL, returns zero rows forever, and reports a duplicate-free delivery on
    a table full of duplicates: the one failure mode a data-quality rule must
    never have is passing silently.
    """
    match check.kind:
        case CheckKind.NOT_NULL:
            return f"{column} IS NULL OR TRIM({column}) = ''", ()
        case CheckKind.IN_SET:
            marks = ", ".join("?" for _ in check.allowed)
            if check.case_sensitive:
                return f"{column} IS NOT NULL AND {column} NOT IN ({marks})", check.allowed
            return (
                f"{column} IS NOT NULL AND LOWER({column}) NOT IN ({marks})",
                tuple(value.lower() for value in check.allowed),
            )
        case CheckKind.MATCHES_PATTERN:
            return f"{column} IS NOT NULL AND {column} !~ ?", (check.pattern,)
        case CheckKind.BETWEEN:
            parts, values = [], []
            if check.minimum is not None:
                parts.append(f"{column} < ?")
                values.append(check.minimum)
            if check.maximum is not None:
                parts.append(f"{column} > ?")
                values.append(check.maximum)
            return f"{column} IS NOT NULL AND ({' OR '.join(parts)})", tuple(values)
        case CheckKind.COMPARE_COLUMNS:
            other = safe_identifier(check.other_column or "")
            operator = check.comparison.sql if check.comparison else "="
            return (
                f"{column} IS NOT NULL AND {other} IS NOT NULL "
                f"AND NOT ({column} {operator} {other})",
                (),
            )
        case CheckKind.UNIQUE:
            if table is None:
                return "", ()
            keys = ", ".join(safe_identifier(name) for name in (column, *check.also_by))
            # Every name in this string has passed `safe_identifier`, and every
            # VALUE is a bound placeholder — there is no path from a model, a
            # payer or a registry row to query text. The suppression is the one
            # place that claim is made, so it is worth reading twice.
            #
            # Two things this shape gets right, both of which read as pedantry
            # until the rule is wrong in production:
            #
            #   the subquery names its own FROM — without it the statement
            #   still parses, `column` simply binds to the outer row, every
            #   group counts one, and the rule passes on every delivery;
            #
            #   the comparison is the WHOLE KEY as a row, not its first
            #   column — `line_no IN (SELECT line_no ... GROUP BY line_no,
            #   claim_id)` flags every row sharing a line number with some
            #   duplicated pair, which is a false accusation against data
            #   that is fine.
            return (
                f"({keys}) IN (SELECT {keys} FROM {table} "  # noqa: S608
                f"GROUP BY {keys} HAVING COUNT(*) > 1)",
                (),
            )
        case CheckKind.EXISTS_IN:
            reference = safe_identifier(check.reference_table or "")
            target = safe_identifier(check.reference_column or "")
            # identifiers validated by `safe_identifier` above; no values inlined
            exists = (
                f"{column} IS NOT NULL AND NOT EXISTS "  # noqa: S608
                f"(SELECT 1 FROM {reference} WHERE {reference}.{target} = {column})"
            )
            return exists, ()
        case CheckKind.FRESHNESS:
            return f"{column} IS NOT NULL AND {column} < ?", (f"-{check.within_days} days",)


def render_pyspark(check: Check) -> str:
    """The PySpark filter that selects FAILING rows. The SAME spec, rendered.

    Returned as source rather than a callable, deliberately: it is shown to an
    engineer on a screen and stored in the evidence pack, and it is never
    `eval`'d by this platform. The cluster adapter builds the real Column
    expression from the `Check` itself, not from this string.
    """
    column = safe_identifier(check.column)
    match check.kind:
        case CheckKind.NOT_NULL:
            return f"F.col('{column}').isNull() | (F.trim(F.col('{column}')) == '')"
        case CheckKind.IN_SET:
            values = list(
                check.allowed if check.case_sensitive else [v.lower() for v in check.allowed]
            )
            side = (
                f"F.lower(F.col('{column}'))" if not check.case_sensitive else f"F.col('{column}')"
            )
            return f"F.col('{column}').isNotNull() & ~{side}.isin({values!r})"
        case CheckKind.MATCHES_PATTERN:
            return f"F.col('{column}').isNotNull() & ~F.col('{column}').rlike({check.pattern!r})"
        case CheckKind.BETWEEN:
            parts = []
            if check.minimum is not None:
                parts.append(f"(F.col('{column}') < F.lit({check.minimum!r}))")
            if check.maximum is not None:
                parts.append(f"(F.col('{column}') > F.lit({check.maximum!r}))")
            return f"F.col('{column}').isNotNull() & ({' | '.join(parts)})"
        case CheckKind.COMPARE_COLUMNS:
            other = safe_identifier(check.other_column or "")
            operator = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "==", "neq": "!="}[
                (check.comparison or Comparison.EQ).value
            ]
            return (
                f"F.col('{column}').isNotNull() & F.col('{other}').isNotNull() & "
                f"~(F.col('{column}') {operator} F.col('{other}'))"
            )
        case CheckKind.UNIQUE:
            keys = [column, *check.also_by]
            return f"F.count('*').over(Window.partitionBy({keys!r})) > 1"
        case CheckKind.EXISTS_IN:
            return (
                f"~F.col('{column}').isin("
                f"[r.{check.reference_column} for r in {check.reference_table}.collect()])"
            )
        case CheckKind.FRESHNESS:
            return (
                f"F.col('{column}').isNotNull() & "
                f"(F.datediff(F.current_date(), F.col('{column}')) > {check.within_days})"
            )


# ── the local predicate — the third rendering, and the one tests run ─────────


def passes(check: Check, row: dict[str, Any], *, as_of: date | None = None) -> bool:
    """Does one row satisfy this check?

    A NULL PASSES EVERY CHECK BUT `NOT_NULL`, and that is deliberate. "Is this
    value in the allowed set?" has no answer for a value that is not there —
    conflating absence with invalidity means one missing field fails two rules,
    the drop ledger double-counts it, and the balance equation the whole
    pipeline rests on stops adding up. Absence is `NOT_NULL`'s question.
    """
    if not check.kind.is_row_level:
        raise RuleError(
            f"{check.kind.value} is not decided by one row — it needs the whole batch "
            f"({CheckKind.UNIQUE.value}) or another dataset ({CheckKind.EXISTS_IN.value}). "
            "Use `evaluate` over the sample instead."
        )
    raw = row.get(check.column)
    text = "" if raw is None else str(raw).strip()
    if not text:
        return check.kind is not CheckKind.NOT_NULL

    match check.kind:
        case CheckKind.NOT_NULL:
            return True
        case CheckKind.IN_SET:
            if check.case_sensitive:
                return text in check.allowed
            return text.lower() in {value.lower() for value in check.allowed}
        case CheckKind.MATCHES_PATTERN:
            return re.search(check.pattern or "", text) is not None
        case CheckKind.BETWEEN:
            return _within(text, check)
        case CheckKind.COMPARE_COLUMNS:
            other = row.get(check.other_column or "")
            other_text = "" if other is None else str(other).strip()
            if not other_text:
                return True
            return _compares(text, other_text, check.comparison or Comparison.EQ)
        case CheckKind.FRESHNESS:
            moment = _as_date(text)
            if moment is None:
                return True
            today = as_of or datetime.now(UTC).date()
            return (today - moment).days <= (check.within_days or 0)
        case _:  # pragma: no cover - guarded above
            return True


def _numeric(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _as_date(text: str) -> date | None:
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _within(text: str, check: Check) -> bool:
    """Compare as numbers when both sides are numeric, as dates when both are
    dates, and as text otherwise.

    The three-way choice is not fussiness: `BETWEEN 1 AND 10` must not put 9
    outside the range, and `BETWEEN 1900-01-01 AND 2100-01-01` must not compare
    a compact `19900101` as text against a hyphenated bound. A single textual
    comparison gets both of those wrong quietly.
    """
    numeric = _numeric(text)
    low_number = _numeric(check.minimum) if check.minimum is not None else None
    high_number = _numeric(check.maximum) if check.maximum is not None else None
    if (
        numeric is not None
        and (check.minimum is None or low_number is not None)
        and (check.maximum is None or high_number is not None)
    ):
        return (low_number is None or numeric >= low_number) and (
            high_number is None or numeric <= high_number
        )

    moment = _as_date(text)
    low_date = _as_date(check.minimum) if check.minimum is not None else None
    high_date = _as_date(check.maximum) if check.maximum is not None else None
    if (
        moment is not None
        and (check.minimum is None or low_date is not None)
        and (check.maximum is None or high_date is not None)
    ):
        return (low_date is None or moment >= low_date) and (
            high_date is None or moment <= high_date
        )

    return (check.minimum is None or text >= check.minimum) and (
        check.maximum is None or text <= check.maximum
    )


def _compares(left: str, right: str, how: Comparison) -> bool:
    """Order two values, choosing the comparison their SHAPES support.

    Both sides converted together or not at all: comparing a number against a
    date, or either against text, is a question with no right answer, and
    answering it anyway is how `discharge_date >= admission_date` silently
    passes for a row where one of them is `N/A`.
    """
    left_number, right_number = _numeric(left), _numeric(right)
    if left_number is not None and right_number is not None:
        return _ordered(left_number, right_number, how)

    left_date, right_date = _as_date(left), _as_date(right)
    if left_date is not None and right_date is not None:
        return _ordered(left_date, right_date, how)

    return _ordered(left, right, how)


def _ordered(first: Decimal | date | str, second: Decimal | date | str, how: Comparison) -> bool:
    match how:
        case Comparison.LT:
            return first < second  # type: ignore[operator]
        case Comparison.LTE:
            return first <= second  # type: ignore[operator]
        case Comparison.GT:
            return first > second  # type: ignore[operator]
        case Comparison.GTE:
            return first >= second  # type: ignore[operator]
        case Comparison.EQ:
            return first == second
        case Comparison.NEQ:
            return first != second


# ── the rule ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleSpec:
    """One rule: what a person said, what the platform checks, and both texts.

    `stated` and `explanation` are BOTH kept, which the story asks for in as
    many words. The first is the BA's own sentence — what they will search for,
    and what tells the next person what was meant. The second is generated from
    the check, so it cannot drift from what runs. Where they disagree, the rule
    is wrong; keeping one of them makes that undiscoverable.
    """

    rule_id: str
    name: str
    #: The BA's own words, verbatim. Never rewritten by the platform.
    stated: str
    check: Check
    dimension: Dimension = Dimension.VALIDITY
    #: PROPOSED, not bound. CF-V1-E7-03 owns severity, the execution layer and
    #: the thresholds; a rule arrives here suggesting one and a steward decides.
    proposed_severity: Severity = Severity.MEDIUM
    glossary_id: str | None = None
    confidence: float | None = None
    rationale: str = ""
    citations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.stated.strip():
            raise RuleError(
                f"{self.rule_id}: a rule with no stated intent cannot be reviewed — the "
                "person approving it has only the platform's own paraphrase to go on"
            )

    @property
    def explanation(self) -> str:
        return self.check.explain()

    @property
    def columns(self) -> tuple[str, ...]:
        return self.check.columns

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.rule_id)

    def sql(self, *, table: str) -> str:
        return render_sql(self.check, table=table)

    def pyspark(self) -> str:
        return render_pyspark(self.check)


def rule_as_governed(
    feed_id: str,
    rules: tuple[RuleSpec, ...],
    *,
    author: Actor,
    version: int = 1,
    created_ts: datetime | None = None,
    contract_version: int | None = None,
    business_consumers: tuple[str, ...] = (),
) -> GovernedObject:
    """A rule set as a governed object, routed to the DATA STEWARD.

    The body keys are the ones `core.impact.REFERENCES` already declares for a
    DQ_RULE — `feed_id`, `contract_id`, `glossary_ids` — so lineage works the
    moment the object is stored.
    """
    return GovernedObject(
        object_type=ObjectType.DQ_RULE,
        object_id=feed_id,
        version=version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body=rule_body(
            feed_id,
            rules,
            contract_version=contract_version,
            business_consumers=business_consumers,
        ),
    )


def rule_body(
    feed_id: str,
    rules: tuple[RuleSpec, ...],
    *,
    contract_version: int | None = None,
    business_consumers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The governed body without the envelope — see `mapping_body` for why."""
    return {
        "feed_id": feed_id,
        "contract_id": feed_id if contract_version else None,
        "contract_version": contract_version,
        "glossary_ids": sorted({r.glossary_id for r in rules if r.glossary_id}),
        "business_consumers": list(business_consumers),
        "rules": [rule_to_dict(rule) for rule in rules],
    }


def rules_from_governed(obj: GovernedObject) -> tuple[RuleSpec, ...]:
    if obj.object_type is not ObjectType.DQ_RULE:
        raise RuleError(f"{obj.object_type} is not a rule set")
    return tuple(rule_from_dict(raw) for raw in obj.body.get("rules", ()))


def rule_to_dict(rule: RuleSpec) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "name": rule.name,
        "stated": rule.stated,
        "check": check_to_dict(rule.check),
        "dimension": rule.dimension.value,
        "proposed_severity": rule.proposed_severity.value,
        "glossary_id": rule.glossary_id,
        "confidence": rule.confidence,
        "rationale": rule.rationale,
        "citations": list(rule.citations),
        # DERIVED, and written anyway: a reader of the stored JSON, and a
        # steward's report counting rules by what they check, should not have
        # to re-implement `explain`.
        "explanation": rule.explanation,
        "columns": list(rule.columns),
    }


def rule_from_dict(raw: dict[str, Any]) -> RuleSpec:
    return RuleSpec(
        rule_id=str(raw.get("rule_id", "")),
        name=str(raw.get("name", "")),
        stated=str(raw.get("stated", "")),
        check=check_from_dict(raw.get("check") or {}),
        dimension=Dimension(raw.get("dimension", Dimension.VALIDITY.value)),
        proposed_severity=Severity(raw.get("proposed_severity", Severity.MEDIUM.value)),
        glossary_id=raw.get("glossary_id"),
        confidence=raw.get("confidence"),
        rationale=str(raw.get("rationale", "")),
        citations=tuple(raw.get("citations", ())),
    )


def check_to_dict(check: Check) -> dict[str, Any]:
    return {
        "kind": check.kind.value,
        "column": check.column,
        "allowed": list(check.allowed),
        "case_sensitive": check.case_sensitive,
        "pattern": check.pattern,
        "minimum": check.minimum,
        "maximum": check.maximum,
        "other_column": check.other_column,
        "comparison": check.comparison.value if check.comparison else None,
        "also_by": list(check.also_by),
        "reference_table": check.reference_table,
        "reference_column": check.reference_column,
        "within_days": check.within_days,
    }


def check_from_dict(raw: dict[str, Any]) -> Check:
    comparison = raw.get("comparison")
    return Check(
        kind=CheckKind(raw.get("kind", CheckKind.NOT_NULL.value)),
        column=str(raw.get("column", "")),
        allowed=tuple(str(v) for v in raw.get("allowed", ())),
        case_sensitive=bool(raw.get("case_sensitive", True)),
        pattern=raw.get("pattern"),
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
        other_column=raw.get("other_column"),
        comparison=Comparison(comparison) if comparison else None,
        also_by=tuple(str(v) for v in raw.get("also_by", ())),
        reference_table=raw.get("reference_table"),
        reference_column=raw.get("reference_column"),
        within_days=raw.get("within_days"),
    )


#: Re-exported so callers need not reach into `core.registry.contract` for the
#: severity vocabulary a rule proposes.
__all__ = [
    "Check",
    "CheckKind",
    "Comparison",
    "Dimension",
    "RuleError",
    "RuleSpec",
    "Severity",
    "UnsafeIdentifierError",
    "UnsupportedRuleError",
    "check_from_dict",
    "check_to_dict",
    "passes",
    "render_pyspark",
    "render_sql",
    "rule_as_governed",
    "rule_body",
    "rule_from_dict",
    "rule_to_dict",
    "rules_from_governed",
    "safe_identifier",
    "sql_parameters",
]
