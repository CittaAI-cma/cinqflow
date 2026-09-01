"""CF-V0-E13-01 — Count reconciliation with a named-reason drop ledger.

    "I want every layer transition to prove that records in equal records out
     plus quarantined plus dropped — with every dropped record attributed to a
     named rule, so that SILENT ROW LOSS (the documented cause of understated
     member rosters in the past) becomes STRUCTURALLY IMPOSSIBLE: a row can
     only leave the pipeline with a reason attached."

The invariant, quoted so a failing test explains itself:

    "rows_in == rows_out + quarantined + attributed_drops, every stage, every
     batch"
    "no drop-ledger category named 'other' or 'unknown' may exist"
    — docs/architecture/INVARIANTS.md, data plane

This module is the arithmetic and the vocabulary. The database enforces the
same two rules as CHECK constraints, deliberately: one of them can be reasoned
about in tests, and the other cannot be bypassed by a code path nobody
reviewed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import ErrorCategory, Layer

# A drop category must NAME something. These are the words that are not names.
FORBIDDEN_REASONS = frozenset({"other", "unknown", "n/a", "na", "misc", "various", ""})


@unique
class ReconVerdict(StrEnum):
    BALANCED = "balanced"
    FAILED_RECONCILIATION = "failed_reconciliation"


class UnattributedDropError(ValueError):
    """A drop with no named rule, or with a category that is not a reason.

    Raised rather than logged. Incident #2 — member_provider silently losing
    rows where pcp_npi was null — is the reason this is an exception and not a
    warning: the failure mode is that nobody notices.
    """


@dataclass(frozen=True)
class DropReason:
    """One attributed exclusion. The rule that did it, and what it means."""

    rule_id: str
    reason: str
    record_count: int
    columns: tuple[str, ...] = ()
    financial_impact: Decimal | None = None

    def __post_init__(self) -> None:
        if self.record_count < 0:
            raise ValueError(f"{self.rule_id}: a negative drop count is not a count")
        for value, label in ((self.rule_id, "rule_id"), (self.reason, "reason")):
            if value.strip().lower() in FORBIDDEN_REASONS:
                raise UnattributedDropError(
                    f"{label}={value!r} is not a reason. Every dropped record is attributed "
                    "to the specific rule or step that excluded it — 'other' and 'unknown' "
                    "are how silent row loss gets a label instead of a fix."
                )

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.rule_id)


@dataclass(frozen=True)
class StageReconciliation:
    """One stage's accounting, and its verdict."""

    batch_id: str
    stage: Layer
    records_in: int
    records_out: int
    quarantined: int = 0
    drops: tuple[DropReason, ...] = field(default_factory=tuple)

    @property
    def attributed_drops(self) -> int:
        return sum(d.record_count for d in self.drops)

    @property
    def unexplained(self) -> int:
        """What the equation cannot account for. Zero, or the batch fails."""
        return self.records_in - (self.records_out + self.quarantined + self.attributed_drops)

    @property
    def balances(self) -> bool:
        return self.unexplained == 0

    @property
    def verdict(self) -> ReconVerdict:
        return ReconVerdict.BALANCED if self.balances else ReconVerdict.FAILED_RECONCILIATION

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RECON, subject=self.batch_id)

    def explain(self) -> str:
        """The sentence an operator actually needs.

        "22,000 in = 21,820 out + 175 quarantined by DQ-002 + 5 rejected by
        structure check" — which is the exact phrasing in the story's happy
        path, because that is the phrasing that ends an investigation.
        """
        parts = [f"{self.records_out:,} out"]
        if self.quarantined:
            parts.append(f"{self.quarantined:,} quarantined")
        parts.extend(f"{d.record_count:,} {d.reason} ({d.rule_id})" for d in self.drops)
        summary = f"{self.records_in:,} in = " + " + ".join(parts)
        if self.balances:
            return f"{summary}. Balanced."
        return (
            f"{summary}. UNBALANCED by {self.unexplained:,} records at {self.stage.value} — "
            "publication is blocked until every excluded record is attributed."
        )


def reconcile(stage: StageReconciliation) -> StageReconciliation:
    """Check a stage, or refuse it.

    "Fail the batch loudly if the equation does not balance — an unexplained
    difference is a defect, never a footnote." So this RAISES rather than
    returning a flag that a caller could forget to check.
    """
    if not stage.balances:
        raise UnattributedDropError(stage.explain())
    return stage


def error_id_hash(
    *,
    batch_id: str,
    stage: Layer,
    record_key: str | None,
    error_type: ErrorCategory,
    rule_id: str | None,
) -> str:
    """The deterministic error hash — the quiet hero of the control plane.

        error_id_hash = hash(batch_id, stage_name, record_key, error_type, rule_id)

    Deriving the identifier from the error's OWN FACTS makes replay idempotent
    at the error level: reprocessing a corrected batch cannot manufacture
    duplicate incidents. That is precisely what lets "reprocess only the failed
    records" exist as a safe, ordinary button rather than a control-row surgery
    session — which is how the incumbent platform did it ("delete the control
    rows so bronze will accept the replay").

    SHA-256 rather than Python's hash(), which is salted per process: an
    identifier that changed between runs would defeat the entire point.
    """
    material = "|".join([batch_id, stage.value, record_key or "", error_type.value, rule_id or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
