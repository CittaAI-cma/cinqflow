"""CF-V2-E13-04 — certification is DERIVED. There is no button.

    "Derive certification mechanically from the checks — no manual 'mark as
     certified' button exists."
    "Include waivers and their reasons in the evidence — honesty is the
     product."
    "Given someone requests evidence for a batch from four months ago, when
     they export, then the report generates from retained history IDENTICAL to
     the day it was certified — evidence never degrades."
    — CF-V2-E13-04

    "Certify a batch with open critical variances or failed mandatory checks,
     under any circumstances."
    — the documented don't, and `certify()` is why it cannot be done

A PURE FUNCTION OVER RETAINED HISTORY, and every requirement above falls out of
that one decision:

  • "no manual button" — there is no route that SETS a status. The API computes
    on read. A status nobody can set is a status nobody can fake;
  • "evidence never degrades" — the verdict is a function of history, so
    re-deriving it four months later from that same history returns the same
    answer. There is no stored verdict to drift from the facts;
  • "under any circumstances" — `certify()` cannot return CERTIFIED while a
    critical variance is open, so the don't is unreachable rather than
    forbidden.

WHY `CERTIFIED_WITH_WAIVER` IS A DISTINCT VERDICT rather than a flag on
CERTIFIED: the story's happy path shows it on screen, and a payer reading the
exported report must see at a glance that something was accepted rather than
passed. Folding it into CERTIFIED with a footnote is exactly the honesty this
epic exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.variance import Variance, VarianceOutcome


class CertificationError(ValueError):
    """A certification that cannot be evaluated against its own history."""


@unique
class Verdict(StrEnum):
    """What a batch's certification says. Four values, no fifth."""

    CERTIFIED = "Certified"
    CERTIFIED_WITH_WAIVER = "Certified-with-Waiver"
    NOT_CERTIFIED = "Not Certified"
    #: Checks have not all completed. NOT a failure — a batch mid-flight is
    #: neither certified nor uncertified, and conflating the two turns every
    #: in-progress run into a red light on the board.
    PENDING = "Pending"


@unique
class CheckKind(StrEnum):
    """The mandatory check set. Every one must have an answer to certify."""

    BALANCE = "balance"
    RECONCILIATION = "reconciliation"
    DQ_RULES = "dq_rules"
    SCHEMA_CONTRACT = "schema_contract"
    SLA_WINDOW = "sla_window"
    DROP_LEDGER = "drop_ledger"
    #: CF-V3-E10-03 — G5. Not added to `MANDATORY`: that set governs every
    #: batch on the platform, most of which never touch Silver ODS at all.
    #: `certify()`'s own rule already makes ANY completed-and-failed check
    #: block certification (`Certification.failed`, checked regardless of
    #: mandatory membership) — so the ODS certification gate simply
    #: includes this check in what it passes to `certify()`, and the
    #: existing policy does the rest with no change to it.
    RELATIONSHIP_INTEGRITY = "relationship_integrity"


#: Checks whose failure can never be waived. Balance and the drop ledger are
#: the two invariants the platform's whole silent-row-loss story rests on.
MANDATORY: frozenset[CheckKind] = frozenset(
    {CheckKind.BALANCE, CheckKind.DROP_LEDGER, CheckKind.RECONCILIATION}
)


@dataclass(frozen=True)
class Check:
    """One check, its result, and where the result came from.

    `citation` is not optional in practice: the export is read by auditors and
    payers, and a check whose evidence cannot be opened is a claim rather than
    evidence. `evidence` carries the number so the report is readable when the
    reader has no access to the platform at all.
    """

    kind: CheckKind
    passed: bool
    evidence: str
    citation: CitationId | None = None
    completed: bool = True

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        if not self.completed:
            mark = "PENDING"
        return f"{mark:<8} {self.kind.value:<18} {self.evidence}"


