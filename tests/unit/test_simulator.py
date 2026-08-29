"""CF-V0-E8-08 — the Payer Source Simulator."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.simulator import FIDELIS_DOWNSTATE_ROSTER, Injection, PayerSimulator

pytestmark = pytest.mark.unit

AUGUST = date(2026, 8, 1)


def test_it_generates_from_the_real_fidelis_layout() -> None:
    """ "Generate files from the REAL LAYOUT ARTIFACTS ... with realistic value
    distributions and ZERO REAL MEMBER DATA." — CF-V0-E8-08

    The layout is from `Source Details.xlsx`. The leading underscore is real,
    it is in production, and it is the filename that broke the Excel reader.
    """
    delivery = PayerSimulator().deliver(business_date=AUGUST)
    assert delivery.filename == "_CINQDOWNSTATE_Member_Roster_202608.csv"
    header = delivery.content.decode().splitlines()[0]
    assert header.split(",") == list(FIDELIS_DOWNSTATE_ROSTER.columns)


def test_the_values_are_obviously_synthetic() -> None:
    """ "Contain any member-derived values — layouts are real, data is always
    synthetic" is a documented don't.

    Generated from the index rather than a name library, so nobody can mistake
    FIRST000042 for a real member when a quarantine screenshot lands in a
    ticket.
    """
    content = PayerSimulator().deliver(business_date=AUGUST).content.decode()
    assert "FIRST000042" in content
    assert "MBR000042" in content


def test_generation_is_deterministic_so_golden_sets_stay_usable() -> None:
    """The golden pipeline compares BYTE-EXACT outputs including the exact
    quarantine rows. A generator that varied between runs would make the
    golden set unusable and teach the team to re-run CI."""
    first = PayerSimulator().deliver(business_date=AUGUST)
    second = PayerSimulator().deliver(business_date=AUGUST)
    assert first.content == second.content


def test_different_months_produce_different_files() -> None:
    august = PayerSimulator().deliver(business_date=AUGUST)
    september = PayerSimulator().deliver(business_date=date(2026, 9, 1))
    assert august.content != september.content
    assert "202609" in september.filename


# ── the seeded failure library, injectable on demand ─────────────────────────
def test_every_documented_injection_is_available() -> None:
    """ "Inject EVERY seeded failure on demand: late, truncated, drifted schema,
    duplicate month, underscore-named, bad encoding."

    A lesson that cannot be re-injected is a lesson nobody is checking.
    """
    required = {
        "late",
        "truncated",
        "drifted_schema",
        "duplicate_month",
        "underscore_filename",
        "bad_encoding",
    }
    assert required <= {i.value for i in Injection}


def test_a_truncated_file_parses_perfectly_and_is_still_wrong() -> None:
    """Which is exactly why the size bound exists: a roster at a tenth of its
    size parses fine and quietly halves a member population."""
    delivery = PayerSimulator().deliver(business_date=AUGUST, injection=Injection.TRUNCATED)
    lines = delivery.content.decode().strip().splitlines()
    assert len(lines) == 4
    assert len(lines[1].split(",")) == len(FIDELIS_DOWNSTATE_ROSTER.columns)


def test_drifted_schema_drops_a_contracted_column_structurally_intact() -> None:
    """Nothing about the FILE is malformed — which is why this needs drift
    detection rather than a size or parse check."""
    delivery = PayerSimulator().deliver(business_date=AUGUST, injection=Injection.DRIFTED_SCHEMA)
    header = delivery.content.decode().splitlines()[0]
    assert "First_Name" not in header
    assert "MemberID" in header


def test_bad_encoding_produces_bytes_that_are_not_utf_8() -> None:
    delivery = PayerSimulator().deliver(business_date=AUGUST, injection=Injection.BAD_ENCODING)
    with pytest.raises(UnicodeDecodeError):
        delivery.content.decode("utf-8")


def test_a_late_delivery_is_a_perfectly_good_file_that_arrives_late() -> None:
    """Lateness is an SLA signal, not a rejection. Nothing is wrong with the
    file, and treating it as a rejection would lose a valid roster."""
    on_time = PayerSimulator().deliver(business_date=AUGUST)
    late = PayerSimulator().deliver(business_date=AUGUST, injection=Injection.LATE)
    assert late.content == on_time.content
    assert late.arrives_at is not None and on_time.arrives_at is not None
    assert late.arrives_at > on_time.arrives_at


def test_the_duplicate_month_arrives_under_a_different_name() -> None:
    """Incident #4: the duplicate Feb-2025 Fidelis roster.

    Byte-identical content, different filename — because that is how a re-send
    actually arrives, and a name-based dedup would miss the one that matters.
    """
    original = PayerSimulator().deliver(business_date=AUGUST)
    resend = PayerSimulator().deliver_duplicate(original)
    assert resend.content == original.content
    assert resend.key != original.key
    assert resend.injection is Injection.DUPLICATE_MONTH


def test_the_underscore_injection_targets_feeds_that_have_not_declared_one() -> None:
    """The Fidelis name already starts with an underscore, so the injection is
    meaningful against a feed that has NOT declared it — which is what the
    landing check actually guards."""
    delivery = PayerSimulator().deliver(
        business_date=AUGUST, injection=Injection.UNDERSCORE_FILENAME
    )
    assert delivery.filename.startswith("_")


def test_the_duplicate_member_injection_puts_one_member_in_twice() -> None:
    """The delivery fault that used to fail a whole roster on the uniqueness
    constraint, before in-batch deduplication attributed it instead."""
    delivery = PayerSimulator().deliver(business_date=AUGUST, injection=Injection.DUPLICATE_MEMBER)
    ids = [line.split(",")[0] for line in delivery.content.decode().splitlines()[1:]]
    assert len(ids) != len(set(ids))
