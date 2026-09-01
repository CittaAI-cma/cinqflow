"""CF-V1-E5-01 on the REAL rung-0.5 plane, over the REAL simulator's files.

The unit suite proves the arithmetic; this proves the arithmetic survives
contact with the two things that actually hold it — the Postgres row and the
files the Payer Simulator generates from the estate's own layouts.

    "Every Wave 0-3 demo is simulator-driven end to end, with ZERO hand-placed
     files. A demo that needs a human to drop a file is a demo that hides the
     connector."
    — src/cinqflow/simulator/__init__.py (ADR-0011)

So the fixtures here are not written for the occasion: every file profiled
below is one `PayerSimulator` produces, including the seeded failures, which
means the quirk tests are re-derivations rather than approximations.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core.profiling import Quirk, RefusalReason, TypeName, profile_bytes
from cinqflow.core.schema_spec import TypeName as SpecTypeName
from cinqflow.simulator import FIDELIS_DOWNSTATE_ROSTER, Injection, PayerSimulator
from cinqflow.workers.profiler import Profiler
from tests.conftest import require_corpus

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

BUSINESS_DATE = date(2026, 8, 1)
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = "dev-ba@cinqcare.test"
FEED = FIDELIS_DOWNSTATE_ROSTER.feed_id


@pytest.fixture
def simulator() -> PayerSimulator:
    return PayerSimulator(rows=200)


@pytest.fixture
def landing(tmp_path) -> LocalFsStorage:  # type: ignore[no-untyped-def]
    return LocalFsStorage(root=str(tmp_path))


def _profiler(plane: object, landing: LocalFsStorage) -> Profiler:
    return Profiler(storage=landing, metadata=PostgresMetadataDb(plane))  # type: ignore[arg-type]


# ── the happy delivery ───────────────────────────────────────────────────────


def test_a_simulated_roster_profiles_into_postgres_and_reads_back(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)

    profiler = _profiler(plane, landing)
    record = profiler.profile(
        feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW
    )

    assert record.profile.structure.data_rows == 200
    assert record.profile.structure.column_count == 8
    assert record.profile.would_load is True

    # ...and it round-trips through the JSONB column unchanged.
    stored = PostgresMetadataDb(plane).get_profile(record.profile_id, FEED)  # type: ignore[arg-type]
    assert stored.profile == record.profile
    assert stored.profiled_by == BA


def test_the_roster_s_key_column_is_found_with_its_evidence(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """`MemberID` is the layout's declared key. The profiler must reach that
    conclusion from the data alone — it is not told."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    primary = profile.primary_key_candidates[0]
    assert primary.columns == (FIDELIS_DOWNSTATE_ROSTER.key_column,)
    assert (primary.distinct_count, primary.populated_rows, primary.null_rows) == (200, 200, 0)


