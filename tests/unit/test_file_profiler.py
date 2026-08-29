"""CF-V1-E5-01 — the deterministic profiler, and the quirks it must survive.

    "quirk fixtures: BOM, quoted delimiters, Excel typed cells, ragged rows ->
     reported, never crashed · reproducibility: identical stats on re-run ·
     unreadable file -> plain-language explanation"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1, tests written first

    "Modify the sample; send sample data anywhere except storage the BA's role
     can access." (the documented don'ts)

THE NEGATIVES COME FIRST, because for this story they ARE the acceptance
criteria: the profiler's whole claim is that nothing makes it crash and nothing
makes it lie.
"""

from __future__ import annotations

import pytest

from cinqflow.core.profiling import (
    PLAUSIBLE_YEARS,
    Quirk,
    RefusalReason,
    TypeName,
    date_format_of,
    profile_bytes,
    suggest_contract_columns,
)
from cinqflow.core.registry.contract import CastFailureError, ContractColumn, cast_value

pytestmark = pytest.mark.unit

#: The Fidelis downstate roster's real layout, with synthetic members — the
#: same shape `simulator.FIDELIS_DOWNSTATE_ROSTER` delivers.
ROSTER = (
    b"MemberID,First_Name,Last_Name,DOB,Gender,LOB,EffDate,EndDate\n"
    b"MBR000001,FIRST000001,LAST000001,19360201,M,MEDICAID,20260101,\n"
    b"MBR000002,,LAST000002,19370302,F,MEDICARE,20260101,\n"
    b"MBR000003,FIRST000003,LAST000003,19380403,U,DUAL,20260101,\n"
)


# ── nothing makes it crash ───────────────────────────────────────────────────


def test_a_ragged_row_is_reported_and_the_rest_of_the_file_still_profiles() -> None:
    """The quirk that matters most: `core/parsers.parse` RAISES here, and it is
    right to. The profiler must survive and explain, or a BA learns about three
    bad rows at publication instead of at upload."""
    ragged = b"a,b,c\n1,2,3\n4,5\n6,7,8,9\n10,11,12\n"
    profile = profile_bytes(ragged, file_format="csv")

    assert profile.readable is True
    assert profile.structure.data_rows == 4, "every row was profiled, including the bad ones"
    finding = next(f for f in profile.findings if f.quirk is Quirk.RAGGED_ROW)
    assert finding.occurrences == 2
    assert finding.first_lines == (3, 4)
    assert finding.blocks_ingestion is True
    assert profile.would_load is False, "the strict reader will refuse this — say so now"


def test_a_bom_is_consumed_and_still_reported() -> None:
    """Excel's "CSV UTF-8" writes one on every save. Left in place it becomes
    part of the FIRST COLUMN'S NAME, and drift detection then reports a
    removed column and an added one on a perfectly good file."""
    profile = profile_bytes(b"\xef\xbb\xbf" + ROSTER, file_format="csv")

    assert profile.columns[0].name == "MemberID", "the BOM must not become part of the name"
    assert profile.structure.byte_order_mark == "UTF-8"
    finding = next(f for f in profile.findings if f.quirk is Quirk.BYTE_ORDER_MARK)
    assert finding.blocks_ingestion is False
    assert profile.would_load is True


def test_a_quoted_delimiter_stays_one_field() -> None:
    """`"Smith, John"` is one name, not two fields — and a delimiter detector
    that counts commas in the header gets this wrong."""
    quoted = b'MemberID,Name,LOB\nMBR000001,"Smith, John",MEDICAID\n'
    profile = profile_bytes(quoted, file_format="csv")

    assert profile.structure.column_count == 3
    assert profile.structure.delimiter == ","
    assert profile.column("Name") is not None
    assert profile.column("Name").examples == ("Smith, John",)  # type: ignore[union-attr]
    assert any(f.quirk is Quirk.QUOTED_DELIMITER for f in profile.findings)


