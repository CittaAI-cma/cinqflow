"""CF-V1-E3-05 — the delivery contract, in isolation.

The whole of "where must this file land, and are these the bytes that were
promised" is a pure function, so it is exhaustively testable with no
filesystem, no connector and no credentials. That is the point of putting it in
`core`: seven connectors will share these answers, and a second connector
inventing its own key composition is how two ingestion paths appear.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.core.delivery import (
    ChecksumMismatchError,
    Delivery,
    DeliveryError,
    Manifest,
    UnsafeFilenameError,
    business_date_of,
    fingerprint_of,
    landing_key,
    safe_filename,
    verify_manifest,
)
from cinqflow.core.model.files import FileRef
from cinqflow.core.model.vocabulary import LandingFolder

pytestmark = pytest.mark.unit

LANDING = "enrollments/fidelis_downstate/roster"
ROSTER = b"MemberID,First_Name\nM001,Ada\n"


# ── the layout, spelled once ─────────────────────────────────────────────────


def test_the_key_follows_the_profiles_layout() -> None:
    """`{domain}/{source_system}/{feed}/{folder}/{business_date}` — and the
    feed's landing_path is already the first three."""
    assert (
        landing_key(landing_path=LANDING, filename="roster.csv", business_date="2026-09-01")
        == f"{LANDING}/incoming/2026-09-01/roster.csv"
    )


def test_a_delivery_lands_in_incoming_by_default() -> None:
    """Never straight to processed. Landing controls decide where it goes
    next, and a connector that could land into `processed/` would be deciding
    a file was acceptable."""
    key = landing_key(landing_path=LANDING, filename="r.csv", business_date="2026-09-01")
    assert "/incoming/" in key


def test_the_folder_can_be_named_for_the_move_that_follows() -> None:
    assert "/parked/" in landing_key(
        landing_path=LANDING,
        filename="r.csv",
        business_date="2026-09-01",
        folder=LandingFolder.PARKED,
    )


@pytest.mark.parametrize("messy", [f"/{LANDING}", f"{LANDING}/", f"  {LANDING}  "])
def test_a_landing_path_is_normalised_rather_than_doubling_a_separator(messy: str) -> None:
    assert (
        landing_key(landing_path=messy, filename="r.csv", business_date="2026-09-01")
        == f"{LANDING}/incoming/2026-09-01/r.csv"
    )


def test_a_feed_with_no_landing_path_is_refused_with_the_reason() -> None:
    with pytest.raises(DeliveryError) as refused:
        landing_key(landing_path="   ", filename="r.csv", business_date="2026-09-01")
    assert "nowhere for its files to land" in str(refused.value)


# ── the filename is a name, never a path ─────────────────────────────────────


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "../secrets.env",
        "sub/dir/roster.csv",
        "sub\\dir\\roster.csv",
        "..",
        ".",
        "",
        "    ",
        "roster\x00.csv",
        "roster\n.csv",
        "roster\x7f.csv",
    ],
)
def test_a_name_that_could_choose_the_write_path_is_refused(hostile: str) -> None:
    with pytest.raises(UnsafeFilenameError):
        safe_filename(hostile)


@pytest.mark.parametrize(
    "legal",
    [
        "roster.csv",
        "_CINQDOWNSTATE_Member_Roster_202608.xlsx",
        "claims-2026-09.ndjson",
        "a.csv",
        "A1.CSV",
    ],
)
def test_the_names_payers_actually_send_are_accepted(legal: str) -> None:
    assert safe_filename(legal) == legal


def test_the_refusal_says_what_a_portable_name_is() -> None:
    """A refusal a person can act on, not a regex they have to reverse."""
    with pytest.raises(UnsafeFilenameError) as refused:
        safe_filename("roster (final).csv")
    assert "Letters, digits, dot, dash and underscore" in str(refused.value)


def test_a_separator_is_refused_for_a_different_stated_reason() -> None:
    """Traversal and 'one folder deeper than the layout' are the same bug, and
    the message names the one the caller actually made."""
    with pytest.raises(UnsafeFilenameError) as refused:
        safe_filename("roster/2026.csv")
    assert "the platform composes the path" in str(refused.value)


def test_a_name_is_trimmed_rather_than_refused_for_stray_spaces() -> None:
    assert safe_filename("  roster.csv  ") == "roster.csv"