def test_the_seeded_null_first_names_are_counted_exactly(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The simulator seeds exactly five. DQ-002 — "Member First Name Not Null"
    — is the canonical quarantine reason in the stories, and this is the number
    a BA sees before writing that rule."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    first_name = profile.column("First_Name")
    assert first_name is not None
    assert first_name.null_count == simulator.null_first_names == 5


def test_the_profile_types_match_what_the_caster_will_accept(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The roster's dates are YYYYMMDD, which is also eight digits. The
    profiler reports both readings and refuses to choose — that refusal is
    CF-V1-E5-02's question, handed over with the evidence to answer it."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    dob = profile.column("DOB")
    assert dob is not None
    assert set(dob.total_match_types) == {TypeName.DATE, TypeName.INT64}
    assert dob.date_formats[0].label == "YYYYMMDD"
    assert dob.narrowest_type is None
    assert SpecTypeName is TypeName, "one type vocabulary, shared with the DDL spec"


# ── the seeded failure library, profiled ─────────────────────────────────────


def test_the_bad_encoding_injection_is_refused_with_an_explanation(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The simulator's latin-1 `JOSÉ MARÍA` in a feed declared utf-8 — a real
    payer export saved with a regional default."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE, injection=Injection.BAD_ENCODING)
    landing.place(delivery.key, delivery.content)

    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    assert profile.readable is False
    assert profile.refusal is not None
    assert profile.refusal.reason is RefusalReason.UNDECODABLE
    assert profile.refusal.ask_the_payer


def test_the_drifted_schema_injection_shows_up_as_a_missing_column(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """A contracted column simply stops arriving. Structurally the file is
    fine — which is why this needs the profile, not a size or parse check."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE, injection=Injection.DRIFTED_SCHEMA)
    landing.place(delivery.key, delivery.content)

    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    assert profile.readable is True
    assert profile.would_load is True, "the FILE is fine; the contract is what disagrees"
    assert profile.column("First_Name") is None
    assert profile.structure.column_count == 7


def test_the_duplicate_member_injection_is_counted_not_crashed_on(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    delivery = simulator.deliver(business_date=BUSINESS_DATE, injection=Injection.DUPLICATE_MEMBER)
    landing.place(delivery.key, delivery.content)

    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    assert profile.duplicates.duplicate_rows == 1
    assert any(f.quirk is Quirk.DUPLICATE_ROW for f in profile.findings)
    member = next(k for k in profile.key_candidates if k.columns == ("MemberID",))
    assert member.is_unique is False, "the declared key repeats in this delivery"


def test_the_truncated_injection_profiles_and_reports_its_size(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """A partial delivery PARSES perfectly, which is exactly why it reaches
    production. The row count is what gives it away."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE, injection=Injection.TRUNCATED)
    landing.place(delivery.key, delivery.content)

    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    assert profile.readable is True
    assert profile.would_load is True
    assert profile.structure.data_rows == 3, "a tenth of a roster, and nothing structural objects"


# ── the replay and restart proofs ────────────────────────────────────────────


def test_re_profiling_the_same_delivery_writes_no_second_row(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The replay proof, on the real store: ON CONFLICT DO NOTHING, and the
    original timestamp survives."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profiler = _profiler(plane, landing)

    first = profiler.profile(
        feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW
    )
    second = profiler.profile(
        feed_id=FEED,
        file_key=delivery.key,
        file_format="csv",
        profiled_by="somebody-else@cinqcare.test",
        now=datetime(2026, 9, 30, tzinfo=UTC),
    )

    assert second.profile_id == first.profile_id
    assert second.profiled_ts == first.profiled_ts
    assert second.profiled_by == BA, "the first writer keeps the row"
    assert len(PostgresMetadataDb(plane).list_profiles(feed_id=FEED)) == 1  # type: ignore[arg-type]


def test_a_re_delivered_month_under_a_new_name_is_the_same_file(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """Incident #4: the Feb-2025 roster, delivered twice under different names.
    Content-addressed profiling recognises it; a name-based check would not."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    resend = simulator.deliver_duplicate(delivery)
    landing.place(delivery.key, delivery.content)
    landing.place(resend.key, resend.content)

    profiler = _profiler(plane, landing)
    profiler.profile(
        feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW
    )
    already = profiler.already_profiled(feed_id=FEED, file_key=resend.key)
    assert already is not None, "byte-identical content under a different name"


def test_a_fifty_thousand_row_delivery_profiles_within_the_story_s_bound(
    plane: object, landing: LocalFsStorage
) -> None:
    """ "A 50MB sample in minutes with progress."

    50k rows is a real roster's order of magnitude. The bound here is generous
    on purpose — this asserts the profiler is linear and unhurried, not that a
    particular machine is fast.
    """
    import time

    big = PayerSimulator(rows=50_000)
    delivery = big.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)

    seen: list[int] = []
    started = time.monotonic()
    record = _profiler(plane, landing).profile(
        feed_id=FEED,
        file_key=delivery.key,
        file_format="csv",
        profiled_by=BA,
        now=NOW,
        progress=lambda p: seen.append(p.rows_read),
    )
    elapsed = time.monotonic() - started

    assert record.profile.structure.data_rows == 50_000
    assert seen[-1] == 50_000 and len(seen) > 1, "progress was actually reported"
    assert elapsed < 120, f"50k rows took {elapsed:.1f}s"


def test_the_stored_profile_is_the_evidence_an_approval_packet_carries(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """CF-V1-E11-02's packet takes `evidence`; this is what step 1 puts in it —
    the claim and the address to open it, never a copy of the file."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )

    evidence = profile.as_evidence()
    assert evidence["profile_id"] == profile.fingerprint
    assert evidence["rows"] == 200
    assert evidence["would_load"] is True
    assert str(profile.citation) == f"profile:{profile.fingerprint}"
    assert "examples" not in evidence, "an approval packet carries the claim, not the values"


def test_profiling_a_file_that_is_not_there_does_not_write_a_row(
    plane: object, landing: LocalFsStorage
) -> None:
    from cinqflow.workers.profiler import ProfileTargetMissingError

    with pytest.raises(ProfileTargetMissingError):
        _profiler(plane, landing).profile(
            feed_id=FEED, file_key="enrollments/nothing.csv", file_format="csv", profiled_by=BA
        )
    assert PostgresMetadataDb(plane).list_profiles(feed_id=FEED) == ()  # type: ignore[arg-type]


def test_an_unprofiled_file_never_appears_in_the_store(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The store answers "have we profiled this?" by fingerprint, so a file
    that was merely delivered must not look profiled."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    profiler = _profiler(plane, landing)
    assert profiler.already_profiled(feed_id=FEED, file_key=delivery.key) is None


def test_the_profiler_never_moves_or_edits_the_sample(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """ "Modify the sample" is a documented don't, and profiling is a
    design-time READ — the storage port has no write verb to break it with."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)
    before = landing.fingerprint(delivery.key)

    _profiler(plane, landing).profile(
        feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW
    )

    assert landing.exists(delivery.key), "still where the connector left it"
    assert landing.fingerprint(delivery.key) == before
    assert landing.read_bytes(delivery.key) == delivery.content


def test_a_workbook_delivery_profiles_its_typed_cells(
    plane: object, landing: LocalFsStorage
) -> None:
    """The estate's rosters arrive as .xlsx as often as .csv, and a member id
    stored as a number comes back 1000042.0 — which stops matching the same
    member arriving in a CSV."""
    openpyxl = pytest.importorskip("openpyxl")
    from io import BytesIO

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["MemberID", "First_Name", "DOB", "Amount"])
    sheet.append([1000042, "FIRST000042", date(1990, 1, 1), 12.5])
    sheet.append([1000043, "FIRST000043", date(1985, 6, 15), 3.75])
    buffer = BytesIO()
    workbook.save(buffer)

    key = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/roster.xlsx"
    landing.place(key, buffer.getvalue())
    profile = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=key, file_format="xlsx", profiled_by=BA, now=NOW)
        .profile
    )

    assert profile.readable is True
    member = profile.column("MemberID")
    assert member is not None
    assert member.examples == ("1000042", "1000043"), "no '.0' — the id still matches a CSV's"
    assert member.typed_cell_count == 2
    assert any(f.quirk is Quirk.TYPED_CELL for f in profile.findings)


def test_the_client_s_own_workbook_profiles_without_special_handling(
    plane: object, landing: LocalFsStorage
) -> None:
    """The real `Data lake data model.xlsx` — 171 rows of business glossary
    written by the client's analysts, profiled as an ordinary spreadsheet.

    Skips when the corpus is absent, like every other test that reads it: the
    repository is useful without `clientdata/`.
    """
    from pathlib import Path

    workbook = (
        Path(__file__).resolve().parents[3]
        / "clientdata"
        / "Uploads"
        / "2-Design"
        / "Data lake data model.xlsx"
    )
    require_corpus(workbook)

    key = "reference/design/data-lake-data-model.xlsx"
    landing.place(key, workbook.read_bytes())
    profile = (
        _profiler(plane, landing)
        .profile(
            feed_id="reference-glossary", file_key=key, file_format="xlsx", profiled_by=BA, now=NOW
        )
        .profile
    )

    assert profile.readable is True, profile.refusal
    assert profile.structure.data_rows > 0
    assert profile.columns, "a real 20-column business workbook, read without ceremony"
    key_candidate = profile.primary_key_candidates
    assert key_candidate, "the glossary's id column is unique, and the profiler found it"


def test_the_profile_survives_the_json_column_including_its_refusal(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """A lossy read would let a stale-evidence gate compare a full profile
    against a partial one and call the difference a change."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE, injection=Injection.BAD_ENCODING)
    landing.place(delivery.key, delivery.content)
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]

    written = _profiler(plane, landing).profile(
        feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW
    )
    read_back = store.get_profile(written.profile_id, FEED)

    assert read_back.profile == written.profile
    assert read_back.profile.refusal == written.profile.refusal
    assert read_back.profile.fingerprint == written.profile.fingerprint


def test_a_hand_built_profile_and_a_stored_one_agree(
    plane: object, landing: LocalFsStorage, simulator: PayerSimulator
) -> None:
    """The pure function and the whole composition must produce the same
    facts, or the worker is doing something to the bytes on the way past."""
    delivery = simulator.deliver(business_date=BUSINESS_DATE)
    landing.place(delivery.key, delivery.content)

    stored = (
        _profiler(plane, landing)
        .profile(feed_id=FEED, file_key=delivery.key, file_format="csv", profiled_by=BA, now=NOW)
        .profile
    )
    direct = profile_bytes(
        delivery.content,
        file_format="csv",
        source_key=delivery.key,
        source_fingerprint=landing.fingerprint(delivery.key),
    )
    assert stored == direct
