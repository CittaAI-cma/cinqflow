"""CF-V1-E5-03 — the value-shape library: what a healthcare code actually looks like.

    "PHI & healthcare code-set detection (Presidio + glossary flags + pattern
     library)"
    — CF-V1-E5-03

    "PHI must be known at contract time, not discovered in production."
    — CF-V1-E5-03, why it exists

THE DESIGN DECISION, and it is the same one `core/profiling` makes:

    A PATTERN MATCH IS ARITHMETIC. A PATTERN'S MEANING IS NOT.

`0342` matches the revenue-code shape, the DRG shape and the four-digit-integer
shape. `10101` is a valid CPT code and a valid ZIP code and a Chicago plan
code. Nothing in this module resolves that — it counts, exactly, how many of a
column's values fit each declared shape, and hands the counts on. Deciding
WHICH shape a column holds is `core/phi`'s job where the evidence settles it,
and the model's question where it does not.

CHECK DIGITS ARE WHAT MAKE SOME OF THIS DECIDABLE. An NPI is not "ten digits";
it is ten digits whose Luhn checksum over `80840` + the first nine is the
tenth. A column of ten-digit numbers where every value passes that check is an
NPI by computation — the probability of 200 random ten-digit numbers all
passing is 10**-200. A column where 9% pass is a member id. That distinction
costs one function and removes a whole class of guess, so every code set that
HAS a check digit gets one here.

`discriminating` is the other half of the same idea. A shape that only a code
set of that kind can have (NPI with its checksum, an MBI's positional
alphabet, an email address) is decisive on its own. A shape that many things
share (five digits, three digits, a four-digit integer) is EVIDENCE ONLY, and
this module says which is which rather than leaving each caller to guess.

Nothing here is a regex on a column NAME. Names are the least reliable signal
in the estate — `MEM_ID`, `MBR_ID`, `MEMBERID` and `ID_MEMBER` are the same
field from four payers — and the platform already has a better answer for
names: the client's own 171-term glossary and its 99 PHI column spellings.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class CodeSet(StrEnum):
    """The healthcare code sets this estate carries.

    Closed, like every vocabulary in the platform. Adding a member means a
    pattern, a test with REAL example codes, and — where the standard has one
    — its check digit.
    """

    NPI = "npi"
    ICD_10_CM = "icd_10_cm"
    ICD_10_PCS = "icd_10_pcs"
    CPT = "cpt"
    HCPCS = "hcpcs"
    NDC = "ndc"
    LOINC = "loinc"
    DRG = "drg"
    REVENUE_CODE = "revenue_code"
    PLACE_OF_SERVICE = "place_of_service"
    TAXONOMY = "taxonomy"

    @property
    def label(self) -> str:
        return {
            CodeSet.NPI: "National Provider Identifier",
            CodeSet.ICD_10_CM: "ICD-10-CM diagnosis code",
            CodeSet.ICD_10_PCS: "ICD-10-PCS procedure code",
            CodeSet.CPT: "CPT procedure code",
            CodeSet.HCPCS: "HCPCS Level II code",
            CodeSet.NDC: "National Drug Code",
            CodeSet.LOINC: "LOINC observation code",
            CodeSet.DRG: "Diagnosis Related Group",
            CodeSet.REVENUE_CODE: "UB-04 revenue code",
            CodeSet.PLACE_OF_SERVICE: "CMS place-of-service code",
            CodeSet.TAXONOMY: "NUCC provider taxonomy code",
        }[self]

    @property
    def is_phi(self) -> bool:
        """A code set is not PHI. What it is ABOUT sometimes is.

        A diagnosis code attached to a member is protected health information
        in every practical sense — but the protection attaches to the ROW, not
        to the code column, and masking a diagnosis column breaks every
        clinical report the platform exists to produce. So this is False for
        every code set, deliberately, and the PHI decision is made about
        identifiers in `core/phi` where it belongs.

        The exception that proves it: an NPI identifies a PROVIDER, and a
        provider is not the patient whose record this is. HIPAA's eighteen
        identifiers are identifiers of the individual.
        """
        return False


@unique
class IdentifierShape(StrEnum):
    """Shapes that identify a PERSON. These are the ones that carry PHI risk.

    Named for the SHAPE, not the meaning — `SSN_SHAPE` rather than `SSN` —
    because this module only ever establishes that values fit a form. Whether
    the column IS a social security number is a classification, and it is made
    one layer up with the glossary and the column's name in hand.
    """

    SSN = "ssn"
    MBI = "mbi"
    HICN = "hicn"
    EMAIL = "email"
    PHONE_US = "phone_us"
    POSTAL_CODE_US = "postal_code_us"
    POSTAL_CODE_PLUS_FOUR = "postal_code_plus_four"
    IP_ADDRESS = "ip_address"

    @property
    def label(self) -> str:
        return {
            IdentifierShape.SSN: "US Social Security Number",
            IdentifierShape.MBI: "Medicare Beneficiary Identifier",
            IdentifierShape.HICN: "legacy Medicare Health Insurance Claim Number",
            IdentifierShape.EMAIL: "email address",
            IdentifierShape.PHONE_US: "US telephone number",
            IdentifierShape.POSTAL_CODE_US: "US ZIP code",
            IdentifierShape.POSTAL_CODE_PLUS_FOUR: "US ZIP+4 code",
            IdentifierShape.IP_ADDRESS: "IP address",
        }[self]


# ── check digits ─────────────────────────────────────────────────────────────
def luhn_ok(digits: str) -> bool:
    """The Luhn (mod-10) checksum, right to left, doubling every second digit."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def npi_check(value: str) -> bool:
    """An NPI is Luhn-valid over `80840` + its first nine digits.

    The `80840` prefix is the NPI's assigned ISO 7812 issuer identifier, and
    including it is what stops every Luhn-valid credit-card-shaped number from
    reading as a provider. CMS publishes the rule; this is it, unchanged.
    """
    if len(value) != 10 or not value.isdigit():
        return False
    return luhn_ok("80840" + value)