# ── the business date is the sender's, not the clock's ───────────────────────


def test_a_date_object_is_formatted_by_the_platform() -> None:
    """So nobody hand-formats it — `2026-9-1` and `2026-09-01` would be two
    folders holding one month."""
    assert business_date_of(date(2026, 9, 1)) == "2026-09-01"
    assert business_date_of(datetime(2026, 9, 1, 13, 0, tzinfo=UTC)) == "2026-09-01"


@pytest.mark.parametrize("bad", ["2026-9-1", "01/09/2026", "September", "", "2026-13-01"])
def test_a_date_that_is_not_the_layouts_spelling_is_refused(bad: str) -> None:
    with pytest.raises(DeliveryError):
        business_date_of(bad)


def test_the_date_refusal_explains_why_the_spelling_matters() -> None:
    with pytest.raises(DeliveryError) as refused:
        business_date_of("2026-9-1")
    assert "two folders" in str(refused.value)


# ── the manifest is optional, and checked where supplied ─────────────────────


def test_no_manifest_verifies_trivially() -> None:
    verify_manifest(Manifest(), fingerprint=fingerprint_of(ROSTER))


def test_a_matching_checksum_passes() -> None:
    verify_manifest(Manifest(checksum=fingerprint_of(ROSTER)), fingerprint=fingerprint_of(ROSTER))


def test_a_bare_hex_checksum_passes_without_the_prefix() -> None:
    """A sender quoting a plain sha256 should not have to know the platform's
    storage prefix."""
    bare = fingerprint_of(ROSTER).removeprefix("sha256-")
    verify_manifest(Manifest(checksum=bare), fingerprint=fingerprint_of(ROSTER))


def test_a_full_sha256_is_compared_on_the_prefix_the_platform_stores() -> None:
    """The pin truncates to 32 hex characters. A sender quoting all 64 is
    right, and must not be told they are wrong."""
    import hashlib

    full = hashlib.sha256(ROSTER).hexdigest()
    verify_manifest(Manifest(checksum=full), fingerprint=fingerprint_of(ROSTER))


def test_a_wrong_checksum_is_refused_and_says_why_nothing_landed() -> None:
    with pytest.raises(ChecksumMismatchError) as refused:
        verify_manifest(Manifest(checksum="sha256-" + "0" * 32), fingerprint=fingerprint_of(ROSTER))
    assert "look like a replay" in str(refused.value)


def test_a_negative_declared_row_count_is_refused() -> None:
    with pytest.raises(DeliveryError):
        Manifest(declared_row_count=-1)


# ── the receipt ──────────────────────────────────────────────────────────────


def _delivery(fingerprint: str | None = "sha256-abc") -> Delivery:
    return Delivery(
        file=FileRef(
            key=f"{LANDING}/incoming/2026-09-01/roster.csv",
            size_bytes=len(ROSTER),
            modified_ts=datetime(2026, 9, 1, tzinfo=UTC),
            fingerprint=fingerprint,
        ),
        feed_id="fidelis-downstate-roster",
        business_date="2026-09-01",
        delivered_by="upload-endpoint",
    )


def test_a_delivery_cites_itself_as_a_file() -> None:
    """No new citation kind was needed: a delivery IS a file, and
    `CitationKind.FILE` has meant that since Wave 0."""
    assert _delivery().citation == "file:sha256-abc"


def test_a_delivery_with_no_fingerprint_refuses_to_be_used() -> None:
    """Exactly-once ingestion is enforced on this value. A receipt without one
    is a file the platform could not refuse to process twice."""
    with pytest.raises(DeliveryError) as refused:
        _ = _delivery(fingerprint=None).fingerprint
    assert "exactly-once" in str(refused.value)


# ── the two fingerprints must never drift ────────────────────────────────────


def test_the_precomputed_fingerprint_has_the_storage_pins_shape() -> None:
    value = fingerprint_of(ROSTER)
    assert value.startswith("sha256-")
    assert len(value) == len("sha256-") + 32


def test_identical_bytes_fingerprint_identically_and_different_bytes_do_not() -> None:
    assert fingerprint_of(ROSTER) == fingerprint_of(b"MemberID,First_Name\nM001,Ada\n")
    assert fingerprint_of(ROSTER) != fingerprint_of(ROSTER + b" ")
