"""The `phi_scrub` pin — detect and mask PHI.

    verb: detect_and_mask   mock: patterns   dev: presidio   target: presidio
    — docs/architecture/plates/04-pin-out-map.md

    "PHI is scrubbed before ANY prompt; the scrub-then-prompt ordering has its
     own test"
    — docs/architecture/INVARIANTS.md, intelligence

Presidio in both worlds — identical software locally and in the tenant — so
there is no behavioural gap to discover late (ADR-0016). Swap cost: ZERO.

The ordering has its own test, asserted INDEPENDENTLY of what either component
does, because "we scrub before we prompt" is the kind of property that survives
review and dies to a refactor that reorders two lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Finding:
    """One detected entity. Positions, never the value.

    Carrying the detected VALUE in a finding would put PHI into logs and error
    messages — the two classic leak routes — via the very type built to
    prevent it.
    """

    entity_type: str
    start: int
    end: int
    score: float


@dataclass(frozen=True)
class ScrubResult:
    text: str
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def was_scrubbed(self) -> bool:
        return bool(self.findings)


@runtime_checkable
class PhiScrubPort(Protocol):
    def scrub(self, text: str) -> ScrubResult:
        """Mask PHI. Returns the masked text and what was found, never the
        values found."""
        ...

    def detect(self, text: str) -> tuple[Finding, ...]:
        """Detect without masking.

        The gate is 100% RECALL, not precision: missing PHI is the failure that
        matters, and a false positive costs a masked field while a false
        negative costs a disclosure.
        """
        ...