def test_a_pipe_delimited_file_is_detected_by_consistency_not_by_counting() -> None:
    """The estate uses comma, pipe and tab. Consistency scoring picks the one
    that yields the same field count on every row."""
    piped = b"MemberID|Name|LOB\nMBR000001|SMITH, JOHN|MEDICAID\nMBR000002|DOE, JANE|MEDICARE\n"
    profile = profile_bytes(piped, file_format="csv")

    assert profile.structure.delimiter == "|"
    assert profile.structure.column_count == 3
    evidence = {e.delimiter: e for e in profile.structure.delimiter_evidence}
    assert evidence["|"].fields_per_row == 3
    assert evidence[","].fields_per_row < 3, "the losing candidate is reported too"


def test_a_duplicated_header_name_blocks_ingestion() -> None:
    """The platform reads by name. Two columns with one name cannot be told
    apart, so this is a blocker rather than a note."""
    profile = profile_bytes(b"id,dob,dob\n1,19900101,19900102\n", file_format="csv")

    finding = next(f for f in profile.findings if f.quirk is Quirk.DUPLICATE_HEADER)
    assert finding.blocks_ingestion is True
    assert finding.columns == ("dob",)
    assert profile.would_load is False


def test_whole_row_duplicates_are_counted_with_their_lines() -> None:
    """The same member twice in one file — an ordinary delivery fault that must
    become an attributed drop, not a failed batch."""
    doubled = ROSTER + b"MBR000001,FIRST000001,LAST000001,19360201,M,MEDICAID,20260101,\n"
    profile = profile_bytes(doubled, file_format="csv")

    assert profile.duplicates.duplicate_rows == 1
    assert profile.duplicates.duplicate_groups == 1
    assert profile.duplicates.first_lines == (5,)
    assert any(f.quirk is Quirk.DUPLICATE_ROW for f in profile.findings)


def test_a_null_spelled_as_text_is_not_counted_as_a_null() -> None:
    """A literal "NULL" is four characters. A completeness rule counts it as
    populated, and the column loads the word — which is worth saying before
    somebody writes that rule."""
    profile = profile_bytes(b"id,notes\n1,NULL\n2,N/A\n3,real note\n", file_format="csv")

    notes = profile.column("notes")
    assert notes is not None
    assert notes.null_count == 0, "they are not empty"
    assert notes.null_like_count == 2
    assert any(f.quirk is Quirk.NULL_LIKE_TOKEN for f in profile.findings)


# ── nothing makes it lie ─────────────────────────────────────────────────────


def test_profiling_the_same_bytes_twice_is_identical() -> None:
    """ "Profiling statistics exactly reproducible on re-run of the same file."

    Asserted on the whole value, not just the fingerprint: a digest that
    matched while the reported statistics differed would be a worse failure
    than either alone.
    """
    first = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")
    second = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.profile_id == first.fingerprint


def test_one_changed_value_changes_the_fingerprint() -> None:
    """A fingerprint that did not move when the file did would make the
    stale-evidence gate a decoration."""
    changed = ROSTER.replace(b"MEDICAID", b"MEDICARE")
    assert (
        profile_bytes(ROSTER, file_format="csv").fingerprint
        != profile_bytes(changed, file_format="csv").fingerprint
    )


def test_redacting_the_values_does_not_move_the_fingerprint() -> None:
    """The property the whole redaction design rests on.

    A steward reading a masked packet and the BA who ran the profile must be
    provably looking at the same evidence — so `without_values` may remove
    values and must not touch the facts.
    """
    profile = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")
    redacted = profile.without_values()

    assert redacted.fingerprint == profile.fingerprint
    assert redacted.values_redacted is True
    assert all(column.examples == () for column in redacted.columns)
    assert all(column.min_value is None for column in redacted.columns)
    assert all(key.examples == () for key in redacted.key_candidates)
    # ...and the statistics survived untouched.
    assert [c.distinct_count for c in redacted.columns] == [
        c.distinct_count for c in profile.columns
    ]


