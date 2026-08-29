"""CF-V1-E5-03 — the value-shape library, against real published codes.

Every example below is a REAL code from its standard, not a string shaped like
one. That distinction is the point of the suite: a regex tested only against
strings somebody invented to match it is a regex that tests its author's
imagination.

The suite is organised around the module's one claim — that a `discriminating`
shape settles a column and a shared shape does not — and the last section is
the negative half: the shapes that must NOT be decisive, each with the real
thing it would otherwise be confused with.
"""

from __future__ import annotations

import pytest

from cinqflow.core.patterns import (
    BY_ID,
    PATTERN_IDS,
    PATTERNS,
    CodeSet,
    IdentifierShape,
    loinc_check,
    luhn_ok,
    matching_patterns,
    mbi_check,
    npi_check,
)

pytestmark = pytest.mark.unit


def _hits(text: str) -> set[str]:
    return {p.pattern_id for p in matching_patterns(text)}


def _decisive(text: str) -> set[str]:
    return {p.pattern_id for p in matching_patterns(text) if p.discriminating}


# ── check digits ─────────────────────────────────────────────────────────────


def test_luhn_agrees_with_a_worked_example() -> None:
    """The textbook case, so a broken doubling loop cannot pass the NPI tests
    by coincidence."""
    assert luhn_ok("79927398713")
    assert not luhn_ok("79927398710")


@pytest.mark.parametrize("npi", ["1234567893", "1841293990", "1215930367"])
def test_a_valid_npi_passes_its_check_digit(npi: str) -> None:
    """Ten digits AND the CMS checksum over the 80840 prefix."""
    assert npi_check(npi)


def test_ten_digits_are_not_an_npi(caplog: pytest.LogCaptureFixture) -> None:
    """The whole reason the check digit is here.

    `1234567890` is ten digits and reads exactly like an NPI to any regex. It
    is not one, and 90% of ten-digit numbers are not — which is what turns a
    ten-digit column from "a provider identifier" into "a member id, probably".
    """
    _ = caplog
    assert not npi_check("1234567890")
    assert not npi_check("1235186266")
    assert "npi" not in _decisive("1234567890")


def test_an_npi_check_refuses_the_wrong_length_and_non_digits() -> None:
    assert not npi_check("123456789")
    assert not npi_check("12345678931")
    assert not npi_check("12345678AB")


def test_mbi_follows_the_cms_position_alphabet() -> None:
    """S, L, O, I, B and Z never appear in an alphabetic position."""
    assert mbi_check("1EG4TE5MK73")
    assert mbi_check("1EG4-TE5-MK73"), "the hyphenated form is the same identifier"
    # Position 2 must be alpha, and S is excluded.
    assert not mbi_check("1SG4TE5MK73")
    # Position 1 must be a non-zero digit.
    assert not mbi_check("0EG4TE5MK73")
    # Eleven characters, exactly.
    assert not mbi_check("1EG4TE5MK7")


def test_loinc_carries_a_mod_ten_check_digit() -> None:
    """`4548-4` is haemoglobin A1c — a real code, with a real check digit."""
    assert loinc_check("4548-4")
    assert not loinc_check("4548-5")
    assert not loinc_check("4548")


# ── the decisive shapes ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "pattern_id"),
    [
        ("1841293990", "npi"),
        ("1EG4TE5MK73", "mbi"),
        ("4548-4", "loinc"),
        ("078-05-1120", "ssn"),
        ("member@cinqcare.test", "email"),
        ("10.0.12.4", "ip_address"),
        ("0002-7510-01", "ndc_hyphenated"),
        ("E11.9", "icd_10_cm_dotted"),
        ("02134-1234", "postal_code_plus_four"),
        ("617-555-0142", "phone_us"),
        ("(212) 555-0198", "phone_us"),
        ("123456789A", "hicn"),
        ("207Q00000X", "taxonomy"),
    ],
)
def test_a_real_code_matches_its_shape_decisively(value: str, pattern_id: str) -> None:
    assert pattern_id in _decisive(value), f"{value!r} should be decisively {pattern_id}"


