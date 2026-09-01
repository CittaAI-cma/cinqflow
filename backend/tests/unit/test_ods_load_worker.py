"""CF-V3-E8-05 — the ODS load worker, against the mock plane.

Proven against the REAL harvested `MEMBER_DOMAIN_V1` and its REAL canonical
mapping `MEMBER_MAPPING_V1` — not a fixture invented for this test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.ods_load import MemOdsLoad
from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.recon import UnattributedDropError
from cinqflow.core.registry.ods_model_member_domain import MEMBER_DOMAIN_V1
from cinqflow.core.registry.ods_model_member_mapping import MEMBER_MAPPING_V1
from cinqflow.ports.control_tables import BatchControl
from cinqflow.workers.ods_load import OdsLoadWorker

pytestmark = pytest.mark.unit

BATCH_ID = "batch-8842"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
BUSINESS_DATE = date(2026, 9, 1)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_system": "fidelis",
        "source_member_id": "F-1",
        "first_name": "Ana",
        "last_name": "Diaz",
        "date_of_birth": date(1990, 1, 1),
        "gender": "F",
        "is_active": True,
        "batch_id": BATCH_ID,
        "feed_id": "fidelis-downstate-roster",
    }
    row.update(overrides)
    return row


def _entry(**overrides: object) -> CrosswalkEntry:
    defaults: dict[str, object] = {
        "source_system": "fidelis",
        "source_member_id": "F-1",
        "internal_member_id": "",
        "verato_person_id": "LNK-1",
        "batch_id": BATCH_ID,
        "outcome": MatchOutcome.RESOLVED,
    }
    defaults.update(overrides)
    return CrosswalkEntry(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def worker() -> tuple[OdsLoadWorker, MemOdsLoad, MemStoreControlTables]:
    ods = MemOdsLoad()
    control = MemStoreControlTables()
    control.open_batch(
        BatchControl(
            batch_id=BATCH_ID,
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            business_date="2026-09-01",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    return OdsLoadWorker(ods=ods, control=control), ods, control


def _load(
    worker_tuple: tuple[OdsLoadWorker, MemOdsLoad, MemStoreControlTables],
    entries: tuple[CrosswalkEntry, ...],
    rows: tuple[dict[str, object], ...],
):
    worker, _, _ = worker_tuple
    return worker.load_members(
        batch_id=BATCH_ID,
        model=MEMBER_DOMAIN_V1,
        mapping=MEMBER_MAPPING_V1,
        loadable=entries,
        silver_raw_rows=rows,
        business_date=BUSINESS_DATE,
        now=NOW,
    )


def test_a_new_member_is_inserted_with_a_minted_surrogate_key(worker) -> None:
    recon = _load(worker, (_entry(),), (_row(),))
    assert recon.records_in == recon.records_out == 1
    assert recon.balances

    _, ods, _ = worker
    # A fresh MemOdsLoad mints 1 first — the one thing this test may assume
    # about the mock's own counter without reaching into its internals.
    loaded = ods.existing_current_row("Members", "OurId", 1)
    assert loaded["FirstName"] == "Ana"
    assert loaded["LastName"] == "Diaz"
    assert loaded["LinkId"] == "LNK-1"
    assert loaded["DateOfBirth"] == date(1990, 1, 1)


def test_a_freshly_minted_key_is_written_back_to_the_crosswalk(worker) -> None:
    """The idempotency seam: without this, a genuinely new member mints a
    DIFFERENT surrogate key every time this batch is re-processed, because
    nothing upstream ever learns the empty `internal_member_id` was
    filled — the same read `IdentityWorker.resolve_batch` relies on."""
    _load(worker, (_entry(),), (_row(),))
    _, _, control = worker
    written = control.get_crosswalk(
        source_system="fidelis", source_member_id="F-1", batch_id=BATCH_ID
    )
    assert written is not None
    assert written.internal_member_id == "1"


def test_a_legacy_our_id_does_not_trigger_a_crosswalk_write(worker) -> None:
    """Only a MINT is written back — a member who already carries a legacy
    id has nothing new for the crosswalk to learn."""
    _load(worker, (_entry(internal_member_id="482910"),), (_row(),))
    _, _, control = worker
    written = control.get_crosswalk(
        source_system="fidelis", source_member_id="F-1", batch_id=BATCH_ID
    )
    assert written is None


def test_a_legacy_our_id_is_reused_not_minted(worker) -> None:
    _load(worker, (_entry(internal_member_id="482910"),), (_row(),))
    _, ods, _ = worker
    loaded = ods.existing_current_row("Members", "OurId", 482910)
    assert loaded is not None
    assert loaded["OurId"] == 482910


def test_source_identifiers_are_preserved_alongside_the_surrogate_key(worker) -> None:
    """ "Preserve source identifiers on every row alongside surrogate
    keys" — SourceSystem is the one silver_raw genuinely carries."""
    _load(worker, (_entry(internal_member_id="1"),), (_row(source_system="fidelis"),))
    _, ods, _ = worker
    loaded = ods.existing_current_row("Members", "OurId", 1)
    assert loaded["SourceSystem"] == "fidelis"