def test_the_profile_survives_a_round_trip_through_storage() -> None:
    """A lossy read would let a stale-evidence gate compare a full profile
    against a partial one and call the difference a change."""
    profile = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")
    from cinqflow.core.profiling import FileProfile

    assert FileProfile.from_dict(profile.to_dict()) == profile


def test_the_profiler_never_names_a_date_format_the_caster_would_reject() -> None:
    """The correspondence that makes the profile trustworthy downstream.

    A profiler whose idea of a date is wider than `cast_value`'s tells the BA
    their file is fine and the pipeline that it is not — and the disagreement
    surfaces as attributed drops on a feed somebody already approved.
    """
    column = ContractColumn(name="DOB", type=TypeName.DATE)
    for text in ("19900101", "1990-01-01", "1/1/1990", "12/31/2099"):
        assert date_format_of(text) is not None, text
        cast_value(text, column)  # must not raise

    for rejected in ("10000101", "17530101", "1990-13-01", "notadate", "20260230"):
        assert date_format_of(rejected) is None, rejected
        with pytest.raises(CastFailureError):
            cast_value(rejected, column)


def test_implausible_legacy_dates_are_not_called_dates() -> None:
    """Incident #8: service months of 1000-01 and 1753-01 are in the estate. A
    date that parses is not the same as a date that is possible."""
    assert 1900 in PLAUSIBLE_YEARS and 1899 not in PLAUSIBLE_YEARS
    profile = profile_bytes(b"svc\n10000101\n17530101\n", file_format="csv")
    column = profile.column("svc")
    assert column is not None
    assert TypeName.DATE not in column.total_match_types
    assert column.narrowest_type is TypeName.INT64, "eight digits, but not a date"
    assert column.date_formats == (), "no format claimed for a year outside 1900-2100"


# ── the facts the next story interprets ──────────────────────────────────────


def test_a_column_reports_every_type_that_fits_with_its_counts() -> None:
    """Never a verdict. `matched/considered` is checkable arithmetic, which is
    what lets CF-V1-E5-02 cite a number instead of asserting one."""
    profile = profile_bytes(ROSTER, file_format="csv")
    dob = profile.column("DOB")
    assert dob is not None

    by_type = {c.type: c for c in dob.type_candidates}
    assert by_type[TypeName.DATE].matched == 3
    assert by_type[TypeName.DATE].considered == 3
    assert by_type[TypeName.STRING].matched == 3, "string always fits, and says so"
    assert (by_type[TypeName.DATE].share, by_type[TypeName.DATE].is_total) == (1.0, True)
    assert dob.date_formats[0].label == "YYYYMMDD"
    assert dob.date_formats[0].matched == 3


def test_two_types_fitting_equally_is_reported_as_needing_input() -> None:
    """A column of 1s and 0s is honestly both a bool and an int64. Picking one
    would be the silent typing CF-V1-E5-02 is forbidden to do."""
    profile = profile_bytes(b"flag\n1\n0\n1\n", file_format="csv")
    flag = profile.column("flag")
    assert flag is not None
    # DECIMAL is collapsed away — every whole number is a decimal, and that is
    # containment rather than ambiguity. BOOL vs INT64 is the real question.
    assert set(flag.total_match_types) == {TypeName.INT64, TypeName.BOOL}
    assert flag.narrowest_type is None
    assert suggest_contract_columns(profile)[0]["needs_input"] is True


def test_a_contained_type_is_collapsed_rather_than_reported_as_a_rival() -> None:
    """Every whole number is also a decimal. Listing both as candidates would
    make an ordinary integer column look undecidable."""
    profile = profile_bytes(b"line_no\n11\n22\n33\n", file_format="csv")
    column = profile.column("line_no")
    assert column is not None
    assert {c.type for c in column.type_candidates if c.is_total} >= {
        TypeName.INT64,
        TypeName.DECIMAL,
    }, "the raw evidence still records both"
    assert column.total_match_types == (TypeName.INT64,), "but only one is a real candidate"
    assert column.narrowest_type is TypeName.INT64


