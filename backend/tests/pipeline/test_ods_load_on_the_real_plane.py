"""CF-V3-E8-05 — the ODS load worker, against the REAL Postgres twin.

    "Happy path — Given a resolved enrollment batch, when the ODS stage
     runs, then members update in place ... every row carries its batch and
     source identifiers, and the certification gate receives balanced
     counts."
    — CF-V3-E8-05

Proven against the REAL harvested `MEMBER_DOMAIN_V1`/`MEMBER_MAPPING_V1`,
real casting (`cast_value` against real Postgres types), and real control
tables — not the mock plane `test_ods_load_worker.py` already covers.

WHAT THIS DOES NOT RE-PROVE. Identity resolution (`CrosswalkEntry` objects
are constructed by hand here, never through a real `IdentityWorker`/Verato
round trip — that is `tests/contract/test_control_tables_contract.py`'s and
`workers/identity.py`'s own test's job) and Silver Raw ingestion
(`tests/pipeline/test_golden_roster.py`'s job). This is the one stage this
story adds, proven on its own against the real plane it writes to.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_ods_load import PostgresOdsLoad
from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.registry.ods_model_member_domain import MEMBER_DOMAIN_V1
from cinqflow.core.registry.ods_model_member_mapping import MEMBER_MAPPING_V1
from cinqflow.installer.ods_model import provision_ods_model
from cinqflow.ports.control_tables import BatchControl
from cinqflow.workers.ods_load import OdsLoadWorker

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

BATCH_ID = "e8-05-live-8842"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
BUSINESS_DATE = date(2026, 9, 1)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_system": "fidelis",
        "source_member_id": "F-LIVE-1",
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
        "source_member_id": "F-LIVE-1",
        "internal_member_id": "",
        "verato_person_id": "LNK-LIVE-1",
        "batch_id": BATCH_ID,
        "outcome": MatchOutcome.RESOLVED,
    }
    defaults.update(overrides)
    return CrosswalkEntry(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def rig(plane: object) -> tuple[OdsLoadWorker, PostgresControlTables]:
    provision_ods_model(plane, MEMBER_DOMAIN_V1)
    control = PostgresControlTables(plane)
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
    worker = OdsLoadWorker(ods=PostgresOdsLoad(plane), control=control)
    return worker, control


def test_a_brand_new_member_is_inserted_with_a_real_minted_surrogate_key(
    rig: tuple[OdsLoadWorker, PostgresControlTables], plane: object
) -> None:
    worker, control = rig
    recon = worker.load_members(
        batch_id=BATCH_ID,
        model=MEMBER_DOMAIN_V1,
        mapping=MEMBER_MAPPING_V1,
        loadable=(_entry(),),
        silver_raw_rows=(_row(),),
        business_date=BUSINESS_DATE,
        now=NOW,
    )
    assert recon.balances
    assert recon.records_in == recon.records_out == 1

    row = plane.fetch_one(  # type: ignore[attr-defined]
        'SELECT "FirstName", "LastName", "LinkId", "DateOfBirth", "SourceSystem", "BatchId" '
        'FROM "silver_ods"."Members" WHERE "SourceSystem" = %s AND "BatchId" = %s',
        ("fidelis", BATCH_ID),
    )
    assert row == ("Ana", "Diaz", "LNK-LIVE-1", date(1990, 1, 1), "fidelis", BATCH_ID)

    (status,) = [s for s in control.get_stages(BATCH_ID) if s.stage is Layer.SILVER_ODS]
    assert (status.records_in, status.records_out) == (1, 1)
    assert control.get_batch(BATCH_ID).model_version == "1"


def test_reloading_the_same_batch_does_not_duplicate_the_row(
    rig: tuple[OdsLoadWorker, PostgresControlTables], plane: object
) -> None:
    """A genuinely new member has no legacy `OurId` to make the surrogate
    key stable on its own — the worker writes the freshly minted key back
    to the crosswalk, the same idempotency seam `IdentityWorker.
    resolve_batch` already relies on (a fresh `list_crosswalk` read sees
    what the LAST run assigned, exactly as a real re-processed batch would)."""
    worker, control = rig
    entry = _entry(source_member_id="F-LIVE-2")
    row = _row(source_member_id="F-LIVE-2")
    for _ in range(2):
        worker.load_members(
            batch_id=BATCH_ID,
            model=MEMBER_DOMAIN_V1,
            mapping=MEMBER_MAPPING_V1,
            loadable=(entry,),
            silver_raw_rows=(row,),
            business_date=BUSINESS_DATE,
            now=NOW,
        )
        entry = control.get_crosswalk(
            source_system="fidelis", source_member_id="F-LIVE-2", batch_id=BATCH_ID
        )
        assert entry is not None
    count = plane.fetch_one(  # type: ignore[attr-defined]
        'SELECT count(*) FROM "silver_ods"."Members" WHERE "SourceSystem" = %s '
        'AND "BatchId" = %s AND "LinkId" = %s',
        ("fidelis", BATCH_ID, "LNK-LIVE-1"),
    )
    assert count == (1,)


def test_a_legacy_our_id_is_reused_against_the_real_table(
    rig: tuple[OdsLoadWorker, PostgresControlTables], plane: object
) -> None:
    worker, _ = rig
    worker.load_members(
        batch_id=BATCH_ID,
        model=MEMBER_DOMAIN_V1,
        mapping=MEMBER_MAPPING_V1,
        loadable=(_entry(source_member_id="F-LIVE-3", internal_member_id="900123"),),
        silver_raw_rows=(_row(source_member_id="F-LIVE-3"),),
        business_date=BUSINESS_DATE,
        now=NOW,
    )
    row = plane.fetch_one(  # type: ignore[attr-defined]
        'SELECT "OurId" FROM "silver_ods"."Members" WHERE "SourceSystem" = %s AND "BatchId" = %s '
        'AND "OurId" = %s',
        ("fidelis", BATCH_ID, 900123),
    )
    assert row == (900123,)