def test_batch_and_lineage_columns_are_stamped_by_the_platform(worker) -> None:
    _load(worker, (_entry(internal_member_id="1"),), (_row(),))
    _, ods, _ = worker
    loaded = ods.existing_current_row("Members", "OurId", 1)
    assert loaded["BatchId"] == BATCH_ID
    assert loaded["FeedName"] == "fidelis-downstate-roster"
    assert loaded["CreatedBy"] == "cinqflow.ods_load"
    assert loaded["CreatedAt"] == NOW


def test_reloading_an_unchanged_row_is_a_no_op(worker) -> None:
    """Idempotency guardrail: the same input arriving twice is safely
    skipped — data is never duplicated, and never rewritten for nothing."""
    _load(worker, (_entry(internal_member_id="1"),), (_row(),))
    _, ods, _ = worker
    first_created_at = ods.existing_current_row("Members", "OurId", 1)["CreatedAt"]

    later = datetime(2026, 9, 2, tzinfo=UTC)
    worker[0].load_members(
        batch_id=BATCH_ID,
        model=MEMBER_DOMAIN_V1,
        mapping=MEMBER_MAPPING_V1,
        loadable=(_entry(internal_member_id="1"),),
        silver_raw_rows=(_row(),),
        business_date=BUSINESS_DATE,
        now=later,
    )
    reloaded = ods.existing_current_row("Members", "OurId", 1)
    assert reloaded["CreatedAt"] == first_created_at, "CreatedAt must never move on a re-load"


def test_a_changed_field_updates_the_row_in_place(worker) -> None:
    """ "Members update in place" — SCD-1, verbatim."""
    _load(worker, (_entry(internal_member_id="1"),), (_row(last_name="Diaz"),))
    control2 = MemStoreControlTables()
    control2.open_batch(
        BatchControl(
            batch_id="batch-2",
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            business_date="2026-09-02",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    worker_2 = OdsLoadWorker(ods=worker[1], control=control2)
    recon = worker_2.load_members(
        batch_id="batch-2",
        model=MEMBER_DOMAIN_V1,
        mapping=MEMBER_MAPPING_V1,
        loadable=(_entry(internal_member_id="1"),),
        silver_raw_rows=(_row(last_name="Reyes"),),
        business_date=BUSINESS_DATE,
        now=NOW,
    )
    assert recon.records_out == 1
    loaded = worker[1].existing_current_row("Members", "OurId", 1)
    assert loaded["LastName"] == "Reyes"


def test_a_row_with_no_matching_silver_raw_row_is_an_attributed_drop_not_a_crash(worker) -> None:
    recon = _load(worker, (_entry(internal_member_id="1"),), ())
    assert recon.records_in == 1
    assert recon.records_out == 0
    assert recon.attributed_drops == 1
    assert recon.drops[0].rule_id == "ODS-NO-SILVER-RAW-ROW"
    assert recon.balances


def test_the_stage_status_and_model_version_are_recorded(worker) -> None:
    _load(worker, (_entry(internal_member_id="1"),), (_row(),))
    _, _, control = worker
    (status,) = [s for s in control.get_stages(BATCH_ID) if s.stage is Layer.SILVER_ODS]
    assert status.records_in == status.records_out == 1
    assert control.get_batch(BATCH_ID).model_version == "1"


def test_an_unbalanced_reconciliation_raises_rather_than_hiding_the_gap() -> None:
    """Belt-and-suspenders: `reconcile()` itself refuses a hand-corrupted
    accounting, the same posture every other stage takes."""
    from cinqflow.core.recon import StageReconciliation, reconcile

    with pytest.raises(UnattributedDropError):
        reconcile(
            StageReconciliation(
                batch_id=BATCH_ID, stage=Layer.SILVER_ODS, records_in=5, records_out=3
            )
        )
