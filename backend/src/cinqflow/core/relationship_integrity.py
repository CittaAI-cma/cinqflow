"""CF-V3-E10-03 — relationship validation, as data. G5's first check.

    "Validate relationships as data (orphaned claims, dangling provider
     references) with counts and examples."
    "Exception — Given 0.3% of claims reference members absent from the
     member table, when the gate runs, then publication holds, the
     orphaned claims are listed with their source batches, and the
     incident flow picks it up with the evidence attached."
    — CF-V3-E10-03

NO NEW FOREIGN-KEY METADATA ON `OdsEntity`. `satellite_of` already names a
satellite's parent (`Members_Addresses.satellite_of == "Members"`), but only
as a free string for humans — there is no structured child-column ->
parent-column declaration anywhere, and adding one to `core.registry.
ods_model` for a single caller would be new API surface on an
already-shipped, tested module for a fact this module can simply be TOLD.
So a relationship is named by the CALLER (`workers.ods_certification`, which
reads `OdsEntity.satellite_of` and its own knowledge of the convention that
a satellite's FK column shares its parent's surrogate key name) — the same
"keyed by name, never assumed" discipline `ports.ods_load` already follows.

WHY THIS IS PURE. The actual orphan rows are found by a LEFT JOIN a real
adapter runs (`OdsLoadPort.orphans`, CF-V3-E10-03) — I/O core may not do.
What belongs here is the one thing worth being provably correct about
without a database: given a count and the rows an adapter found, what does
the check's verdict and evidence text say — the same split
`core.certification` itself draws between `certify()` (pure policy) and
`_certification_checks()` (the I/O that gathers its inputs).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cinqflow.core.certification import Check, CheckKind
from cinqflow.core.citations import CitationId, CitationKind


@dataclass(frozen=True)
class Relationship:
    """One declared child -> parent relationship, named by whoever calls
    the gate — never assumed from `OdsEntity.satellite_of`, which is only a
    free string for humans (see this module's own docstring)."""

    child_entity: str
    child_column: str
    parent_entity: str
    parent_column: str


@dataclass(frozen=True)
class OrphanedRow:
    """One child row whose foreign key value matches no real parent row.

    `source_batch` travels with the row because "the orphaned claims are
    listed with their source batches" is the story's own words — an
    orphan attributed to no batch is a number nobody can act on.
    """

    key_value: object
    source_batch: str | None

    def line(self) -> str:
        batch = self.source_batch or "unknown batch"
        return f"{self.key_value!r} (from {batch})"


@dataclass(frozen=True)
class RelationshipCheckResult:
    """One declared relationship, checked against however many rows the
    child entity carries — "with counts and examples", never a bare
    pass/fail."""

    child_entity: str
    child_column: str
    parent_entity: str
    parent_column: str
    checked: int
    orphans: tuple[OrphanedRow, ...]
    #: How many orphan rows were sampled into `orphans` — the check may
    #: cap examples (`limit`) while still reporting the TRUE total count.
    orphan_count: int

    @property
    def passed(self) -> bool:
        return self.orphan_count == 0

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RECON, subject=self.child_entity)

    def evidence(self) -> str:
        relationship = (
            f"{self.child_entity}.{self.child_column} -> {self.parent_entity}.{self.parent_column}"
        )
        if self.passed:
            return f"{relationship}: {self.checked} row(s) checked, none orphaned"
        rate = self.orphan_count / self.checked if self.checked else 1.0
        examples = ", ".join(o.line() for o in self.orphans)
        shown = f" — e.g. {examples}" if examples else ""
        return (
            f"{relationship}: {self.orphan_count} of {self.checked} row(s) orphaned "
            f"({rate:.1%}){shown}"
        )

    def as_check(self) -> Check:
        """The `core.certification.Check` this relationship contributes.
        Handed to `certify()` beside BALANCE/RECONCILIATION/DROP_LEDGER —
        `certify()`'s own policy already fails certification on any
        completed check that did not pass, mandatory or not."""
        return Check(
            kind=CheckKind.RELATIONSHIP_INTEGRITY,
            passed=self.passed,
            evidence=self.evidence(),
            citation=self.citation,
        )


def check_relationship(
    *,
    child_entity: str,
    child_column: str,
    parent_entity: str,
    parent_column: str,
    checked: int,
    orphan_rows: Sequence[Mapping[str, object]],
    orphan_count: int | None = None,
) -> RelationshipCheckResult:
    """Build the result from what an adapter already found.

    `orphan_count` defaults to `len(orphan_rows)` for a caller that fetched
    every orphan; a caller that capped its query at a sample size passes the
    TRUE total separately, so the evidence never understates the problem it
    only partially fetched.
    """
    orphans = tuple(
        OrphanedRow(key_value=row.get(child_column), source_batch=_batch_of(row))
        for row in orphan_rows
    )
    return RelationshipCheckResult(
        child_entity=child_entity,
        child_column=child_column,
        parent_entity=parent_entity,
        parent_column=parent_column,
        checked=checked,
        orphans=orphans,
        orphan_count=orphan_count if orphan_count is not None else len(orphans),
    )


def _batch_of(row: Mapping[str, object]) -> str | None:
    value = row.get("BatchId")
    return str(value) if value is not None else None
