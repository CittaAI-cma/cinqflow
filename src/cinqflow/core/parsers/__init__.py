"""File parsers — bytes in, Arrow out. Identical on both planes.

    "file parsers (csv|xlsx|fhir_ndjson|hl7_json|fixed_width) run BEFORE the
     store, identical on both planes"
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

ARROW IS THE RECORD CONTRACT between parsing and loading. pg-compute writes an
Arrow table with binary COPY; databricks-compute writes the same table through
Spark. One set of types, one null semantics, on both planes.

Deliberately NOT pandas, and this is a correctness argument rather than a style
preference: pandas conflates NaN and None, and silently widens an integer
column containing nulls to float. Both corrupt the balance equation and money
columns — the two things Wave 0 exists to make trustworthy.

Parsers live in core/ and perform NO I/O. They receive bytes handed to them by
the storage adapter. A parser that opened a file would put a filesystem path in
the one place paths are forbidden, and would make the same parse impossible to
test without a disk.

EVERY VALUE ARRIVES AS A STRING. Casting happens later, at the `cast` plan
step, against the approved contract — so a cast failure is an ATTRIBUTED drop
with a rule behind it, rather than a parser exception that loses the row.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO, StringIO

import pyarrow as pa


class ParseError(ValueError):
    """A file that could not be read as its declared format.

    Carries the reason, because "could not parse" routes a file to `rejected/`
    and an operator has to know why without opening it — which they may not be
    allowed to do.
    """


@dataclass(frozen=True)
class ParsedFile:
    """The parse result. Rows as strings, plus what the header claimed."""

    table: pa.Table
    columns: tuple[str, ...]
    row_count: int

    @property
    def column_count(self) -> int:
        return len(self.columns)


def parse(content: bytes, *, file_format: str, encoding: str = "utf-8") -> ParsedFile:
    """Parse by declared format — never by sniffing.

    The registry declares the format, so ambiguity is a registry question
    rather than a runtime guess. Incident #12: the Optum GA landing zone
    contained BOTH csv and xlsx, with the Excel file being the original. A
    sniffing parser picks one and is quietly wrong half the time; a declared
    format makes the disagreement visible in the registry, where someone can
    settle it.
    """
    match file_format.lower():
        case "csv" | "txt" | "tsv" | "delimited":
            return _parse_delimited(content, encoding=encoding, format_name=file_format.lower())
        case "xlsx" | "xls" | "ods":
            return _parse_spreadsheet(content)
        case _:
            raise ParseError(
                f"{file_format!r} has no parser. Wave 0 reads delimited and spreadsheet "
                "formats; FHIR NDJSON, HL7-derived JSON and fixed-width arrive in later waves."
            )


def _parse_delimited(content: bytes, *, encoding: str, format_name: str) -> ParsedFile:
    if not content.strip():
        raise ParseError("the file is empty")

    try:
        text = content.decode(_bom_aware(content, encoding))
    except UnicodeDecodeError as exc:
        # Incident, seeded: "bad encoding". REJECTED WITH A STATED REASON,
        # never silently mojibaked into Bronze where it becomes permanent.
        raise ParseError(
            f"not valid {encoding}: byte {exc.start} is {content[exc.start]:#04x}. "
            "The file is rejected rather than decoded with replacements — a mojibaked "
            "member name in an append-only layer cannot be corrected later."
        ) from None

    delimiter = "\t" if format_name == "tsv" else _sniff_delimiter(text)
    reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter)

    try:
        header = next(reader)
    except StopIteration:
        raise ParseError("the file has no header row") from None

    columns = tuple(name.strip() for name in header)
    if len(set(columns)) != len(columns):
        duplicates = sorted({c for c in columns if columns.count(c) > 1})
        raise ParseError(f"duplicate column names in the header: {', '.join(duplicates)}")

    rows: list[list[str]] = []
    for line_number, row in enumerate(reader, start=2):
        if not any(field.strip() for field in row):
            continue  # a trailing blank line is not a record
        if len(row) != len(columns):
            raise ParseError(
                f"row {line_number} has {len(row)} fields but the header declares "
                f"{len(columns)}. Landing accepted this file structurally; the row-level "
                "mismatch is a G2 structure failure."
            )
        rows.append([field.strip() for field in row])

    return _to_arrow(columns, rows)


#: The byte-order marks a payer export tool actually emits, and the codec that
#: consumes each one. Excel's "CSV UTF-8" writes the first of these on every
#: file it saves, so this is an ordinary delivery, not an exotic one.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def _bom_aware(content: bytes, encoding: str) -> str:
    """Swap in the BOM-consuming codec when the file starts with one.

    Without this, a BOM survives the decode and the FIRST COLUMN'S NAME becomes
    `﻿MemberID` — which `str.strip()` does not remove. Drift detection then
    reports the contracted first column as REMOVED and an unknown column as
    ADDED, and the batch fails on a file that is perfectly good. The profiler
    reports the BOM either way (CF-V1-E5-01), because a payer who starts
    sending one is a fact worth knowing; but the platform reading it correctly
    is not something a BA should have to arrange.
    """
    for mark, codec in _BOMS:
        if content.startswith(mark):
            # Only override a utf-8 request. A feed declared cp1252 that
            # arrives with a utf-16 BOM is a genuine disagreement, and
            # silently switching codecs would hide it.
            return codec if encoding.lower().replace("_", "-") in {"utf-8", "utf8"} else encoding
    return encoding


def _sniff_delimiter(text: str) -> str:
    """Comma, pipe or tab — the three the estate actually uses.

    Sniffing the DELIMITER is safe in a way sniffing the FORMAT is not: a wrong
    delimiter produces one giant column, which fails the contract check loudly
    on the very next plan step.
    """
    header = text.split("\n", 1)[0]
    return max((",", "|", "\t"), key=header.count)


def _parse_spreadsheet(content: bytes) -> ParsedFile:
    """xlsx via calamine: Rust-backed, streaming, and it does NOT evaluate
    formulas — which is what makes a spreadsheet read reproducible enough to
    golden-test."""
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:  # pragma: no cover - declared in requirements/core.txt
        raise ParseError(f"the spreadsheet parser is unavailable: {exc}") from None

    try:
        workbook = CalamineWorkbook.from_filelike(BytesIO(content))
        sheet = workbook.get_sheet_by_index(0).to_python(skip_empty_area=True)
    except Exception as exc:
        raise ParseError(f"not a readable spreadsheet: {exc}") from None

    if not sheet:
        raise ParseError("the workbook's first sheet is empty")

    columns = tuple(str(cell).strip() for cell in sheet[0])
    rows = [
        [cell_to_text(cell) for cell in row]
        for row in sheet[1:]
        if any(str(cell).strip() for cell in row)
    ]
    for index, row in enumerate(rows, start=2):
        if len(row) != len(columns):
            raise ParseError(
                f"row {index} has {len(row)} cells but the header declares {len(columns)}"
            )
    return _to_arrow(columns, rows)


def cell_to_text(cell: object) -> str:
    """Every value becomes a string, losslessly where it matters.

    A float that is a whole number is written without its ".0": spreadsheet
    readers report an integer member id as 1000042.0, and that string would
    fail to match the same member arriving from a csv.
    """
    match cell:
        case None:
            return ""
        case bool():
            return "true" if cell else "false"
        case float() if cell.is_integer():
            return str(int(cell))
        case _:
            return str(cell).strip()


def _to_arrow(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> ParsedFile:
    """Build the Arrow table. All strings, by design.

    Casting is a PLAN STEP against the approved contract, so a cast failure is
    an attributed drop with a rule behind it — not a parser exception that
    loses the row before anything could count it.
    """
    data = {name: [row[index] for row in rows] for index, name in enumerate(columns)}
    table = pa.table(
        {name: pa.array(values, type=pa.string()) for name, values in data.items()}
        if rows
        else {name: pa.array([], type=pa.string()) for name in columns}
    )
    return ParsedFile(table=table, columns=tuple(columns), row_count=len(rows))