def loinc_check(value: str) -> bool:
    """LOINC's mod-10 check digit, over the part before the hyphen."""
    body, _, digit = value.partition("-")
    if not body.isdigit() or len(digit) != 1 or not digit.isdigit():
        return False
    return luhn_ok(body + digit)


#: MBI position alphabet. CMS excludes S, L, O, I, B and Z from every
#: alphabetic position, because each is confusable with a digit in handwriting
#: — which is exactly why the shape is decisive: a random eleven-character
#: string almost never satisfies it.
_MBI_ALPHA = "ACDEFGHJKMNPQRTUVWXY"
_MBI_POSITIONS: tuple[str, ...] = (
    "123456789",  # 1  non-zero digit
    _MBI_ALPHA,  # 2  alpha
    _MBI_ALPHA + "0123456789",  # 3  alphanumeric
    "0123456789",  # 4  digit
    _MBI_ALPHA,  # 5  alpha
    _MBI_ALPHA + "0123456789",  # 6  alphanumeric
    "0123456789",  # 7  digit
    _MBI_ALPHA,  # 8  alpha
    _MBI_ALPHA,  # 9  alpha
    "0123456789",  # 10 digit
    "0123456789",  # 11 digit
)


def mbi_check(value: str) -> bool:
    """CMS's eleven-position Medicare Beneficiary Identifier layout."""
    text = value.replace("-", "").replace(" ", "").upper()
    if len(text) != len(_MBI_POSITIONS):
        return False
    return all(char in allowed for char, allowed in zip(text, _MBI_POSITIONS, strict=True))


# ── the patterns ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Pattern:
    """One declared value shape, and whether fitting it settles anything.

    `discriminating` is the field that matters. It says: if EVERY populated
    value in a column fits this shape, is that on its own enough to name the
    column's contents? True only where the shape is improbable by accident — a
    check digit, a positional alphabet, an `@`. False for every bare run of
    digits, however suggestive, because a column of five-digit numbers is a
    CPT code, a ZIP code and a plan code with equal enthusiasm.
    """

    pattern_id: str
    label: str
    regex: re.Pattern[str]
    discriminating: bool
    code_set: CodeSet | None = None
    identifier: IdentifierShape | None = None
    check: Callable[[str], bool] | None = None
    note: str = ""

    def matches(self, text: str) -> bool:
        """Shape first, then the check digit. Both, or it is not a match."""
        if not self.regex.fullmatch(text):
            return False
        return self.check is None or self.check(text)


