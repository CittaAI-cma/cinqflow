"""The `document_parse` pin — layout-aware parsing before chunking.

    verb: parse_layout_aware   mock: canned   dev: local_pypdf_docx
    target: local_pypdf_docx_or_azure_doc_intelligence
    — docs/architecture/plates/04-pin-out-map.md

THE PIN THAT WAS MISSING FOR CF-V1-E16-04. `core.knowledge.chunk_incident`
and `chunk_runbook` chunk an object that is ALREADY a parsed Python value —
there was never a byte stream to parse. A payer's companion guide (E16-06)
and the client's own specs (E16-04) arrive as PDF or Word bytes, and turning
those bytes into TEXT WITH PAGE ANCHORS AND WHOLE TABLES is vendor work no
different in kind from what `phi_scrub` or `llm` already wrap — so it gets
its own pin rather than a private import inside `workers.knowledge`.

    "Parse layout-aware (tables kept whole, page anchors preserved) and chunk
     per content type, each chunk carrying a citation_id the UI can open."
    — CF-V1-E16-04, acceptance criteria

WHY PAGE ANCHORS ARE THE WHOLE POINT. E16-06's happy path is "the companion
guide p.14 defines MBR_DOB as CCYYMMDD" — a citation a reviewer can open to
the actual page. A parser that returns one flat string cannot honour that;
`ParsedDocument.pages` is the unit `core.knowledge.chunk_document` cites by,
one `CitationKind.DOCUMENT` fragment per page.

WHY TABLES ARE KEPT WHOLE. A companion guide's field-mapping table read cell
by cell loses the row that ties a column name to its format — exactly the
"MBR_DOB -> CCYYMMDD" fact the happy path grounds on. `ParsedPage.tables`
therefore holds each table as its own block, rendered as one contiguous unit
the chunker never splits mid-row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class DocumentParseError(RuntimeError):
    """The bytes could not be parsed at all — corrupt, empty, or a media type
    this adapter does not speak. Never returned as an empty `ParsedDocument`:
    a document nobody could read is a refusal, not a document with nothing in
    it, and the two must not look the same to a caller."""


@dataclass(frozen=True)
class ParsedTable:
    """One table, kept whole. Rendered as pipe-delimited rows so a chunk that
    carries it stays plain text — no second content type for a chunker or a
    citation to reason about."""

    rows: tuple[tuple[str, ...], ...]

    def as_text(self) -> str:
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in self.rows)


@dataclass(frozen=True)
class ParsedPage:
    """One page (or, for a paginated-in-spirit format like a CSV or a plain
    text file, one single page numbered 1) — the unit `chunk_document` cites
    by by page number, never by byte offset."""

    number: int
    text: str
    tables: tuple[ParsedTable, ...] = ()


@dataclass(frozen=True)
class ParsedDocument:
    """A document, layout-aware. `pages` is never empty — a parse that found
    nothing raises `DocumentParseError` rather than returning one."""

    media_type: str
    pages: tuple[ParsedPage, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.pages:
            raise DocumentParseError("a parsed document with no pages is a parse that failed")

    @property
    def page_count(self) -> int:
        return len(self.pages)


#: What this pin will actually be asked to read. A format outside this set is
#: refused by name rather than guessed at — `adapters.local.file_document
#: _parse` raises `DocumentParseError` naming the media type it does not
#: speak, never falls back to treating unknown bytes as plain text.
SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown",
        "text/plain",
        "text/csv",
    }
)


@runtime_checkable
class DocumentParsePort(Protocol):
    def parse(self, content: bytes, *, media_type: str, filename: str = "") -> ParsedDocument:
        """Bytes in, a layout-aware document out — or a refusal.

        `filename` is for the error message only ("guide.pdf could not be
        parsed"), never used to infer the media type: a caller that already
        knows the media type (the upload route does, from the multipart
        part) must not have it silently overridden by an extension.
        """
        ...
