"""presidio — real PHI detection, the dev AND target seat of the phi_scrub pin.

    "phi_scrub: detect_and_mask   mock: patterns   dev: presidio
     target: presidio"
    — docs/architecture/plates/04-pin-out-map.md

Presidio in BOTH worlds — identical software locally and in the tenant, so
there is no behavioural gap to discover late (ADR-0016).

Registered CONDITIONALLY: when `presidio-analyzer` is not installed the pin
simply does not gain this adapter, the mock keeps the contract suite honest,
and conformance against a profile naming `presidio` FAILS with the reason —
which is the correct message: install `requirements/ai.txt` to energize it.

Presidio's NLP recognizers are extended with the platform's own pattern set
(member IDs, Medicare IDs, NPIs) because the golden gate is 100% RECALL on
glossary-flagged PHI: a healthcare identifier presidio does not know is still
PHI here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from cinqflow.core.model.phi import Finding, ScrubResult
from cinqflow.ports import port

try:  # pragma: no cover - exercised only where the wheel is installed
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

    _PRESIDIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PRESIDIO_AVAILABLE = False

#: Healthcare identifiers presidio does not ship recognizers for — plus the
#: identifiers where presidio's PRECISION heuristics fight our RECALL gate.
#: Presidio invalidates "123-45-6789" as a sequential (therefore fake) SSN;
#: this platform masks it anyway, because a validator's idea of implausible is
#: not a disclosure policy, and a false positive costs a masked field.
_PLATFORM_PATTERNS: tuple[tuple[str, str, float], ...] = (
    ("MEMBER_ID", r"\bMBR[-_]?\d{6,}\b", 0.9),
    ("MEDICARE_ID", r"\b[1-9][A-Z][0-9A-Z]\d[A-Z][0-9A-Z]\d[A-Z]{2}\d{2}\b", 0.9),
    ("NPI", r"\b\d{10}\b", 0.6),
    ("US_SSN", r"\b\d{3}-\d{2}-\d{4}\b", 0.9),
    ("PHONE_NUMBER", r"\b\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b", 0.6),
    ("DATE_OF_BIRTH", r"\b(?:19|20)\d{2}[-/]\d{2}[-/]\d{2}\b", 0.6),
)

#: A DATE_TIME finding with no year is a clock reading, not a birth date.
#: "batch 8842 completed at 03:14" must come back untouched.
_YEARLIKE = re.compile(r"(?:19|20)\d{2}")

_SCORE_FLOOR = 0.3


@lru_cache(maxsize=1)
def _engine() -> Any:  # pragma: no cover - heavy construction, cached for the process
    engine = AnalyzerEngine()
    for entity, regex, score in _PLATFORM_PATTERNS:
        engine.registry.add_recognizer(
            PatternRecognizer(
                supported_entity=entity,
                patterns=[Pattern(name=entity.lower(), regex=regex, score=score)],
            )
        )
    return engine


def _non_overlapping(findings: list[Finding]) -> tuple[Finding, ...]:
    """Longest-then-strongest wins where spans overlap, so masking never
    produces a half-replaced value."""
    chosen: list[Finding] = []
    for finding in sorted(findings, key=lambda f: (f.start, -(f.end - f.start), -f.score)):
        if all(finding.start >= c.end or finding.end <= c.start for c in chosen):
            chosen.append(finding)
    return tuple(sorted(chosen, key=lambda f: (f.start, f.entity_type)))


if _PRESIDIO_AVAILABLE:

    @port("phi_scrub", "presidio")
    class PresidioPhiScrub:
        def detect(self, text: str) -> tuple[Finding, ...]:
            results = _engine().analyze(text=text, language="en")
            findings = [
                Finding(entity_type=r.entity_type, start=r.start, end=r.end, score=float(r.score))
                for r in results
                if r.score >= _SCORE_FLOOR
                and not (
                    r.entity_type == "DATE_TIME" and not _YEARLIKE.search(text[r.start : r.end])
                )
            ]
            return _non_overlapping(findings)

        def scrub(self, text: str) -> ScrubResult:
            findings = self.detect(text)
            scrubbed = text
            # Right to left, so earlier offsets stay valid as the string shortens.
            for finding in sorted(findings, key=lambda f: f.start, reverse=True):
                scrubbed = (
                    f"{scrubbed[: finding.start]}<{finding.entity_type}>{scrubbed[finding.end :]}"
                )
            return ScrubResult(text=scrubbed, findings=findings)