@dataclass(frozen=True)
class Certification:
    """A batch's certification, derived — never stored as a decision."""

    batch_id: str
    feed_id: str
    verdict: Verdict
    checks: tuple[Check, ...]
    variances: tuple[Variance, ...] = field(default_factory=tuple)
    derived_ts: datetime | None = None
    as_of: date | None = None

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.completed and not c.passed)

    @property
    def waived(self) -> tuple[Variance, ...]:
        return tuple(v for v in self.variances if v.outcome is VarianceOutcome.WAIVED)

    @property
    def blocking(self) -> tuple[Variance, ...]:
        # A `date.today()` here would make the verdict depend on the READER's
        # clock — and 'evidence never degrades' means the verdict is a function
        # of the batch's own history, never of when somebody looked at it. So
        # there is no fallback: a certification with no `as_of` and no
        # `derived_ts` cannot be evaluated against a waiver's expiry, and
        # guessing a day would be the exact silent drift this epic forbids.
        day = self.as_of or (self.derived_ts.date() if self.derived_ts else None)
        if day is None:
            raise CertificationError(
                f"certification for batch {self.batch_id} carries no as_of and no derived_ts, "
                "so there is no day to judge a waiver's expiry against. Build it with "
                "`certify()`, which stamps both."
            )
        return tuple(v for v in self.variances if v.blocks_publication(day))

    @property
    def publishable(self) -> bool:
        """Silver ODS publication gate. Mechanical, per `E13-03`."""
        return self.verdict in {Verdict.CERTIFIED, Verdict.CERTIFIED_WITH_WAIVER}

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.BATCH, subject=self.batch_id, fragment="recon")


def certify(
    *,
    batch_id: str,
    feed_id: str,
    checks: Sequence[Check],
    variances: Sequence[Variance] = (),
    now: datetime,
) -> Certification:
    """The whole of `E13-04`, as one pure function.

    The order of the tests IS the policy, and each one is a line from the
    story's don'ts:

      1. A mandatory check missing entirely → PENDING (not a failure)
      2. ANY check still running            → PENDING (not a failure)
      3. Any mandatory check failed         → NOT CERTIFIED, always
      4. Any variance still blocking        → NOT CERTIFIED, always
      5. Any non-mandatory check failed     → NOT CERTIFIED
      6. Any active waiver                  → CERTIFIED_WITH_WAIVER
      7. Otherwise                          → CERTIFIED

    Step 2 is ANY check, not merely the mandatory ones. A DQ sweep that has
    not finished is evidence that has not arrived, and certifying around it
    reaches a verdict before the facts are in — which is the one thing a
    document read by a payer four months later must never have done.
    """
    given = tuple(checks)
    present = {c.kind for c in given}
    if MANDATORY - present:
        # A check that has not been RUN is not a check that FAILED. Conflating the
        # two turns every in-flight batch into a red light on the board.
        return Certification(
            batch_id=batch_id,
            feed_id=feed_id,
            verdict=Verdict.PENDING,
            checks=given,
            variances=tuple(variances),
            derived_ts=now,
            as_of=now.date(),
        )

    certification = Certification(
        batch_id=batch_id,
        feed_id=feed_id,
        verdict=Verdict.PENDING,
        checks=given,
        variances=tuple(variances),
        derived_ts=now,
        as_of=now.date(),
    )

    from dataclasses import replace

    if any(not check.completed for check in given):
        return certification

    if certification.failed or certification.blocking:
        return replace(certification, verdict=Verdict.NOT_CERTIFIED)

    if certification.waived:
        return replace(certification, verdict=Verdict.CERTIFIED_WITH_WAIVER)

    return replace(certification, verdict=Verdict.CERTIFIED)


# ── the exportable evidence document ─────────────────────────────────────────


def evidence_document(certification: Certification) -> str:
    """A clean document a payer or auditor can read STANDALONE.

        "Export as a clean document a payer or auditor can read standalone."
        "Include waivers and their reasons in the evidence — honesty is the
         product."

    Plain text on purpose. A PDF renderer is a dependency, a formatting
    argument and a diffing problem; this is byte-comparable, so the
    "identical to the day it was certified" test is a string equality rather
    than an image diff. The API wraps it in whatever the browser wants.
    """
    lines: list[str] = [
        f"CINQFLOW · Batch Certification · {certification.batch_id}",
        f"Feed: {certification.feed_id}",
        f"Verdict: {certification.verdict.value}",
        "",
        "CHECKS",
        "------",
    ]
    lines.extend(check.line() for check in certification.checks)

    if certification.waived:
        lines += ["", "WAIVERS", "-------"]
        for variance in certification.waived:
            waiver = variance.waiver
            assert waiver is not None  # WAIVED implies a waiver; the type guards it
            lines.append(
                f"{variance.kind.value:<12} delta {variance.delta} · "
                f"waived by {waiver.waived_by} until {waiver.expires_on.isoformat()}"
            )
            lines.append(f"             reason: {waiver.reason}")

    if certification.blocking:
        lines += ["", "BLOCKING VARIANCES", "------------------"]
        for variance in certification.blocking:
            lines.append(
                f"{variance.kind.value:<12} expected {variance.expected} "
                f"actual {variance.actual} (tolerance {variance.tolerance})"
            )

    lines += [
        "",
        "This report is DERIVED from retained control-table history. No status was",
        "set by hand; re-deriving it from the same history returns the same verdict.",
    ]
    return "\n".join(lines)
