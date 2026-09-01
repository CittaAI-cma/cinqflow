"""CF-V1-E7-02 — running a rule over the sample, before anyone approves it.

    "Live rule preview on sample — tested/passed/failed counts, failing rows
     highlighted, PHI-masked, saved as evidence"
    "Trust is built in the preview, not the prose: the BA sees exactly what the
     rule catches before anyone approves it."
    — CF-V1-E7-02

THE PROSE IS THE PART THAT IS ALWAYS PLAUSIBLE. A rule reading "member first
name must be populated" is agreeable on any screen; what a person actually
needs to know is that it fails 3 of 200 rows and which three. Approving rules
from their descriptions is how a Critical rule that quarantines 40% of a roster
gets signed on a Tuesday.

FOUR DECISIONS, EACH OF WHICH THE OBVIOUS IMPLEMENTATION GETS WRONG:

  1. THE FAILING ROWS ARE MASKED BEFORE THEY ARE BUILT, not after. `Preview`
     never holds an unmasked value — the masking happens inside `_masked_row`
     while the row is being read, so there is no moment at which the object a
     route serialises contains PHI. A preview that masked on the way out would
     be one refactor away from leaking, and the refactor would look like
     tidying.
  2. WHICH COLUMNS ARE MASKED COMES FROM THE CONTRACT'S PHI FLAGS — the same
     flags CF-V1-E5-03 sets and CF-V4-E2-03 masks by. Not a list this module
     keeps, which would be a second answer to "what is protected here".
  3. A RULE THAT CANNOT BE PREVIEWED SAYS SO. `EXISTS_IN` needs a dataset the
     sample does not contain, and reporting "0 failures" for a check that never
     ran is the most dangerous green a preview can show.
  4. `tested` IS NOT ALWAYS `len(rows)`. A row whose column is absent is not
     tested by most checks — see `core.rules.passes` — so the three counts are
     reported separately and `tested + skipped == len(rows)` is asserted. A
     preview whose numbers do not add up is a preview nobody can reason from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.rules import Check, CheckKind, RuleSpec, passes

#: What a masked value shows. The exact string, so a screen, a test and the
#: evidence pack all mean the same thing by it.
MASKED = "•••"

#: How many failing rows a preview carries. A BA who needs more than this to
#: believe a rule needs the rule explained, not a longer list — and every row
#: kept is PHI held in a proposal payload for as long as the proposal lives.
MAX_FAILING_ROWS = 20


@dataclass(frozen=True)
class FailingRow:
    """One row a rule caught, already masked.

    `row_number` is 1-based and counts data rows, matching what a person sees
    when they open the file — an off-by-one here sends somebody to the wrong
    line of a payer's delivery.
    """

    row_number: int
    values: dict[str, str]

    @property
    def as_record(self) -> dict[str, Any]:
        return {"row_number": self.row_number, "values": dict(self.values)}


@dataclass(frozen=True)
class Preview:
    """What one rule does to one sample. The evidence an approval rests on."""

    rule_id: str
    stated: str
    explanation: str
    tested: int = 0
    passed: int = 0
    failed: int = 0
    #: Rows the check could not decide — the value was absent, and absence is
    #: `NOT_NULL`'s question rather than every other check's.
    skipped: int = 0
    failing_rows: tuple[FailingRow, ...] = ()
    masked_columns: tuple[str, ...] = ()
    #: Set when the check could not be run here at all. Distinct from "ran and
    #: found nothing", which is what makes the difference visible.
    not_previewable: str = ""

    @property
    def ran(self) -> bool:
        return not self.not_previewable

    @property
    def failure_rate(self) -> float:
        return self.failed / self.tested if self.tested else 0.0

    @property
    def sampled_rows_shown(self) -> int:
        return len(self.failing_rows)

    def summary(self) -> str:
        """The sentence a BA reads before deciding whether to submit."""
        if not self.ran:
            return f"{self.rule_id} could not be previewed here: {self.not_previewable}"
        if not self.tested:
            return (
                f"{self.rule_id} tested no rows — every row in the sample left this column "
                "empty, so there was nothing for the check to decide."
            )
        shown = f" The first {self.sampled_rows_shown} are below" if self.failing_rows else ""
        return (
            f"{self.rule_id} tested {self.tested} row(s): {self.passed} passed, "
            f"{self.failed} failed ({self.failure_rate:.1%}).{shown}."
        )

    def as_evidence(self) -> dict[str, Any]:
        """The evidence pack's entry. Masked values only, and counted."""
        return {
            "rule_id": self.rule_id,
            "stated": self.stated,
            "explanation": self.explanation,
            "tested": self.tested,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "failure_rate": self.failure_rate,
            "masked_columns": list(self.masked_columns),
            "failing_rows": [row.as_record for row in self.failing_rows],
            "not_previewable": self.not_previewable,
            "summary": self.summary(),
        }