def test_a_compact_date_is_left_undecided_between_date_and_integer() -> None:
    """`19360201` is a valid date AND a valid member id, and neither type
    contains the other. The estate writes dates this way constantly, so the
    temptation is to prefer DATE — and this module is the one place that never
    guesses. CF-V1-E5-02 resolves it from the column name and the glossary, and
    it gets the evidence to do so.
    """
    profile = profile_bytes(ROSTER, file_format="csv")
    dob = profile.column("DOB")
    assert dob is not None
    assert set(dob.total_match_types) == {TypeName.DATE, TypeName.INT64}
    assert dob.narrowest_type is None

    suggested = {c["source_name"]: c for c in suggest_contract_columns(profile)}
    assert suggested["DOB"]["needs_input"] is True
    assert suggested["DOB"]["date_formats"] == ["YYYYMMDD"], "with the evidence to decide on"


def test_a_column_nothing_narrower_fits_is_determined_to_be_a_string() -> None:
    """STRING here is a determination, not a fallback: nothing narrower fits,
    so the file has said what the type is and nobody needs asking."""
    profile = profile_bytes(ROSTER, file_format="csv")
    member = profile.column("MemberID")
    assert member is not None
    assert member.narrowest_type is TypeName.STRING
    assert suggest_contract_columns(profile)[0]["needs_input"] is False


def test_an_entirely_empty_column_is_not_typed_at_all() -> None:
    """`EndDate` is empty in every sample row. Typing it from no evidence is
    exactly the guess the deterministic-first rule exists to prevent."""
    profile = profile_bytes(ROSTER, file_format="csv")
    end_date = profile.column("EndDate")
    assert end_date is not None
    assert end_date.null_count == 3
    assert end_date.narrowest_type is None


def test_decimal_columns_report_precision_and_scale() -> None:
    """`schema_spec.Column` REFUSES a decimal without them — "an undeclared
    decimal is where money quietly changes between engines"."""
    profile = profile_bytes(b"amt\n12.50\n1234.5\n0.00\n", file_format="csv")
    amount = profile.column("amt")
    assert amount is not None
    assert amount.narrowest_type is TypeName.DECIMAL
    assert (amount.observed_precision, amount.observed_scale) == (6, 2)


def test_nullability_comes_from_the_null_count() -> None:
    profile = profile_bytes(ROSTER, file_format="csv")
    suggested = {c["source_name"]: c for c in suggest_contract_columns(profile)}
    assert suggested["MemberID"]["nullable"] is False
    assert suggested["First_Name"]["nullable"] is True, "one row has no first name"


# ── candidate keys, with their evidence ──────────────────────────────────────


def test_the_key_column_is_a_candidate_and_carries_its_counts() -> None:
    profile = profile_bytes(ROSTER, file_format="csv")
    member = next(k for k in profile.key_candidates if k.columns == ("MemberID",))

    assert member.is_unique is True
    assert (member.distinct_count, member.populated_rows, member.null_rows) == (3, 3, 0)
    assert member.duplicate_values == 0
    assert profile.primary_key_candidates[0].columns == ("MemberID",)


def test_a_repeated_key_is_refused_as_a_candidate_and_shows_the_offending_rows() -> None:
    """ "MemberID is not unique" is an assertion; the line numbers are what
    make it something a BA can look at."""
    doubled = ROSTER + b"MBR000001,OTHER,PERSON,19900101,F,DUAL,20260101,\n"
    profile = profile_bytes(doubled, file_format="csv")
    member = next(k for k in profile.key_candidates if k.columns == ("MemberID",))

    assert member.is_unique is False
    assert member.duplicate_values == 1
    assert member.examples[0][0] == "MBR000001"
    assert member.examples[0][1] == (2, 5)


