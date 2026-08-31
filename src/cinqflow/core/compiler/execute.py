"""Execute a compiled plan against parsed rows. Pure, and engine-free.

This is the part of the pipeline that decides WHAT HAPPENS TO EACH ROW, and it
lives in core/ so that both compute renderings behave identically:

    "golden pipelines compare OUTPUT DATA across engines, never generated code"
    — docs/architecture/INVARIANTS.md, data plane

If the decision to quarantine a row lived in the pg-compute renderer, the
Databricks renderer would have to re-implement it, and the cross-engine golden
comparison would be comparing two implementations of a judgement rather than
two renderings of one plan.

So the split is: core decides, the adapter WRITES. `apply` returns the rows
that survived, the rows that were quarantined and the reason for every one —
and the adapter's only job is to put them where they go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinqflow.core.compiler.plan import LogicalPlan, StepKind
from cinqflow.core.mapping import FeedMapping
from cinqflow.core.model.vocabulary import ErrorCategory, Layer
from cinqflow.core.recon import DropReason, StageReconciliation
from cinqflow.core.registry.contract import (
    CastFailureError,
    DqRule,
    SchemaContract,
    cast_value,
)


@dataclass(frozen=True)
class QuarantinedRow:
    """One excluded row, with the rule that excluded it and the column it read.

    `row` is carried because quarantine STORAGE holds it — that is what makes
    "reprocess only the failed records" possible. It is never carried into a
    SUMMARY, and no certified query tool can reach it.
    """

    row_number: int
    rule_id: str
    reason: str
    columns: tuple[str, ...]
    row: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    """What the plan did to the data, ready for the adapter to write."""

    loaded: tuple[dict[str, Any], ...]
    quarantined: tuple[QuarantinedRow, ...]
    reconciliation: StageReconciliation
    warnings: tuple[QuarantinedRow, ...] = ()

    @property
    def balances(self) -> bool:
        return self.reconciliation.balances


def apply(
    plan: LogicalPlan,
    *,
    rows: list[dict[str, str]],
    contract: SchemaContract,
    rules: tuple[DqRule, ...],
    batch_id: str,
    reads_as: dict[str, str] | None = None,
    mapping: FeedMapping | None = None,
) -> ExecutionResult:
    """Run the cast -> map -> evaluate_rules steps over parsed rows.

    Every exclusion is attributed as it happens, so the balance equation is a
    consequence of how rows are processed rather than something checked
    afterwards and hoped for.

    `reads_as` is CF-V2-E5-04's settled renames — contract spelling to the
    spelling that actually arrived, each one backed by a glossary term. It
    aliases THIS READ and nothing else: the contract is not modified, and the
    rename that produced the alias also produced a draft contract v(n+1) for
    a steward. Refusing to read a spelling the glossary itself settled would
    null a contracted column and quarantine a perfectly good file.

    `mapping` is W1-30: the feed's PUBLISHED `FeedMapping`, when it has one.
    `None` is the common case today — nothing publishes a mapping
    automatically — and it means the MAP step runs exactly as it always has,
    the bare rename `ContractColumn.reads_from` declares. A feed that has
    never had a mapping published cannot tell the difference.
    """
    steps = set(plan.step_kinds)
    loaded: list[dict[str, Any]] = []
    quarantined: list[QuarantinedRow] = []
    warnings: list[QuarantinedRow] = []
    seen_keys: set[tuple[Any, ...]] = set()

    for row_number, raw in enumerate(rows, start=1):
        mapped, failure = _cast_and_map(
            raw,
            contract,
            row_number,
            run_cast=StepKind.CAST in steps,
            reads_as=reads_as,
            mapping=mapping,
        )
        if failure is not None:
            quarantined.append(failure)
            continue

        duplicate = _duplicate_key(mapped, contract, seen_keys, row_number, raw)
        if duplicate is not None:
            quarantined.append(duplicate)
            continue

        if StepKind.EVALUATE_RULES in steps:
            broken = _first_broken_rule(mapped, rules, row_number, raw)
            if broken is not None:
                if broken.severity_quarantines:
                    quarantined.append(broken.record)
                    continue
                warnings.append(broken.record)

        loaded.append(mapped)

    drops = _ledger(quarantined)
    return ExecutionResult(
        loaded=tuple(loaded),
        quarantined=tuple(quarantined),
        warnings=tuple(warnings),
        reconciliation=StageReconciliation(
            batch_id=batch_id,
            stage=Layer.SILVER_RAW,
            records_in=len(rows),
            records_out=len(loaded),
            # Every excluded row is attributed to its rule, so `quarantined`
            # stays zero and the ledger carries the whole difference. An
            # unattributed quarantine count would be a drop with no reason.
            quarantined=0,
            drops=drops,
        ),
    )


def _duplicate_key(
    mapped: dict[str, Any],
    contract: SchemaContract,
    seen: set[tuple[Any, ...]],
    row_number: int,
    raw: dict[str, str],
) -> QuarantinedRow | None:
    """A record whose key has already appeared in this batch.

    "dedup precedence logged; losing value retained in history" — FIG 10.
    First occurrence wins, and the loser is an ATTRIBUTED drop: it is in
    Bronze, it is in quarantine with its reason, and it is in the ledger. What
    it is not is a crash, which is what a bare unique constraint would give —
    one duplicated member failing a 22,000-row roster.
    """
    if not contract.key_columns:
        return None
    key = tuple(mapped.get(column) for column in contract.key_columns)
    if key in seen:
        return QuarantinedRow(
            row_number=row_number,
            rule_id="DUPLICATE-" + "-".join(contract.key_columns),
            reason=(
                f"{', '.join(contract.key_columns)} "
                f"{', '.join(str(v) for v in key)} already appears in this batch"
            ),
            columns=contract.key_columns,
            row=dict(raw),
        )
    seen.add(key)
    return None


@dataclass(frozen=True)
class _BrokenRule:
    record: QuarantinedRow
    severity_quarantines: bool


def _cast_and_map(
    raw: dict[str, str],
    contract: SchemaContract,
    row_number: int,
    *,
    run_cast: bool,
    reads_as: dict[str, str] | None = None,
    mapping: FeedMapping | None = None,
) -> tuple[dict[str, Any], QuarantinedRow | None]:
    """Cast to contracted types and rename to canonical names, in one pass.

    Two ways to get from a raw value to a canonical one:

      • no published mapping (`mapping is None`, still the common case): the
        bare rename `ContractColumn.reads_from` declares. Wave 0's behaviour,
        untouched by W1-30.
      • a PUBLISHED `FeedMapping` (W1-30): every value comes from
        `FeedMapping.apply_to` instead — the transform taxonomy the mapping
        studio writes (cast, split, concat, lookup, conditional, constant)
        finally governs a running batch, not only its own unit test.

    Casting against the CONTRACT's declared type happens HERE either way.
    `core.mapping.apply` deliberately returns strings — its own docstring
    says `cast_value` already owns type conversion for the whole platform —
    so a mapping says WHICH string populates a field, and the contract still
    says how to parse it.
    """
    aliases = reads_as or {}
    transformed: dict[str, object] = {}
    if mapping is not None:
        # `reads_as` aliases are applied to the RAW row's keys BEFORE
        # `apply_to` runs, not after: a mapping line is authored against the
        # contract's own spelling, and a settled rename this run must not
        # make that spelling go missing from underneath it.
        aliased_row = _alias_for_mapping(raw, aliases)
        transformed, rejecting_line = mapping.apply_to(aliased_row)
        if rejecting_line is not None:
            return {}, QuarantinedRow(
                row_number=row_number,
                # Attributed to the MAPPING LINE that refused the row, not to
                # a DQ rule or the contract — it is the line's own null or
                # unlisted-code policy that made this call.
                rule_id=f"MAPPING-{rejecting_line.address}",
                reason=rejecting_line.describe(),
                columns=rejecting_line.source_columns,
                row=dict(raw),
            )

    mapped: dict[str, Any] = {}
    for column in contract.columns:
        if mapping is not None:
            found = transformed.get(column.name)
            source_value = "" if found is None else str(found)
        else:
            source_value = raw.get(aliases.get(column.reads_from, column.reads_from), "")
        if not run_cast:
            mapped[column.name] = source_value.strip() or None
            continue
        try:
            mapped[column.name] = cast_value(source_value, column)
        except CastFailureError as exc:
            return {}, QuarantinedRow(
                row_number=row_number,
                # A cast failure is attributed to the CONTRACT, not to a DQ
                # rule — it is the contract that declared the type.
                rule_id=f"CAST-{column.name}",
                reason=str(exc),
                columns=(column.name,),
                row=dict(raw),
            )
    return mapped, None


def _alias_for_mapping(raw: dict[str, str], reads_as: dict[str, str]) -> dict[str, str]:
    """The raw row, with every settled rename's CONTRACT spelling also
    populated from the spelling that actually arrived this run.

    A `FeedMapping` line is authored against the published contract's own
    vocabulary — the spelling a BA sees in the mapping studio — never against
    whatever name a payer happens to send on a given delivery. Applying
    `reads_as` here, before `apply_to` runs, means a settled rename does not
    have to be adopted into the contract before a mapping line written in the
    contract's spelling keeps reading a real value.
    """
    if not reads_as:
        return raw
    aliased = dict(raw)
    for was, now in reads_as.items():
        if now in raw:
            aliased[was] = raw[now]
    return aliased


def _first_broken_rule(
    mapped: dict[str, Any], rules: tuple[DqRule, ...], row_number: int, raw: dict[str, str]
) -> _BrokenRule | None:
    """The FIRST failing rule wins, and the row is attributed to it.

    Deliberately not "all failing rules": a row can only be dropped once, and
    the drop ledger must add up. Attributing one row to three rules would
    triple-count it and break the balance equation — the exact failure the
    ledger exists to prevent.
    """
    for rule in rules:
        if not rule.passes(mapped):
            return _BrokenRule(
                record=QuarantinedRow(
                    row_number=row_number,
                    rule_id=rule.rule_id,
                    reason=rule.name,
                    columns=rule.columns,
                    row=dict(raw),
                ),
                severity_quarantines=rule.severity.quarantines,
            )
    return None


def _ledger(quarantined: list[QuarantinedRow]) -> tuple[DropReason, ...]:
    """Group exclusions by rule. Every entry names something."""
    grouped: dict[tuple[str, str], list[QuarantinedRow]] = {}
    for record in quarantined:
        grouped.setdefault((record.rule_id, record.reason), []).append(record)
    return tuple(
        DropReason(
            rule_id=rule_id,
            reason=reason,
            record_count=len(records),
            columns=records[0].columns,
        )
        for (rule_id, reason), records in sorted(grouped.items())
    )


def error_category_for(rule_id: str) -> ErrorCategory:
    """Which control-plane error category a rule failure belongs to."""
    if rule_id.startswith(("CAST-", "MAPPING-")):
        return ErrorCategory.TRANSFORMATION
    return ErrorCategory.VALIDATION