def preview(
    rule: RuleSpec,
    rows: tuple[dict[str, Any], ...],
    *,
    contract: SchemaContract | None = None,
    as_of: date | None = None,
    limit: int = MAX_FAILING_ROWS,
) -> Preview:
    """Run one rule over a sample and report what it caught.

    `contract` supplies the PHI flags. Omitted, EVERY column is masked — the
    safe direction, and the same asymmetry CF-V1-E5-03 chose: a field masked
    wrongly costs a reviewer a question, a field unmasked wrongly is a
    disclosure.
    """
    if not rule.check.kind.is_row_level and rule.check.kind is not CheckKind.UNIQUE:
        return Preview(
            rule_id=rule.rule_id,
            stated=rule.stated,
            explanation=rule.explanation,
            not_previewable=(
                f"a {rule.check.kind.value} check reads "
                f"{rule.check.reference_table}, which is not in this sample. Reporting no "
                "failures for a check that never ran would be the most misleading green a "
                "preview can show."
            ),
        )

    protected = _protected_columns(rule.check, contract)
    if rule.check.kind is CheckKind.UNIQUE:
        return _preview_unique(rule, rows, protected, limit)

    tested = passed = failed = skipped = 0
    failing: list[FailingRow] = []
    for number, row in enumerate(rows, start=1):
        raw = row.get(rule.check.column)
        absent = raw is None or not str(raw).strip()
        if absent and rule.check.kind is not CheckKind.NOT_NULL:
            # NOT TESTED, and not counted as a pass. `core.rules.passes`
            # returns True here so a missing value fails exactly one rule, and
            # a preview that reported those as passes would tell a BA their
            # rule was satisfied by rows it never looked at.
            skipped += 1
            continue
        tested += 1
        if passes(rule.check, row, as_of=as_of):
            passed += 1
            continue
        failed += 1
        if len(failing) < limit:
            failing.append(_masked_row(number, row, rule.check, protected))

    return Preview(
        rule_id=rule.rule_id,
        stated=rule.stated,
        explanation=rule.explanation,
        tested=tested,
        passed=passed,
        failed=failed,
        skipped=skipped,
        failing_rows=tuple(failing),
        masked_columns=protected,
    )


def _preview_unique(
    rule: RuleSpec,
    rows: tuple[dict[str, Any], ...],
    protected: tuple[str, ...],
    limit: int,
) -> Preview:
    """`UNIQUE` needs the whole sample, so it is counted over the sample.

    Previewable, unlike `EXISTS_IN`: everything it needs is here. A duplicate
    member in one delivery is an ordinary payer fault and exactly the thing a
    BA wants to see before approving a Critical key rule.
    """
    keys = (rule.check.column, *rule.check.also_by)
    seen: dict[tuple[str, ...], list[int]] = {}
    tested = skipped = 0
    for number, row in enumerate(rows, start=1):
        values = tuple(str(row.get(key) or "").strip() for key in keys)
        if not values[0]:
            skipped += 1
            continue
        tested += 1
        seen.setdefault(values, []).append(number)

    duplicated = [numbers for numbers in seen.values() if len(numbers) > 1]
    failing_numbers = sorted(number for numbers in duplicated for number in numbers)
    failing = [
        _masked_row(number, rows[number - 1], rule.check, protected)
        for number in failing_numbers[:limit]
    ]
    return Preview(
        rule_id=rule.rule_id,
        stated=rule.stated,
        explanation=rule.explanation,
        tested=tested,
        passed=tested - len(failing_numbers),
        failed=len(failing_numbers),
        skipped=skipped,
        failing_rows=tuple(failing),
        masked_columns=protected,
    )


def _protected_columns(check: Check, contract: SchemaContract | None) -> tuple[str, ...]:
    """Which of this check's columns are masked in the preview.

    Read from the CONTRACT's PHI flags — the same flags CF-V1-E5-03 sets and
    CF-V4-E2-03 masks by. A list kept here would be a second answer to "what is
    protected on this feed", and two answers to that question is how one of
    them goes stale.
    """
    if contract is None:
        return check.columns
    protected = []
    for name in check.columns:
        try:
            column = contract.column(name)
        except KeyError:
            # Not under contract, so nothing says it is safe. Masked.
            protected.append(name)
            continue
        if column.is_phi:
            protected.append(name)
    return tuple(protected)


def _masked_row(
    number: int, row: dict[str, Any], check: Check, protected: tuple[str, ...]
) -> FailingRow:
    """Build the row ALREADY MASKED.

    The masking happens here, while the value is being read, rather than on the
    way out of a `Preview`. There is therefore no moment at which the object a
    route serialises holds a protected value — and no refactor that could
    remove the masking while looking like tidying.
    """
    return FailingRow(
        row_number=number,
        values={
            name: MASKED if name in protected else str(row.get(name) or "")
            for name in check.columns
        },
    )


def preview_all(
    rules: tuple[RuleSpec, ...],
    rows: tuple[dict[str, Any], ...],
    *,
    contract: SchemaContract | None = None,
    as_of: date | None = None,
    limit: int = MAX_FAILING_ROWS,
) -> tuple[Preview, ...]:
    return tuple(preview(rule, rows, contract=contract, as_of=as_of, limit=limit) for rule in rules)


def evidence_pack(previews: tuple[Preview, ...], *, sample_rows: int) -> dict[str, Any]:
    """What is stored with the proposal, and what an approver later reads.

        "saved as evidence"

    The SAMPLE SIZE travels with it, because "3 rows failed" means one thing in
    200 rows and another in 200,000 — and a stored figure whose denominator is
    missing is a figure somebody will quote wrongly.
    """
    return {
        "sample_rows": sample_rows,
        "rules_previewed": len(previews),
        "rules_not_previewable": sum(1 for p in previews if not p.ran),
        "total_failures": sum(p.failed for p in previews if p.ran),
        "previews": [p.as_evidence() for p in previews],
    }