def test_a_column_with_nulls_is_not_a_primary_key_even_when_it_never_repeats() -> None:
    """The two questions are different, and only one of them is about keys."""
    profile = profile_bytes(ROSTER, file_format="csv")
    first_name = profile.column("First_Name")
    assert first_name is not None
    assert first_name.is_unique is True, "no repeats among the populated values"

    candidate = next(k for k in profile.key_candidates if k.columns == ("First_Name",))
    assert candidate.null_rows == 1
    assert candidate.is_unique is False, "a key with a null row is not a key"


def test_a_composite_key_is_found_when_no_single_column_works() -> None:
    lines = (
        b"claim_id,line_no,amount\nCLM001,1,10.00\nCLM001,2,20.00\nCLM002,1,30.00\nCLM002,2,40.00\n"
    )
    profile = profile_bytes(lines, file_format="csv")

    assert not any(k.columns == ("claim_id",) and k.is_unique for k in profile.key_candidates)
    composite = next(k for k in profile.key_candidates if len(k.columns) == 2 and k.is_unique)
    assert composite.columns == ("claim_id", "line_no")
    assert composite.distinct_count == 4


def test_an_unexamined_pair_is_reported_rather_than_absorbed() -> None:
    """ "No composite key found" after examining 1 of 3 pairs is not the same
    statement as "no composite key exists"."""
    # Four columns, none unique on its own -> six pairs to consider.
    lines = b"a,b,c,d\n1,1,1,1\n1,2,2,2\n2,1,3,3\n2,2,1,1\n"
    profile = profile_bytes(lines, file_format="csv", max_composite_pairs=2)

    assert profile.key_search.pairs_examined == 2
    assert profile.key_search.pairs_skipped == 4
    assert "not examined" in profile.key_search.note


def test_a_column_past_the_distinct_cap_is_unknown_rather_than_not_unique() -> None:
    """Unknown is not False. A real primary key discarded because nobody
    counted it is the failure this guards."""
    rows = b"id\n" + b"".join(f"MBR{n:06d}\n".encode() for n in range(50))
    profile = profile_bytes(rows, file_format="csv", distinct_cap=10)

    column = profile.column("id")
    assert column is not None
    assert column.distinct_is_exact is False
    assert column.is_unique is None
    assert profile.key_candidates == (), "excluded from candidacy, not judged"
    assert profile.key_search.excluded_columns == ("id",)
    assert "unknown, not assumed non-unique" in profile.key_search.note


# ── the unreadable file ──────────────────────────────────────────────────────


def test_bad_encoding_is_explained_in_words_a_ba_can_forward() -> None:
    """The seeded failure, as a refusal. Never a stack trace, and never a
    decode with replacements — Bronze is append-only, and a mojibaked member
    name there is permanent."""
    latin1 = "MemberID,Name\nMBR000001,JOSÉ MARÍA\n".encode("latin-1")
    profile = profile_bytes(latin1, file_format="csv")

    assert profile.readable is False
    assert profile.refusal is not None
    assert profile.refusal.reason is RefusalReason.UNDECODABLE
    assert "É" in profile.refusal.explanation, "name the character, not just the byte"
    assert "encoding" in profile.refusal.ask_the_payer
    assert profile.would_load is False


def test_an_empty_file_is_a_refusal_not_a_zero_row_profile() -> None:
    """Reporting an empty file as "0 rows, 0 columns" invites somebody to
    approve a contract with no fields in it."""
    profile = profile_bytes(b"   \n", file_format="csv")
    assert profile.readable is False
    assert profile.refusal is not None
    assert profile.refusal.reason is RefusalReason.EMPTY_FILE


def test_an_unsupported_format_says_what_the_platform_reads_today() -> None:
    profile = profile_bytes(b"{}", file_format="fhir_ndjson")
    assert profile.readable is False
    assert profile.refusal is not None
    assert profile.refusal.reason is RefusalReason.NO_PARSER
    assert "later waves" in profile.refusal.explanation


