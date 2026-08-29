"""What a PHI scrub returns. Positions, never values.

A `Finding` that carried the detected VALUE would put PHI into logs and error
messages — the two classic leak routes — through the very type built to prevent
it. That is a domain guarantee, so it lives in core where the gateway can
depend on it without depending on the pin.

`ports/phi_scrub.py` re-exports these.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
