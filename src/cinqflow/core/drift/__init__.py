"""CF-V2-E5-04 — drift classified by what it MEANS, not by what changed.

    "differences classified by what they mean — compatible rename
     (DOB → date_of_birth), additive new column, or a genuine breaking
     change — plus the impact on mappings and rules"

    "Never block ingestion on compatible drift — log it and propose the
     contract update."
    "Auto-modify a contract — even a compatible rename becomes a proposed new
     contract version for approval." — the documented don't

COMPATIBLE-RENAME DETECTION IS A GLOSSARY LOOKUP, NOT A MODEL CALL. The
glossary already records every spelling a concept has arrived under —
`BG-004` carries `Date_of_Birth; Patient_dob; MemberDateOfBirth` — so "the
column that vanished and the column that appeared are the same concept" is a
question the platform answers deterministically, with the term as evidence.
Structure sees two events; the glossary sees one rename.

THE PAIRING MUST BE UNIQUE IN BOTH DIRECTIONS, or it is not a rename. A
removed column whose concept matches two arriving columns — or an arriving
column that could stand in for two removed ones — is genuine ambiguity, and
the platform NEVER GUESSES SILENTLY (the profiler refused to type `19360201`
for the same reason). Ambiguity stays a breaking finding with both
candidates named, for a person.

WHAT A RENAME BUYS, AND WHAT IT DOES NOT. A classified rename lets THIS run
read the new spelling — refusing to would null a contracted column the
glossary itself says arrived, quarantining a perfectly good file. It does
NOT touch the contract: the proposal a rename produces is a DRAFT contract
version a steward approves, because the pipeline reads published metadata
and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.core.registry.contract import (
    DqRule,
    DriftFinding,
    DriftKind,
    SchemaContract,
)
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm


@dataclass(frozen=True)
class Rename:
    """One settled rename, and the evidence that settled it."""

    was: str
    now: str
    glossary_id: str
    term: str
    #: The `term:<slug>` citation subject, so a proposal built from this
    #: rename can cite the exact row the reviewer should open.
    term_slug: str = ""

    def explain(self) -> str:
        return (
            f"{self.was!r} and {self.now!r} both carry {self.term!r} ({self.glossary_id}) — "
            "one concept, two spellings; a structural diff would have called this a dropped "
            "column plus a new one"
        )


@dataclass(frozen=True)
class DriftAssessment:
    """The meaning-first reading of one arrival's structural findings.

    `findings` is the full reclassified set the control plane records —
    including the RENAMED rows the structural comparison could never emit.
    `reads_as` is what the engine may do about it THIS run: contract spelling
    → arriving spelling, for settled renames only.
    """

    findings: tuple[DriftFinding, ...]
    renames: tuple[Rename, ...]
    additions: tuple[str, ...]

    @property
    def reads_as(self) -> dict[str, str]:
        return {rename.was: rename.now for rename in self.renames}

    @property
    def blocking(self) -> tuple[DriftFinding, ...]:
        return tuple(f for f in self.findings if f.blocks_batch)

    @property
    def proposes_contract_update(self) -> bool:
        """A settled rename always proposes — and never applies — v(n+1)."""
        return bool(self.renames)


def classify(
    findings: tuple[DriftFinding, ...],
    *,
    contract: SchemaContract,
    glossary: Glossary,
) -> DriftAssessment:
    """Fold glossary meaning over the structural findings.

    Everything that is not a settled rename passes through EXACTLY as the
    structural comparison said it — this function only ever downgrades a
    REMOVED+ADDED pair into one RENAMED, never invents severity and never
    hides a finding.
    """
    removed = [f for f in findings if f.kind is DriftKind.REMOVED]
    added = [f for f in findings if f.kind is DriftKind.ADDED]
    passthrough = [f for f in findings if f.kind not in {DriftKind.REMOVED, DriftKind.ADDED}]

    # Candidate pairs: (removed, arrived, term) where ONE term claims both.
    candidates: list[tuple[str, str, GlossaryTerm]] = []
    for gone in removed:
        for came in added:
            shared = [
                term
                for term in glossary.for_column(gone.column)
                if term.matches_column(came.column)
            ]
            if len(shared) == 1:
                candidates.append((gone.column, came.column, shared[0]))

    # Unique in BOTH directions, or it is ambiguity — never a guess.
    by_removed: dict[str, int] = {}
    by_added: dict[str, int] = {}
    for was, now, _ in candidates:
        by_removed[was] = by_removed.get(was, 0) + 1
        by_added[now] = by_added.get(now, 0) + 1
    renames = tuple(
        Rename(
            was=was,
            now=now,
            glossary_id=term.glossary_id,
            term=term.term,
            term_slug=term.slug,
        )
        for was, now, term in candidates
        if by_removed[was] == 1 and by_added[now] == 1
    )
    settled_was = {r.was for r in renames}
    settled_now = {r.now for r in renames}

    reclassified: list[DriftFinding] = list(passthrough)
    for rename in renames:
        reclassified.append(
            DriftFinding(
                kind=DriftKind.RENAMED,
                column=rename.now,
                detail=rename.explain(),
                blocks_batch=False,
            )
        )
    for gone in removed:
        if gone.column not in settled_was:
            reclassified.append(gone)
    additions: list[str] = []
    for came in added:
        if came.column not in settled_now:
            reclassified.append(came)
            additions.append(came.column)

    return DriftAssessment(
        findings=tuple(reclassified), renames=renames, additions=tuple(additions)
    )


@dataclass(frozen=True)
class BlastRadius:
    """What loses its source if this column stays gone — the list the
    breaking-drift alert carries, computed from lineage, never curated."""

    source_column: str
    canonical_field: str
    rule_ids: tuple[str, ...]

    def explain(self) -> str:
        rules = ", ".join(self.rule_ids) if self.rule_ids else "no rules"
        return f"{self.source_column!r} feeds {self.canonical_field!r}; affected rules: {rules}"


def blast_radius(
    source_column: str, *, contract: SchemaContract, rules: tuple[DqRule, ...]
) -> BlastRadius:
    """ "the alert lists the four mappings and two rules affected" — derived
    from the contract's own lineage and the rules' own column lists."""
    canonical = ""
    for column in contract.columns:
        if column.reads_from == source_column:
            canonical = column.name
            break
    affected = tuple(rule.rule_id for rule in rules if canonical and canonical in rule.columns)
    return BlastRadius(source_column=source_column, canonical_field=canonical, rule_ids=affected)