# ── the shapes that must NOT be decisive ─────────────────────────────────────
#
# Each case below is a real collision. A first draft of this library called
# every one of them decisive, and each would have produced a confident wrong
# answer about a column of a payer's file.


def test_a_bare_ten_digit_number_is_not_decisively_a_phone_number() -> None:
    """The defect the smoke test caught: a column of valid NPIs read as phone
    numbers, because a phone regex with optional separators matches ten bare
    digits. A separator is now required."""
    assert "phone_us" not in _hits("1841293990")
    assert _decisive("1841293990") == {"npi"}


def test_a_compact_icd_code_is_not_decisive_because_it_is_also_a_member_id() -> None:
    """`A0001` is cholera written without its point, AND a perfectly ordinary
    member id, AND a real HCPCS ambulance code. Three readings, no arithmetic
    that separates them — so nothing here is allowed to settle it."""
    hits = _hits("A0001")
    assert "icd_10_cm" in hits and "hcpcs" in hits
    assert _decisive("A0001") == set(), "no shape may claim this value on its own"


def test_five_digits_are_a_zip_code_and_a_cpt_code_at_the_same_time() -> None:
    """`02134` is Boston's ZIP. `02134` is also a valid CPT code. The library
    reports both and settles neither, which is the honest answer."""
    hits = _hits("02134")
    assert {"postal_code_us", "cpt"} <= hits
    assert _decisive("02134") == set()


def test_a_five_digit_zip_never_reaches_a_computed_phi_classification() -> None:
    """Consequence of the previous test, stated as the property that matters.

    Under HIPAA Safe Harbor a five-digit ZIP is not itself an identifier in
    most cases — but the reason it cannot settle a column here is simpler and
    stronger: it is five digits, and so are several code sets.
    """
    zip_pattern = BY_ID["postal_code_us"]
    assert zip_pattern.identifier is IdentifierShape.POSTAL_CODE_US
    assert not zip_pattern.discriminating


def test_an_eleven_digit_ndc_is_not_decisive() -> None:
    """The hyphenated form is unmistakable; the compact form is eleven digits,
    which is also an internal claim number."""
    assert "ndc_11" in _hits("00027510012")
    assert _decisive("00027510012") == set()


# ── structure ────────────────────────────────────────────────────────────────


def test_every_pattern_has_a_label_and_a_lane() -> None:
    """A pattern with no code set and no identifier shape belongs to nothing,
    and a caller would have nowhere to put its meaning."""
    for pattern in PATTERNS:
        assert pattern.label, pattern.pattern_id
        assert (pattern.code_set is not None) ^ (pattern.identifier is not None), (
            f"{pattern.pattern_id} must be exactly one of a code set or an identifier shape"
        )


def test_pattern_ids_are_unique_and_ordered() -> None:
    """The profiler's fingerprint depends on this order, so a duplicate id or a
    reshuffle is a change to every stored profile's identity."""
    assert len(set(PATTERN_IDS)) == len(PATTERN_IDS)
    assert tuple(BY_ID) == PATTERN_IDS


def test_every_declared_code_set_has_at_least_one_pattern() -> None:
    """A member of the vocabulary nothing can detect is a promise the platform
    does not keep."""
    covered = {p.code_set for p in PATTERNS if p.code_set}
    assert covered == set(CodeSet)


def test_every_identifier_shape_has_at_least_one_pattern() -> None:
    covered = {p.identifier for p in PATTERNS if p.identifier}
    assert covered == set(IdentifierShape)


def test_a_code_set_is_never_phi() -> None:
    """ "A code set identifies a clinical concept, not a person."

    Asserted over the whole enum rather than spot-checked, because the
    tempting edit is to make ONE of them True — usually the NPI — and that
    would mask every provider column in the estate.
    """
    for code_set in CodeSet:
        assert not code_set.is_phi, f"{code_set} must not be PHI: masking it breaks reporting"


def test_a_pattern_with_a_check_digit_requires_both_the_shape_and_the_check() -> None:
    """Shape alone is never enough where a check exists."""
    npi = BY_ID["npi"]
    assert npi.regex.fullmatch("1234567890"), "the shape fits"
    assert not npi.matches("1234567890"), "but the check digit does not"
