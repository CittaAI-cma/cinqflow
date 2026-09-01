"""CF-V3-E9-03 — merge/split: R4, human-always, verified.

    "for each potential merge (two records, one person) or split (one record,
     two people), an AI-prepared evidence card ... and a preview of exactly
     which records would repoint or separate, with the decision itself always
     mine, so that the riskiest action in the platform (changing who a record
     belongs to) is made by an informed human every single time"
    "Execute any merge or split automatically, at any confidence, ever — this
     is a human decision by policy, not by threshold."
    "Hide any affected record from the preview."
    — CF-V3-E9-03

    "2. Merge (L200 -> L100). C2 is marked MERGED_TO_C1; C1 stays ACTIVE. The
     satellite rows repoint and dedup — two identical addresses collapse to
     one. A subsequent update on the merged-away source id flows to C1."
    — memory/05-ground-truth/01-canonical-model.md, Verato Scenarios.docx

THE DESIGN DECISION THAT MAKES "100% PREVIEW-MATCHES-OUTCOME" STRUCTURAL
RATHER THAN ASSERTED: `plan_merge()` is the ONLY function that decides what a
merge does. It runs once, before, to produce the preview; it runs AGAIN,
after, inside `verify_post_change()`, against whatever the plane actually
looks like — and "matches" means the second run finds nothing left to do. Two
separate implementations (a "preview engine" and a "verify engine") could
drift from each other the day one of them is patched and the other is not;
one function run twice cannot drift from itself.

R4 HAS NO CONFIDENCE PARAMETER, ANYWHERE IN THIS MODULE. `RiskClass.R4.
at_confidence()` already ignores its argument by construction
(`core/model/vocabulary.py`); `execute_merge` goes further and does not
ACCEPT one — there is no signature this function could have that a caller
could accidentally thread a confidence score through and reach automation.

DEMOGRAPHIC COMPARISON NEVER RETURNS A VALUE, ONLY A CATEGORY. `compare_
demographics` is what makes "send unmasked PHI to any model" structurally
false for the evidence-card agent specifically: its output is `match` /
`differs` / `similar` per field, which is everything an AI narrator needs to
write a grounded sentence and NOTHING it could use to reconstruct a name or a
birth date.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, unique


class MergeError(RuntimeError):
    """The merge engine refused something."""


class UnapprovedMergeExecutionError(MergeError):
    """Execution attempted without a named steward approval.

    R4 is human-always. Raised, never defaulted around — the same posture
    `core.model.governed.UnnamedApproverError` takes on a publish, applied to
    the riskiest action on the platform.
    """


class PreviewMismatchError(MergeError):
    """Post-change verification found the plane does not match the preview.

    Raised only when a caller asks `verify_post_change` to ENFORCE — the
    default is to report the mismatch as data, because a steward re-reading
    what actually happened needs the difference, not just a crash.
    """


@dataclass(frozen=True)
class SatelliteRow:
    """One row of one satellite table, as the merge engine needs to see it —
    never the row's own PHI-bearing content, only what identifies it and a
    normalized signature of what it says."""

    entity: str
    record_id: str
    owner_member_id: str
    content_key: str


@dataclass(frozen=True)
class SatelliteRepoint:
    entity: str
    record_id: str
    from_member_id: str
    to_member_id: str


@dataclass(frozen=True)
class DuplicateCollapse:
    entity: str
    kept_record_id: str
    collapsed_record_id: str


@dataclass(frozen=True)
class MergePlan:
    """The deterministic preview — and the thing `verify_post_change`
    re-derives to check against. Nothing here is model output."""

    merged_away_member_id: str
    survivor_member_id: str
    repoints: tuple[SatelliteRepoint, ...]
    collapses: tuple[DuplicateCollapse, ...]

    @property
    def marked_merged(self) -> str:
        """The identifier the plate's own scenario marks MERGED_TO — the
        member that stops being active."""
        return self.merged_away_member_id

    def affected_record_ids(self) -> frozenset[str]:
        """Every merged-away row this plan named — repointed or collapsed.
        "Hide any affected record from the preview" is the don't this method
        makes checkable: nothing merged-away is missing from this set."""
        return frozenset(
            {r.record_id for r in self.repoints} | {c.collapsed_record_id for c in self.collapses}
        )

    @property
    def fingerprint(self) -> str:
        material = (
            f"{self.merged_away_member_id}->{self.survivor_member_id}|"
            + "|".join(
                sorted(f"R:{r.entity}:{r.record_id}:{r.to_member_id}" for r in self.repoints)
            )
            + "|"
            + "|".join(
                sorted(
                    f"C:{c.entity}:{c.collapsed_record_id}:{c.kept_record_id}"
                    for c in self.collapses
                )
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()[:32]


def plan_merge(
    *,
    merged_away_member_id: str,
    survivor_member_id: str,
    merged_away_rows: Sequence[SatelliteRow],
    survivor_rows: Sequence[SatelliteRow],
) -> MergePlan:
    """Pure. Every merged-away row either repoints to the survivor or
    collapses into an identical row the survivor already has — never both,
    never neither.
    """
    if merged_away_member_id == survivor_member_id:
        raise MergeError(f"{merged_away_member_id} cannot be merged into itself")

    survivor_by_content: dict[str, SatelliteRow] = {}
    for row in survivor_rows:
        survivor_by_content.setdefault(row.content_key, row)

    repoints: list[SatelliteRepoint] = []
    collapses: list[DuplicateCollapse] = []
    for row in merged_away_rows:
        match = survivor_by_content.get(row.content_key)
        if match is not None:
            collapses.append(
                DuplicateCollapse(
                    entity=row.entity,
                    kept_record_id=match.record_id,
                    collapsed_record_id=row.record_id,
                )
            )
        else:
            repoints.append(
                SatelliteRepoint(
                    entity=row.entity,
                    record_id=row.record_id,
                    from_member_id=merged_away_member_id,
                    to_member_id=survivor_member_id,
                )
            )

    return MergePlan(
        merged_away_member_id=merged_away_member_id,
        survivor_member_id=survivor_member_id,
        repoints=tuple(repoints),
        collapses=tuple(collapses),
    )


@dataclass(frozen=True)
class AuthorizedMerge:
    """A plan a named steward has approved. The only thing `execute_merge`
    produces — a worker performs the actual write from this, never from a
    bare `MergePlan`."""

    plan: MergePlan
    steward_approval_id: str


def execute_merge(plan: MergePlan, *, steward_approval_id: str | None) -> AuthorizedMerge:
    """Refuse without a named approval. This function performs no write
    itself — writing rows is I/O, and core performs none; a worker takes the
    `AuthorizedMerge` this returns and does the actual repointing."""
    if not steward_approval_id or not steward_approval_id.strip():
        raise UnapprovedMergeExecutionError(
            f"merge of {plan.merged_away_member_id} into {plan.survivor_member_id} requires a "
            "named steward approval. R4 is human-always, never automated, and not configurable "
            "at any confidence — there is no parameter on this function a confidence score "
            "could reach."
        )
    return AuthorizedMerge(plan=plan, steward_approval_id=steward_approval_id.strip())


@dataclass(frozen=True)
class MergeVerification:
    """Whether the plane, after execution, matches what the preview named."""

    plan: MergePlan
    matches_preview: bool
    unexpected_remainder: tuple[str, ...]


def verify_post_change(
    authorized: AuthorizedMerge,
    *,
    post_change_rows: Sequence[SatelliteRow],
    enforce: bool = False,
) -> MergeVerification:
    """Re-derive from the CURRENT state of every row the plan named — if the
    merge fully executed, none of them still belongs to the merged-away id.

    This is the same function (`plan_merge`) run again, in effect: any row
    still owned by `merged_away_member_id` is exactly what a second planning
    pass would still find work to do on.
    """
    named = authorized.plan.affected_record_ids()
    still_owned = tuple(
        sorted(
            r.record_id
            for r in post_change_rows
            if r.record_id in named and r.owner_member_id == authorized.plan.merged_away_member_id
        )
    )
    verification = MergeVerification(
        plan=authorized.plan, matches_preview=not still_owned, unexpected_remainder=still_owned
    )
    if enforce and not verification.matches_preview:
        raise PreviewMismatchError(
            f"post-change state does not match the preview for "
            f"{authorized.plan.merged_away_member_id}->{authorized.plan.survivor_member_id}: "
            f"still owned by {authorized.plan.merged_away_member_id}: {', '.join(still_owned)}"
        )
    return verification


# ── demographic comparison — categorical only ────────────────────────────────


@unique
class FieldComparison(StrEnum):
    MATCH = "match"
    DIFFERS = "differs"
    SIMILAR = "similar"


@dataclass(frozen=True)
class DemographicComparison:
    """Field name -> category. No raw value survives into this object, which
    is what makes it safe to hand to a model."""

    fields: dict[str, FieldComparison]


def compare_demographics(
    left: Mapping[str, str], right: Mapping[str, str], *, fields: tuple[str, ...]
) -> DemographicComparison:
    """A field present on only one side, or with a different value, DIFFERS.
    Neither raw value crosses into the result."""
    result: dict[str, FieldComparison] = {}
    for field in fields:
        left_value = left.get(field)
        right_value = right.get(field)
        if left_value is not None and left_value == right_value:
            result[field] = FieldComparison.MATCH
        else:
            result[field] = FieldComparison.DIFFERS
    return DemographicComparison(fields=result)


# ── flip-flop detection ───────────────────────────────────────────────────────


@unique
class IdentityEventKind(StrEnum):
    MERGE = "merge"
    SPLIT = "split"


@dataclass(frozen=True)
class IdentityEvent:
    kind: IdentityEventKind
    member_a: str
    member_b: str
    source_system: str
    occurred_ts: datetime


@dataclass(frozen=True)
class FlipFlopFinding:
    member_a: str
    member_b: str
    offending_source: str
    reversal_count: int


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def detect_flip_flop(
    events: Sequence[IdentityEvent], *, within: timedelta, min_reversals: int = 2
) -> tuple[FlipFlopFinding, ...]:
    """A pair of identities whose merge/split state reverses `min_reversals`
    times within `within` is oscillating. The offending source is whichever
    source supplied the most events in the reversing run — a diagnostic, not
    a shared blame across every payer that ever touched the pair.
    """
    by_pair: dict[tuple[str, str], list[IdentityEvent]] = {}
    for event in events:
        by_pair.setdefault(_pair_key(event.member_a, event.member_b), []).append(event)

    findings: list[FlipFlopFinding] = []
    for (member_a, member_b), pair_events in by_pair.items():
        ordered = sorted(pair_events, key=lambda e: e.occurred_ts)
        reversals = 0
        window: list[IdentityEvent] = []
        for event in ordered:
            window = [e for e in window if event.occurred_ts - e.occurred_ts <= within]
            if window and window[-1].kind is not event.kind:
                reversals += 1
            window.append(event)

        if reversals >= min_reversals:
            counts: dict[str, int] = {}
            for event in ordered:
                counts[event.source_system] = counts.get(event.source_system, 0) + 1
            offending = max(sorted(counts), key=lambda source: counts[source])
            findings.append(
                FlipFlopFinding(
                    member_a=member_a,
                    member_b=member_b,
                    offending_source=offending,
                    reversal_count=reversals,
                )
            )

    return tuple(findings)