def _p(
    pattern_id: str,
    regex: str,
    *,
    discriminating: bool,
    code_set: CodeSet | None = None,
    identifier: IdentifierShape | None = None,
    check: Callable[[str], bool] | None = None,
    note: str = "",
    flags: int = 0,
) -> Pattern:
    label = (
        code_set.label
        if code_set is not None
        else identifier.label
        if identifier is not None
        else pattern_id
    )
    return Pattern(
        pattern_id=pattern_id,
        label=label,
        regex=re.compile(regex, flags),
        discriminating=discriminating,
        code_set=code_set,
        identifier=identifier,
        check=check,
        note=note,
    )


#: Evaluated and reported in THIS order, so two runs of the profiler list a
#: column's pattern matches identically and the fingerprint reproduces.
#:
#: Read the `discriminating` column as the specification it is: it is the only
#: thing separating "the platform knows what this column holds" from "the
#: platform has a hint and must ask".
PATTERNS: tuple[Pattern, ...] = (
    # ── decisive: a check digit or an alphabet accident cannot fake ──────────
    _p(
        "npi",
        r"\d{10}",
        discriminating=True,
        code_set=CodeSet.NPI,
        check=npi_check,
        note="ten digits AND Luhn-valid over the 80840 prefix",
    ),
    _p(
        "mbi",
        r"[0-9A-Za-z]{11}|[0-9A-Za-z]{4}-[0-9A-Za-z]{3}-[0-9A-Za-z]{4}",
        discriminating=True,
        identifier=IdentifierShape.MBI,
        check=mbi_check,
        note="CMS's eleven-position alphabet, which excludes S L O I B Z",
    ),
    _p(
        "loinc",
        r"\d{1,5}-\d",
        discriminating=True,
        code_set=CodeSet.LOINC,
        check=loinc_check,
        note="mod-10 check digit after the hyphen",
    ),
    _p(
        "ssn",
        # The excluded ranges are the ones the SSA never issues. Without them
        # every nine-digit id with two hyphens reads as a social security
        # number, and a member id formatted 123-45-6789 is not unheard of.
        r"(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}",
        discriminating=True,
        identifier=IdentifierShape.SSN,
        note="hyphenated, with the SSA's never-issued ranges excluded",
    ),
    _p(
        "email",
        r"[^@\s]+@[^@\s.]+\.[^@\s]+",
        discriminating=True,
        identifier=IdentifierShape.EMAIL,
    ),
    _p(
        "ip_address",
        r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)",
        discriminating=True,
        identifier=IdentifierShape.IP_ADDRESS,
    ),
    _p(
        "ndc_hyphenated",
        r"\d{4,5}-\d{3,4}-\d{1,2}",
        discriminating=True,
        code_set=CodeSet.NDC,
        note="4-4-2, 5-3-2, 5-4-1 or 5-4-2 segments",
    ),
    _p(
        # THE DOT IS THE DISCRIMINATOR, and the split below is the whole
        # reason this module distinguishes decisive shapes from suggestive
        # ones. `E11.9` is a diagnosis code and nothing else. `A0001` — the
        # same code written without its point — is ALSO a perfectly ordinary
        # member id, and a first draft of this library called it a diagnosis
        # with total confidence. Two patterns, one decisive, one not.
        "icd_10_cm_dotted",
        r"[A-TV-Z]\d[0-9AB]\.[0-9A-TV-Z]{1,4}",
        discriminating=True,
        code_set=CodeSet.ICD_10_CM,
        note="letter, digit, digit-or-A-or-B, decimal point, then the extension",
        flags=re.IGNORECASE,
    ),
    _p(
        "postal_code_plus_four",
        r"\d{5}-\d{4}",
        discriminating=True,
        identifier=IdentifierShape.POSTAL_CODE_PLUS_FOUR,
        note="ZIP+4 identifies a delivery point, not a region",
    ),
    _p(
        "phone_us",
        # A SEPARATOR IS REQUIRED. Written without one, a US telephone number
        # is ten bare digits — which is also every NPI, and was: the first
        # draft of this pattern reported a column of valid NPIs as decisively
        # a phone number. A bare ten-digit column is genuinely ambiguous and
        # belongs below the line with the other bare digit runs.
        r"(?:\+?1[-. ])?(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-. ]\d{4}",
        discriminating=True,
        identifier=IdentifierShape.PHONE_US,
        note="ten digits WITH separators; bare ten digits are deliberately not counted",
    ),
    _p(
        "hicn",
        r"\d{9}[A-Z]{1,2}\d?",
        discriminating=True,
        identifier=IdentifierShape.HICN,
        note="the pre-2018 Medicare number: an SSN with a beneficiary suffix",
    ),
    _p(
        "taxonomy",
        r"\d{2}[0-9A-Z]{7}X",
        discriminating=True,
        code_set=CodeSet.TAXONOMY,
        note="ten characters, terminal X",
    ),
    # ── evidence only: shapes many different things share ───────────────────
    #
    # Every entry below is a run of digits of some length. Not one of them can
    # settle a column on its own, and saying so HERE is what stops a caller
    # concluding that a five-digit column is a CPT code because a regex agreed.
    _p(
        # Both spellings, so a column mixing `E11.9` with `I10` — which every
        # real claims extract does — still reports one shape fitting every
        # value. The dotted pattern above narrows the same column further
        # wherever the payer is consistent; here the two work as a pair.
        "icd_10_cm",
        r"[A-TV-Z]\d[0-9AB](?:\.?[0-9A-TV-Z]{1,4})?",
        discriminating=False,
        code_set=CodeSet.ICD_10_CM,
        note="dotted or compact — and the compact form is also a member id",
        flags=re.IGNORECASE,
    ),
    _p(
        "icd_10_pcs",
        # A-H, J-N, P-Z and the digits: the standard's alphabet excludes only
        # I and O, for the same handwriting reason the MBI does. Seven
        # characters of it is not a rare shape, so this is evidence only.
        r"[0-9A-HJ-NP-Z]{7}",
        discriminating=False,
        code_set=CodeSet.ICD_10_PCS,
        note="seven characters from an alphabet excluding I and O",
        flags=re.IGNORECASE,
    ),
    _p(
        "cpt",
        r"\d{4}[0-9FTMU]",
        discriminating=False,
        code_set=CodeSet.CPT,
        note="five characters — and so is a ZIP code",
    ),
    _p(
        "hcpcs",
        r"[A-CEGHJ-MP-V]\d{4}",
        discriminating=False,
        code_set=CodeSet.HCPCS,
        note="a letter and four digits — and so are many internal plan codes",
    ),
    _p(
        "ndc_11",
        r"\d{11}",
        discriminating=False,
        code_set=CodeSet.NDC,
        note="eleven bare digits — indistinguishable from an eleven-digit id",
    ),
    _p(
        "drg",
        r"\d{3}",
        discriminating=False,
        code_set=CodeSet.DRG,
        note="three digits — and so is a place-of-service pair with a prefix",
    ),
    _p(
        "revenue_code",
        r"0\d{3}",
        discriminating=False,
        code_set=CodeSet.REVENUE_CODE,
        note="four digits with a leading zero",
    ),
    _p(
        "place_of_service",
        r"\d{2}",
        discriminating=False,
        code_set=CodeSet.PLACE_OF_SERVICE,
        note="two digits — and so is every small enumeration in the estate",
    ),
    _p(
        "postal_code_us",
        r"\d{5}",
        discriminating=False,
        identifier=IdentifierShape.POSTAL_CODE_US,
        note=(
            "five digits. Under HIPAA Safe Harbor a five-digit ZIP is not itself "
            "an identifier unless its population is under 20,000 — but it is five "
            "digits, which is also a CPT code, so this settles nothing either way"
        ),
    ),
)

#: By id, for the profiler's accumulator and for reading a stored profile back.
BY_ID: dict[str, Pattern] = {p.pattern_id: p for p in PATTERNS}

#: The order patterns are counted and reported in. Fixed, and the profiler's
#: fingerprint depends on it.
PATTERN_IDS: tuple[str, ...] = tuple(p.pattern_id for p in PATTERNS)


def matching_patterns(text: str) -> tuple[Pattern, ...]:
    """Every declared shape one value fits. Usually more than one.

    Exposed for tests and for a single-value explanation on a screen — the
    profiler counts over a whole column and does not call this.
    """
    return tuple(p for p in PATTERNS if p.matches(text))
