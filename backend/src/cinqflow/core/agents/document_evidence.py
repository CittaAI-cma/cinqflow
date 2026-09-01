"""CF-V1-E16-06 — when the payer's guide and the payer's file disagree.

    "Given the uploaded guide contradicts the profiled sample (the guide says
     42 columns, the file shows 45), when suggestions build, then the conflict
     is surfaced with both sources cited, and sample evidence wins by default."
    — CF-V1-E16-06, exception path

PURE, AND SHARED BY TWO AGENTS. Schema inference and mapping suggestion both
retrieve the same companion guide and both stand to be misled by the same
sentence in it, so the check lives once, beneath both, with no port call and
no model call in it. A conflict decided by the model would be a conflict
decided by the party least able to check.

WHAT IS DETECTED, AND WHY IT IS DELIBERATELY NARROW. Exactly one class of
claim: a guide stating how many columns or fields the file has. That is the
story's own example, and it is the only claim in a companion guide that the
profiler has already measured EXACTLY — so it is the only one where
"contradicts" is a fact rather than an interpretation. A broader detector
would need to decide whether "member ID is nine digits" contradicts a column
the profiler typed as a string, and that judgement belongs to a person
reading both, not to a regex.

SAMPLE EVIDENCE WINS BY DEFAULT, AND "BY DEFAULT" IS DOING WORK. Nothing here
discards the guide. `DocumentConflict` records BOTH numbers and BOTH
citations, and the resolution it states is which one the platform proceeded
on — because the guide is sometimes right and the file sometimes truncated,
and a reviewer told only "the guide is wrong" cannot tell those apart. What
the platform will not do is silently type 45 columns against a contract that
says 42.

A GUIDE THAT STATES NOTHING PRODUCES NOTHING. No claim, no conflict — never a
conflict asserted from the absence of a claim, which would make every feed
without a specification look like a feed with a bad one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cinqflow.core.citations import CitationId

__all__ = ["COLUMN_COUNT_CLAIM", "ColumnCountClaim", "DocumentConflict", "column_count_conflicts"]

#: "42 columns", "45 fields", "has 42 data elements". Bounded to three
#: nouns the corpus actually uses, and to 1-4 digits: a five-digit column
#: count is a page number or a record count that happens to sit beside the
#: word, and matching it would manufacture a conflict out of a coincidence.
COLUMN_COUNT_CLAIM = re.compile(
    r"\b(\d{1,4})\s+(?:columns?|fields?|data\s+elements?)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ColumnCountClaim:
    """One number a document asserted, and where it said it."""

    count: int
    citation: CitationId
    quote: str


@dataclass(frozen=True)
class DocumentConflict:
    """A disagreement between a document and the file itself, both cited."""

    what: str
    document_says: int
    sample_shows: int
    document_citation: CitationId
    sample_citation: CitationId
    quote: str

    @property
    def resolution(self) -> str:
        """Which source the platform proceeded on, and why — in the reviewer's
        language, because this string is what a review screen prints."""
        return (
            f"Proceeding on the sample: {self.sample_shows}. "
            f"The file is the thing that will actually arrive, and it is measured rather than "
            f"described. If the guide is right and this delivery is truncated, that is worth "
            f"asking the payer about before approving the contract."
        )

    def as_record(self) -> dict[str, object]:
        return {
            "what": self.what,
            "document_says": self.document_says,
            "sample_shows": self.sample_shows,
            "document_citation": str(self.document_citation),
            "sample_citation": str(self.sample_citation),
            "quote": self.quote,
            "resolution": self.resolution,
        }


def claims_in(text: str, citation: CitationId) -> tuple[ColumnCountClaim, ...]:
    """Every column-count claim one chunk of document text makes.

    The QUOTE travels with the number. A reviewer shown "the guide says 42"
    with no sentence around it cannot tell a layout table's total from a
    paragraph about a different file the same guide also documents — and that
    distinction is exactly what they are being asked to judge.
    """
    found: list[ColumnCountClaim] = []
    for match in COLUMN_COUNT_CLAIM.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        found.append(
            ColumnCountClaim(
                count=int(match.group(1)),
                citation=citation,
                quote=" ".join(text[start:end].split()),
            )
        )
    return tuple(found)


def column_count_conflicts(
    *,
    chunks: tuple[tuple[CitationId, str], ...],
    sample_columns: int,
    sample_citation: CitationId,
) -> tuple[DocumentConflict, ...]:
    """Every retrieved chunk whose stated column count is not what the file has.

    De-duplicated by (count, citation): a guide that says "42 columns" three
    times on one page has made one claim, and printing it three times on a
    review screen would read as three independent contradictions.
    """
    seen: set[tuple[int, str]] = set()
    conflicts: list[DocumentConflict] = []
    for citation, text in chunks:
        for claim in claims_in(text, citation):
            if claim.count == sample_columns:
                continue
            key = (claim.count, str(citation))
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(
                DocumentConflict(
                    what="column count",
                    document_says=claim.count,
                    sample_shows=sample_columns,
                    document_citation=citation,
                    sample_citation=sample_citation,
                    quote=claim.quote,
                )
            )
    return tuple(conflicts)