def test_nothing_raises_on_any_of_the_hostile_inputs() -> None:
    """The blanket guarantee. An exception here reaches the wizard as a 500,
    and "something went wrong" is the one answer a BA cannot act on."""
    hostile = [
        b"",
        b"\x00\x01\x02",
        b"no header no rows",
        b"a,b\n",
        b'"unclosed quote,1\n2,3\n',
        b"a,a,a\n1,2,3\n",
        b",,\n,,\n",
        b"\xef\xbb\xbf",
        "héader,b\n1,2\n".encode(),
        b"a\r\n1\n2\r\n",
    ]
    for content in hostile:
        profile = profile_bytes(content, file_format="csv")
        assert isinstance(profile.fingerprint, str), content[:20]


# ── bounds, stated ───────────────────────────────────────────────────────────


def test_a_sampled_read_says_so() -> None:
    """A truncation nobody mentions reads as completeness."""
    rows = b"id,name\n" + b"".join(f"{n},NAME{n:05d}\n".encode() for n in range(2000))
    profile = profile_bytes(rows, file_format="csv", max_bytes=500)

    assert profile.structure.sampled is True
    assert profile.structure.bytes_read == 500
    assert profile.structure.bytes_total == len(rows)
    assert profile.structure.data_rows < 2000


def test_progress_is_reported_on_a_fixed_cadence() -> None:
    """ "A 50MB sample in minutes with progress". Emitted every N rows, so the
    sequence reproduces exactly like everything else here."""
    rows = b"id\n" + b"".join(f"{n}\n".encode() for n in range(250))
    seen: list[tuple[str, int]] = []
    profile_bytes(
        rows,
        file_format="csv",
        progress=lambda p: seen.append((p.phase, p.rows_read)),
        progress_every=100,
    )
    assert seen == [("scan", 100), ("scan", 200), ("done", 250)]


def test_the_profiler_reads_the_bytes_it_is_given_and_nothing_else() -> None:
    """ "Modify the sample" is a documented don't, and core/ performs no I/O at
    all — so the only thing to assert is that the bytes come back unchanged."""
    content = bytearray(ROSTER)
    before = bytes(content)
    profile_bytes(bytes(content), file_format="csv")
    assert bytes(content) == before


def test_a_leading_zero_means_the_value_is_a_code_not_a_number() -> None:
    """`02134` is a Boston ZIP; as an integer it is 2134, and the member now
    lives somewhere else.

    This is one of the classic ways a healthcare load corrupts data in silence
    — nothing errors, every row loads, and a code set stops matching. So the
    profiler refuses to call these numeric, and the contract that follows types
    them as strings.
    """
    profile = profile_bytes(
        b"rel_code,zip,plan,amount,qty\n01,02134,007,0.50,7\n02,10001,012,12.50,8\n",
        file_format="csv",
    )
    for coded in ("rel_code", "zip", "plan"):
        column = profile.column(coded)
        assert column is not None, coded
        assert column.narrowest_type is TypeName.STRING, coded
        assert TypeName.INT64 not in column.total_match_types, coded

    # ...and ordinary numbers are untouched.
    assert profile.column("amount").narrowest_type is TypeName.DECIMAL  # type: ignore[union-attr]
    assert profile.column("qty").narrowest_type is TypeName.INT64  # type: ignore[union-attr]


def test_a_bare_zero_and_a_leading_zero_decimal_are_still_numbers() -> None:
    """The rule is about SIGNIFICANT leading zeros. `0` and `0.50` are numbers
    written normally, and refusing them would make every money column a
    string."""
    profile = profile_bytes(b"n,m\n0,0.50\n5,-0.75\n", file_format="csv")
    assert profile.column("n").narrowest_type is TypeName.INT64  # type: ignore[union-attr]
    assert profile.column("m").narrowest_type is TypeName.DECIMAL  # type: ignore[union-attr]
