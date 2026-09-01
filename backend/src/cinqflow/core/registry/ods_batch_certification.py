"""CF-V3-E10-03 — one batch's Silver ODS publication decision, governed.

    "Hold publication on failure — downstream never sees an uncertified
     batch."
    "Guardrail — Given an approval is attempted by the author or an
     unauthorized role, when they approve, then the system blocks it and
     records the attempt."
    "Don't — Let any object reach Published without the named approver.
     Allow the author of a change to approve their own change."
    — CF-V3-E10-03

THE FIRST GOVERNED OBJECT KEYED TO SOMETHING THE PLATFORM RAN, NOT SOMETHING
A PERSON AUTHORED. Every other `ObjectType` (a feed, a mapping, the ODS model
itself) is a configuration a human wrote. A batch's certification is
computed — `core.certification.certify()` already derives the verdict from
retained history, on purpose, so there is never a second, hand-set status to
drift from the facts. What is NOT computed is whether the batch's data may
be EXPOSED downstream: that is a decision, and ADR-0006's own reasoning
("one lifecycle for every governed object") applies to it exactly as it did
when `ObjectType.ODS_MODEL` was added for CF-V3-E10-01 — reuse the SAME
engine (`core.model.governed`) rather than a bespoke approval flag, so
"author never approves own change" and "nothing reaches Published without a
named approver" are inherited, not re-implemented.

`object_id` is the `batch_id` — fixed, the same way `object_id="silver_ods"`
is fixed for the ODS model. `version` stays 1 for the life of one batch's
decision: a batch is certified once, from one set of checks: a SECOND
version would mean re-litigating history that `certify()` already treats as
immutable evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.certification import Certification, Verdict
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType


class BatchCertificationError(RuntimeError):
    """A batch certification record could not be built or read back."""


class UncertifiedDraftError(BatchCertificationError):
    """A certification decision was about to be drafted for a batch whose
    own checks do not say CERTIFIED or CERTIFIED_WITH_WAIVER.

    "Downstream never sees an uncertified batch" — enforced here, at
    construction, rather than trusted to whichever caller happens to draft
    one: there is no code path that can create a DRAFT for a batch that has
    not actually passed.
    """


@dataclass(frozen=True)
class CheckSummary:
    """One check's result, frozen at draft time — the evidence a Data
    Steward reviews, byte-identical to what `certify()` actually found."""

    kind: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class BatchCertificationRecord:
    """The governed decision: this batch's Silver ODS data may be exposed
    downstream, on the strength of the checks attached."""

    batch_id: str
    feed_id: str
    model_version: str
    verdict: str
    checks: tuple[CheckSummary, ...]

    def __post_init__(self) -> None:
        if self.verdict not in {Verdict.CERTIFIED.value, Verdict.CERTIFIED_WITH_WAIVER.value}:
            raise UncertifiedDraftError(
                f"{self.batch_id}: verdict is {self.verdict!r}, not Certified or "
                "Certified-with-Waiver — a batch that has not passed its checks has "
                "nothing here to draft."
            )


def from_certification(
    certification: Certification, *, model_version: str
) -> BatchCertificationRecord:
    """The governed record a passing `Certification` earns — never built
    from anything else, so the evidence attached is always what `certify()`
    itself produced."""
    return BatchCertificationRecord(
        batch_id=certification.batch_id,
        feed_id=certification.feed_id,
        model_version=model_version,
        verdict=certification.verdict.value,
        checks=tuple(
            CheckSummary(kind=check.kind.value, passed=check.passed, evidence=check.evidence)
            for check in certification.checks
        ),
    )


def as_governed(
    record: BatchCertificationRecord, *, author: Actor, created_ts: datetime | None = None
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.ODS_BATCH_CERTIFICATION,
        object_id=record.batch_id,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body={
            "feed_id": record.feed_id,
            "model_version": record.model_version,
            "verdict": record.verdict,
            "checks": [
                {"kind": check.kind, "passed": check.passed, "evidence": check.evidence}
                for check in record.checks
            ],
        },
    )


def from_governed(obj: GovernedObject) -> BatchCertificationRecord:
    if obj.object_type is not ObjectType.ODS_BATCH_CERTIFICATION:
        raise BatchCertificationError(f"{obj.object_type} is not a batch certification")
    return BatchCertificationRecord(
        batch_id=obj.object_id,
        feed_id=obj.body["feed_id"],
        model_version=obj.body["model_version"],
        verdict=obj.body["verdict"],
        checks=tuple(
            CheckSummary(kind=c["kind"], passed=c["passed"], evidence=c["evidence"])
            for c in obj.body.get("checks", [])
        ),
    )
