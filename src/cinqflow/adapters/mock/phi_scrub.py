"""patterns — regex PHI detection. The Lane-1 stand-in for Presidio."""

from __future__ import annotations

import re

from cinqflow.ports import port
from cinqflow.ports.phi_scrub import Finding, ScrubResult

# Deliberately BROAD rather than precise. The gate is 100% RECALL: missing PHI
# is the failure that matters, so a mock that under-detects would let the
# ordering test pass while the real risk went unexercised.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("MEDICARE_ID", re.compile(r"\b[1-9][A-Z][0-9A-Z]\d[A-Z][0-9A-Z]\d[A-Z]{2}\d{2}\b")),
    ("NPI", re.compile(r"\b\d{10}\b")),
    ("DATE_OF_BIRTH", re.compile(r"\b(?:19|20)\d{2}[-/]\d{2}[-/]\d{2}\b")),
    ("EMAIL_ADDRESS", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("PHONE_NUMBER", re.compile(r"\b\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")),
    ("MEMBER_ID", re.compile(r"\bMBR[-_]?\d{6,}\b", re.IGNORECASE)),
)


@port("phi_scrub", "mock")
class PatternPhiScrub:
    def detect(self, text: str) -> tuple[Finding, ...]:
        findings = [
            Finding(entity_type=name, start=m.start(), end=m.end(), score=1.0)
            for name, pattern in _PATTERNS
            for m in pattern.finditer(text)
        ]
        return tuple(sorted(findings, key=lambda f: (f.start, f.entity_type)))

    def scrub(self, text: str) -> ScrubResult:
        findings = self.detect(text)
        scrubbed = text
        # Right to left, so earlier offsets stay valid as the string shortens.
        for finding in sorted(findings, key=lambda f: f.start, reverse=True):
            scrubbed = (
                f"{scrubbed[: finding.start]}<{finding.entity_type}>{scrubbed[finding.end :]}"
            )
        return ScrubResult(text=scrubbed, findings=findings)
