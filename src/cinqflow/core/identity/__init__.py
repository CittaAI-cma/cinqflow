"""CF-V3-E9-01 — the identity stage: minimize, submit, disposition, balance.

    "Store full request and response payloads with hashes ... Retry transient
     failures with backoff; categorize outcomes rather than collapsing them
     into success/failure. Never let an unresolved identity silently
     disappear — every record has a disposition."
    — CF-V3-E9-01

    "Given a roster batch reaches the identity stage, when resolution runs,
     then 9,940 of 10,000 resolve with LinkIds in the crosswalk, 42 fail
     transiently and succeed on retry, 18 remain unresolved and route to the
     exception queue — and 9,940 + 42 + 18 = 10,000, proven."
    — CF-V3-E9-01, happy path

    "Never let an unresolved identity silently disappear ... A record whose
     identity is unresolved NEVER loads."
    — ports/identity.py

WHY THIS MODULE IS SPLIT FROM THE PORT AND THE WORKER. `ports/identity.py`
declares the CONTRACT (submit/crosswalk/merge); a worker (I/O, retries,
backoff) will eventually drive it off the queue. Between them sits the part
that must be provably correct without a network or a database: what leaves
core (attribute minimization), and what the outcome MEANS (the G4 balance
equation). `IdentityDisposition` is computed the same way
`core.recon.StageReconciliation` computes G2/G3's equation — a property of
the accounting, checked and raised on by `dispose()`, never assumed true
because a caller trusts its own bookkeeping.

ATTRIBUTE MINIMIZATION IS SUBTRACTIVE, NEVER ADDITIVE. `prepare()` keeps only
`REQUIRED_ATTRIBUTES` a record already has; it never invents a key a sparse
record lacks, which would let 'absent' and 'empty string' become
indistinguishable to Verato.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique


@unique
class MatchOutcome(StrEnum):
    """`ports/identity.py` re-exports this — it lives here because core's own
    G4 accounting (`IdentityDisposition`, below) has to reason about it, and
    core may not import a port."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"  # waits, visibly. Never loads.
    FAILED = "failed"


@dataclass(frozen=True)
class CrosswalkEntry:
    """bridge_member_source_to_verato — source identifiers retained beside
    every surrogate key, so a row can always be traced to the file it came
    from. `ports/identity.py` re-exports this for the same reason as
    `MatchOutcome`.

    The two keys line up with the client's own model
    (memory/05-ground-truth/01-canonical-model.md: "PK OurId · key LinkId
    (Verato)"): `internal_member_id` carries `OurId` when a Silver Raw row
    already has one — a member migrated from the legacy SQL Server estate —
    and stays empty for a genuinely new member, never invented to fill it.
    `verato_person_id` IS `LinkId`, Verato's own answer, present only once
    `outcome` is RESOLVED. CF-V3-E9-04's coverage telemetry reads exactly
    these two fields — "what share of records carry LinkId and legacy
    OurId" is this dataclass's own two optional identifiers, counted.
    """

    source_system: str
    source_member_id: str
    internal_member_id: str
    verato_person_id: str | None
    batch_id: str
    match_confidence_score: Decimal | None = None
    outcome: MatchOutcome = MatchOutcome.UNRESOLVED


class IdentityStageError(RuntimeError):
    """The identity stage refused something."""


class UnbalancedIdentityError(IdentityStageError):
    """submitted != resolved + unresolved + failed, or the entries do not
    even number what was submitted.

    Raised, never logged-and-continued — the same posture `core.recon`'s
    `UnattributedDropError` takes, and for the same reason: an unexplained
    difference in the riskiest error class on the platform is a defect, not a
    footnote.
    """


#: The ONLY attributes the Verato request specification allows outward.
#: Anything else a Silver Raw row carries (record_hash, line_of_business,
#: internal row ids, ...) stays in core. Declared here rather than trusted to
#: whoever calls `prepare()` next — the same reasoning `core/registry/contract`
#: gives for a closed `TypeName` vocabulary: a set that can be extended by
#: accident is not closed.
REQUIRED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "source_system",
        "source_member_id",
        "first_name",
        "last_name",
        "date_of_birth",
        "gender",
    }
)


def prepare(records: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], ...]:
    """Minimize every record to exactly the attributes Verato's request
    specification names, present or not.

    Order and count are preserved 1:1 with `records` — the caller matches a
    prepared record back to its source row by position, never by re-deriving
    identity from the minimized output alone.
    """
    return tuple(
        {key: value for key, value in record.items() if key in REQUIRED_ATTRIBUTES}
        for record in records
    )


@dataclass(frozen=True)
class IdentityDisposition:
    """G4's accounting for one batch: what was submitted, and what came back.

    `entries` is the crosswalk's own answer, per record; the properties below
    are the arithmetic the story requires be PROVEN, not eyeballed.
    """

    batch_id: str
    submitted: int
    entries: tuple[CrosswalkEntry, ...]

    @property
    def resolved(self) -> int:
        return sum(1 for e in self.entries if e.outcome is MatchOutcome.RESOLVED)

    @property
    def unresolved(self) -> int:
        return sum(1 for e in self.entries if e.outcome is MatchOutcome.UNRESOLVED)

    @property
    def failed(self) -> int:
        return sum(1 for e in self.entries if e.outcome is MatchOutcome.FAILED)

    @property
    def loadable(self) -> tuple[CrosswalkEntry, ...]:
        """ "A record whose identity is unresolved NEVER loads." Exactly the
        resolved entries — computed here once so the ODS loader (CF-V3-E8-05)
        never has to re-derive "which records may proceed" from raw outcomes."""
        return tuple(e for e in self.entries if e.outcome is MatchOutcome.RESOLVED)

    @property
    def blocked(self) -> tuple[CrosswalkEntry, ...]:
        """Unresolved AND failed — both wait, visibly, in the exception queue.
        Neither is a success; neither is silently discarded."""
        return tuple(e for e in self.entries if e.outcome is not MatchOutcome.RESOLVED)

    @property
    def balances(self) -> bool:
        """submitted == resolved + unresolved + failed, AND the entries
        actually number what was submitted — a disposition silently missing a
        record's outcome altogether must fail this exactly as loudly as a
        disposition whose categories miscount."""
        accounted = self.resolved + self.unresolved + self.failed
        return self.submitted == accounted == len(self.entries)

    def explain(self) -> str:
        """The sentence an operator needs — same shape as
        `StageReconciliation.explain()`, for the same equation-first reason."""
        summary = (
            f"batch {self.batch_id}: {self.submitted:,} submitted = "
            f"{self.resolved:,} resolved + {self.unresolved:,} unresolved + "
            f"{self.failed:,} failed"
        )
        if self.balances:
            return f"{summary}. Balanced."
        unexplained = self.submitted - len(self.entries)
        return f"{summary} ({len(self.entries):,} entries). UNBALANCED by {unexplained:,}."


def dispose(disposition: IdentityDisposition) -> IdentityDisposition:
    """Check a disposition, or refuse it.

    Raises rather than returning a flag a caller could forget to check — the
    same posture `core.recon.reconcile()` takes on G2/G3's equation, applied
    to G4.
    """
    if not disposition.balances:
        raise UnbalancedIdentityError(disposition.explain())
    return disposition
