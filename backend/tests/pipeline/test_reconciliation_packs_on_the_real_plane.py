"""CF-V3-E13-02 — the member-universe pack, against the REAL Postgres twin.

    "Given the member universe shows 312 members present last month and
     absent now, unexplained by terminations, when the pack runs, then a
     variance opens with the 312 identified set-wise and linked to the
     suspect batch — an investigation, not a mystery."
    — CF-V3-E13-02

Proven against the REAL, already-deployed `MEMBER_DOMAIN_V1` and a REAL
`silver_ods."Members"` table — `column_values`'s own `SELECT DISTINCT`
against a real connection, not the mock's in-memory dict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.local.pg_ods_load import PostgresOdsLoad
from cinqflow.core.variance import VarianceKind, VarianceOutcome
from cinqflow.workers.reconciliation_packs import ReconciliationPacks

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

BATCH_ID = "e13-02-live-8842"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
OPENED_BY = "cinqflow.reconciliation_packs"


def _member_row(our_id: int) -> dict[str, object]:
    return {
        "OurId": our_id,
        "LinkId": f"LNK-{our_id}",
        "IsActive": True,
        "RecordHash": "abc",
        "FeedName": "fidelis-downstate-roster",
        "SourceSystem": "fidelis",
        "CreatedBy": "test",
        "CreatedAt": NOW,
        "BatchId": BATCH_ID,
    }


@pytest.fixture
def packs(plane: object) -> ReconciliationPacks:
    """SKIPS when the ODS model this pack reads has never been deployed here.

    Same precondition as `test_ods_certification_gate_on_the_real_plane.py`,
    and absent for the same reason: the deployed `silver_ods` model is created
    by PUBLISHING a domain through the API, which is a runtime act that
    `cinqflow install` does not perform. A freshly installed plane — which is
    what CI has every run — raised `ObjectNotFoundError: ods_model:silver_ods`
    from inside the pack instead of saying its precondition was missing.
    """
    from cinqflow.core.model.governed import ObjectType
    from cinqflow.ports.metadata_db import ObjectNotFoundError

    metadata = PostgresMetadataDb(plane)
    try:
        metadata.get(ObjectType.ODS_MODEL, "silver_ods")
    except ObjectNotFoundError:
        pytest.skip(
            "no ODS model deployed on this plane — this pack reads live state that "
            "publishing a domain creates, which `cinqflow install` does not. Publish "
            "MEMBER_DOMAIN_V1 against this plane, or give this suite a fixture that does."
        )
    return ReconciliationPacks(ods=PostgresOdsLoad(plane), metadata=metadata)


def test_a_real_missing_member_opens_a_real_variance(packs: ReconciliationPacks) -> None:
    packs.ods.upsert_current_row("Members", "OurId", _member_row(700001))
    packs.ods.upsert_current_row("Members", "OurId", _member_row(700002))
    # July's retained roster carried a THIRD member the live table no
    # longer holds — a real, uncorrelated OurId nothing else in this test
    # run could have loaded.
    previous_ids = [700001, 700002, 799999998]

    variance = packs.run_member_universe_pack(
        entity="Members",
        id_column="OurId",
        previous_ids=previous_ids,
        tolerance=Decimal(0),
        batch_id=BATCH_ID,
        feed_id="fidelis-downstate-roster",
        opened_by=OPENED_BY,
        now=NOW,
    )

    assert variance is not None
    assert variance.kind is VarianceKind.MEMBER
    assert "799999998" in variance.explanation
    persisted = packs.metadata.get_variance(variance.variance_id)
    assert persisted.outcome is VarianceOutcome.OPEN
    assert persisted.batch_id == BATCH_ID
