"""CF-V3-E8-05 — Silver Raw to Silver ODS: the load decisions, pure.

    "Apply history rules exactly as configured per entity (current-only
     updates vs full history with effective dates). Run business
     deduplication with the documented precedence rules, logging what was
     merged and why. Preserve source identifiers on every row alongside
     surrogate keys."
    "Given two sources assert different current addresses for one member on
     the same day, when business dedup runs, then the configured precedence
     applies, the losing value is retained in history (not discarded), and
     the conflict is logged for the data-quality trend."
    "Don't — Load a record whose identity is unresolved; those wait,
     visibly, for the exception process."
    — CF-V3-E8-05

WHY THIS IS PURE. The same split `core.identity` draws between "what leaves
core" and "what the outcome means": everything a reviewer needs to verify
about THIS story's own hard parts — which rows get skipped, updated, or
history-closed; who wins when two sources disagree; whether a surrogate key
is reused or minted — is a function of values already in hand, checkable
without a database. The worker (`workers.ods_load`) is the thin I/O shell
that reads those values and writes the decision; this module is where the
decision itself lives, and is provably correct the same way G2-G4's own
accounting is.

NO NEW LINEAGE OR MAPPING MACHINERY. Row-level transforms reuse
`core.mapping.apply_to` exactly as CF-V3-E10-02 reused `core.impact` and
`core.mapping`'s own body vocabulary rather than inventing a lineage graph —
this module supplies only what genuinely does not exist anywhere yet:
attaching the identity crosswalk's two identifiers to a row, change-detection
hashing, the current-only/effective-dated load decision, surrogate-key
assignment, and cross-source precedence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.registry.contract import ContractColumn, cast_value
from cinqflow.core.schema_spec import Column


class OdsLoadError(RuntimeError):
    """The ODS load stage refused something."""


class UnresolvedIdentityLoadedError(OdsLoadError):
    """A crosswalk entry whose identity did not resolve reached the loader.

    "A record whose identity is unresolved never loads" is enforced HERE, at
    the one seam every row must cross to become a canonical row — not
    trusted to every future caller's own filtering discipline. Filtering to
    `IdentityDisposition.loadable` upstream is still correct and expected;
    this is the backstop for the caller that forgets.
    """


def enrich_with_crosswalk(row: Mapping[str, str], entry: CrosswalkEntry) -> dict[str, str]:
    """One Silver Raw row, plus the two identifiers only the crosswalk
    knows — `internal_member_id` (`OurId`, legacy or empty) and
    `verato_person_id` (`LinkId`). The canonical mapping's lines for those
    two ODS columns read them back out under these exact keys."""
    if entry.outcome is not MatchOutcome.RESOLVED:
        raise UnresolvedIdentityLoadedError(
            f"{entry.source_system}:{entry.source_member_id} is {entry.outcome.value}, not "
            "resolved — a record whose identity is unresolved never loads."
        )
    return {
        **row,
        "_internal_member_id": entry.internal_member_id,
        "_verato_person_id": entry.verato_person_id or "",
    }


def compute_record_hash(values: Mapping[str, object], columns: Sequence[str]) -> str:
    """A deterministic change-detection hash over exactly the NAMED business
    columns, never the whole row — so a new audit column, or a column this
    entity does not carry, never invalidates every hash already stored.
    Matches `Members.RecordHash`'s own comment: "change-detection hash over
    every field" — "every field" meaning every declared business field."""
    material = "|".join(f"{column}={values.get(column)!r}" for column in columns)
    return hashlib.sha256(material.encode()).hexdigest()


def assign_surrogate_key(internal_member_id: str, mint: Callable[[], int]) -> int:
    """Reuse the legacy key a migrated member already carries; mint a fresh
    one only for a genuinely new member. `CrosswalkEntry.internal_member_id`
    "stays empty for a genuinely new member, never invented to fill it" —
    minting is what fills it, and only here, once, at load time."""
    return int(internal_member_id) if internal_member_id else mint()


def stringify_for_mapping(row: Mapping[str, object]) -> dict[str, str]:
    """Silver Raw values arrive already typed — a real `date`, not
    `"2026-08-31"` — but `core.mapping.apply` reads `dict[str, str]` and
    strips every value it touches. This is the one conversion between
    Silver Raw's typed columns and the mapping engine's textual contract,
    the same seam `core.compiler.execute._cast_and_map` crosses in the other
    direction (Bronze's raw strings, cast up TO Silver Raw's types)."""

    def one(value: object) -> str:
        if value is None:
            return ""
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)

    return {key: one(value) for key, value in row.items()}


