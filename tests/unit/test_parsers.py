"""Parsers — bytes in, Arrow out, identically on both planes."""

from __future__ import annotations

import io

import pyarrow as pa
import pytest

from cinqflow.core.parsers import ParseError, parse

pytestmark = pytest.mark.unit

CSV = b"member_id,first_name,date_of_birth\nMBR000001,ARUN,19900101\nMBR000002,PRIYA,1985-06-15\n"


def test_a_delimited_file_parses_to_arrow() -> None:
    parsed = parse(CSV, file_format="csv")
    assert parsed.columns == ("member_id", "first_name", "date_of_birth")
    assert parsed.row_count == 2
    assert isinstance(parsed.table, pa.Table)


def test_every_value_arrives_as_a_string() -> None:
    """Casting is a PLAN STEP against the approved contract, so a cast failure
    becomes an attributed drop with a rule behind it — rather than a parser
    exception that loses the row before anything could count it."""
    parsed = parse(CSV, file_format="csv")
    for field in parsed.table.schema:
        assert field.type == pa.string(), f"{field.name} was typed by the parser"
    assert parsed.table.column("date_of_birth").to_pylist() == ["19900101", "1985-06-15"]


def test_pipe_and_tab_delimiters_are_handled() -> None:
    """The estate uses all three. A wrong delimiter produces one giant column,
    which fails the contract check loudly on the very next step."""
    piped = b"member_id|first_name\nMBR000001|ARUN\n"
    assert parse(piped, file_format="csv").columns == ("member_id", "first_name")
    tabbed = b"member_id\tfirst_name\nMBR000001\tARUN\n"
    assert parse(tabbed, file_format="tsv").columns == ("member_id", "first_name")


def test_bad_encoding_is_rejected_with_a_stated_reason_not_mojibaked() -> None:
    """A seeded failure the simulator injects.

    "the file is REJECTED WITH A STATED REASON rather than silently mojibaked
    into Bronze" — and Bronze is append-only, so a mojibaked member name there
    cannot be corrected later.
    """
    latin1 = "member_id,first_name\nMBR000001,JOSÉ\n".encode("latin-1")
    with pytest.raises(ParseError) as caught:
        parse(latin1, file_format="csv")
    assert "not valid utf-8" in str(caught.value)
    assert "cannot be corrected later" in str(caught.value)


def test_a_row_with_the_wrong_field_count_is_a_stated_failure() -> None:
    """Landing accepted the file structurally; a row-level mismatch is G2's."""
    ragged = b"member_id,first_name\nMBR000001,ARUN,EXTRA\n"
    with pytest.raises(ParseError, match="row 2 has 3 fields"):
        parse(ragged, file_format="csv")


def test_duplicate_column_names_are_refused() -> None:
    """Two columns of one name means a mapping silently picks one of them."""
    with pytest.raises(ParseError, match="duplicate column names"):
        parse(b"member_id,member_id\nA,B\n", file_format="csv")


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ParseError, match="empty"):
        parse(b"   ", file_format="csv")


def test_a_header_only_file_parses_to_zero_rows() -> None:
    """Legitimate: a cycle with no members is news, not an error. It must reach
    reconciliation as 0 in = 0 out, so someone SEES the empty delivery."""
    parsed = parse(b"member_id,first_name\n", file_format="csv")
    assert parsed.row_count == 0
    assert parsed.columns == ("member_id", "first_name")


def test_trailing_blank_lines_are_not_records() -> None:
    """A trailing newline would otherwise inflate every count by one, and the
    balance equation would fail on a file that was perfectly fine."""
    assert parse(CSV + b"\n\n", file_format="csv").row_count == 2


def test_an_unknown_format_names_what_is_supported() -> None:
    with pytest.raises(ParseError, match="later waves"):
        parse(b"{}", file_format="fhir_ndjson")


def test_a_spreadsheet_parses_with_the_same_contract_as_a_csv() -> None:
    """The Fidelis roster is xlsx. Same parser contract, same Arrow output —
    which is what lets one pipeline run both formats from metadata."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["member_id", "first_name", "date_of_birth"])
    sheet.append([1000042, "ARUN", "19900101"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed = parse(buffer.getvalue(), file_format="xlsx")
    assert parsed.columns == ("member_id", "first_name", "date_of_birth")
    assert parsed.row_count == 1
    # 1000042, not "1000042.0" — a spreadsheet reader reports whole numbers as
    # floats, and that string would fail to match the same member from a csv.
    assert parsed.table.column("member_id").to_pylist() == ["1000042"]


def test_a_corrupt_spreadsheet_is_a_stated_failure() -> None:
    """The truncated-file injection lands here when the format is xlsx."""
    with pytest.raises(ParseError, match="not a readable spreadsheet"):
        parse(b"PK\x03\x04truncated-before-anything-useful", file_format="xlsx")


def test_parsers_perform_no_io() -> None:
    """ "file parsers run BEFORE the store" — they receive bytes from the
    storage adapter, and a parser that opened a file would put a path in the
    one place paths are forbidden."""
    import ast
    import inspect

    from cinqflow.core import parsers

    tree = ast.parse(inspect.getsource(parsers))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called


def test_a_byte_order_mark_does_not_become_part_of_the_first_column_name() -> None:
    """Excel's "CSV UTF-8" writes one on every save, and `str.strip()` does not
    remove it.

    Left in place, the first column arrives named `﻿member_id`: drift
    detection then reports the contracted column REMOVED and an unknown one
    ADDED, and a perfectly good file fails. CF-V1-E5-01's profiler still
    REPORTS the BOM — a payer who starts sending one has changed their export
    tool — but reading it correctly is not something a BA should have to
    arrange.
    """
    parsed = parse(b"\xef\xbb\xbf" + CSV, file_format="csv")
    assert parsed.columns == ("member_id", "first_name", "date_of_birth")
    assert parsed.row_count == 2


def test_a_bom_on_a_feed_declaring_another_encoding_is_not_silently_reinterpreted() -> None:
    """The BOM override applies only to a feed declared utf-8.

    A feed declared cp1252 that arrives with a utf-16 mark is a genuine
    disagreement between the registry and the payer. cp1252 decodes every byte,
    so nothing raises — and switching codecs underneath would make the
    disagreement invisible. Instead the mark survives into the data, where the
    profiler reports it (CF-V1-E5-01) and somebody settles which side is
    wrong.
    """
    parsed = parse(b"\xff\xfe" + CSV, file_format="csv", encoding="cp1252")
    assert parsed.columns[0] != "member_id", "the declared encoding was honoured, not overridden"
    assert "member_id" in parsed.columns[0], "and the disagreement is visible rather than hidden"