def cast_mapped_value(raw: str, column: Column) -> object:
    """The mapped STRING, cast against the ODS column's declared type.

    Reuses `core.registry.contract.cast_value` — the platform's ONE date/
    bool/int/decimal parser, "19900101 and 01/01/1990 are the same date ...
    identically on both planes" — rather than a second definition of what a
    date string means. `ContractColumn` and `schema_spec.Column` are two
    names for the same three facts (`name`, `type`, `nullable`) plus
    precision/scale; this is the narrow adapter between them, not a new
    caster.
    """
    return cast_value(
        raw,
        ContractColumn(
            name=column.name,
            type=column.type,
            nullable=column.nullable,
            precision=column.precision,
            scale=column.scale,
            is_phi=column.is_phi,
        ),
    )


@unique
class LoadAction(StrEnum):
    """What the loader actually did to one row — logged, never inferred
    after the fact from a row count that could mean any of these."""

    INSERTED = "inserted"
    UPDATED = "updated"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    OPENED = "opened"
    CLOSED_AND_OPENED = "closed_and_opened"


def plan_current_only(existing_hash: str | None, new_hash: str) -> LoadAction:
    """SCD-1: update in place. `existing_hash=None` means the surrogate key
    has never been loaded before."""
    if existing_hash is None:
        return LoadAction.INSERTED
    if existing_hash == new_hash:
        return LoadAction.SKIPPED_UNCHANGED
    return LoadAction.UPDATED


def plan_effective_dated(open_hash: str | None, new_hash: str) -> LoadAction:
    """SCD-2: "an address change closes the old row and opens a new one" —
    `Members_Addresses`'s own comment, verbatim. `open_hash=None` means no
    row is currently open for this key."""
    if open_hash is None:
        return LoadAction.OPENED
    if open_hash == new_hash:
        return LoadAction.SKIPPED_UNCHANGED
    return LoadAction.CLOSED_AND_OPENED


@dataclass(frozen=True)
class SourceValue:
    """One source's assertion of one column's value."""

    source_system: str
    value: object


@dataclass(frozen=True)
class PrecedenceRule:
    """Which source wins when two disagree about one entity/column, most
    authoritative first. Declared as data — "the documented precedence
    rules" — never a per-column `if source == ...` scattered through the
    loader."""

    entity: str
    column: str
    source_priority: tuple[str, ...]


@dataclass(frozen=True)
class DedupConflict:
    """One place two sources disagreed, and which value won — the log entry
    the data-quality trend reads. The losing VALUE is named here, not just
    that a conflict occurred, because "the conflict is logged" without the
    value is a count, not evidence."""

    entity: str
    column: str
    winner_source: str
    winner_value: object
    loser_source: str
    loser_value: object

    def explain(self) -> str:
        return (
            f"{self.entity}.{self.column}: {self.winner_source}={self.winner_value!r} kept "
            f"over {self.loser_source}={self.loser_value!r} by configured precedence; the "
            "losing value is retained in history, not discarded."
        )


class NoPrecedenceRuleError(OdsLoadError):
    """Two sources disagree and nothing says which wins.

    "The configured precedence applies" presupposes one is configured — this
    refuses rather than picking arbitrarily, the same posture
    `UndecidedDiscrepancyError` takes on an unresolved model discrepancy.
    """


def resolve_precedence(
    entity: str,
    column: str,
    candidates: Sequence[SourceValue],
    rules: Sequence[PrecedenceRule],
) -> tuple[SourceValue, tuple[DedupConflict, ...]]:
    """Pick the winning value when one or more sources assert one for the
    same entity/column, and log every loser.

    A single source, or several sources agreeing, needs no rule at all —
    precedence only matters once there is an actual disagreement to break.
    """
    distinct_values = {candidate.value for candidate in candidates}
    if len(distinct_values) <= 1:
        return candidates[0], ()

    rule = next((r for r in rules if r.entity == entity and r.column == column), None)
    if rule is None:
        sources = ", ".join(sorted({c.source_system for c in candidates}))
        raise NoPrecedenceRuleError(
            f"{entity}.{column}: {sources} disagree and no precedence rule names an order."
        )

    def rank(candidate: SourceValue) -> int:
        if candidate.source_system in rule.source_priority:
            return rule.source_priority.index(candidate.source_system)
        return len(rule.source_priority)

    ranked = sorted(candidates, key=rank)
    winner = ranked[0]
    conflicts = tuple(
        DedupConflict(
            entity=entity,
            column=column,
            winner_source=winner.source_system,
            winner_value=winner.value,
            loser_source=candidate.source_system,
            loser_value=candidate.value,
        )
        for candidate in ranked[1:]
        if candidate.value != winner.value
    )
    return winner, conflicts
