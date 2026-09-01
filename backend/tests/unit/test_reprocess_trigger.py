"""W1-34 · CF-V1-E6-04 (F5, RE-SCOPED) — the batch that could not have known.

    "after a mapping-coverage proposal is approved and published, automatically
     offer/trigger reprocessing of the batch(es) that arrived with the
     now-newly-mapped column — using the EXISTING recovery toolkit, not new
     replay logic."

THE CORRECTED PREMISE. "Parked" (`core.landing`) means a file matching no feed
at all — unrelated to mapping. `DriftKind.UNMAPPED_COLUMN.blocks_batch` is
FALSE, unconditionally, so there is no parked batch here to rescue: there is a
batch that already reached COMPLETED, whose own `control.schema_drift` rows
already say — per column, per batch, since W1-32 — that a column arrived
ungoverned. This suite proves `workers.drift.
propose_reprocess_for_newly_mapped_columns` finds EXACTLY those batches from
that ledger, never a guess and never "the most recent batch for this feed",
and that what it writes is a REFUSED candidate — never an executed reprocess.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer
from cinqflow.core.operations.actions import ActionPhase, ActionRecord, Environment, OpsAction
from cinqflow.ports.control_tables import BatchControl, SchemaDrift, StageStatus
from cinqflow.ports.metadata_db import ActionRecordRow
from cinqflow.workers.drift import propose_reprocess_for_newly_mapped_columns

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"

#: The newly PUBLISHED mapping — the trigger for this whole slab. Covers
#: `SUBSCR_REL_CD`, which no earlier mapping this feed ever published did.
MAPPING = FeedMapping(
    feed_id=FEED,
    version=2,
    lines=(
        MappingLine(
            target_entity="members", target_field="source_member_id", source_columns=("MemberID",)
        ),
        MappingLine(
            target_entity="members",
            target_field="relationship_code",
            source_columns=("SUBSCR_REL_CD",),
        ),
    ),
)


def _batch(
    control: MemStoreControlTables,
    batch_id: str,
    *,
    state: BatchState,
    started: datetime = NOW - timedelta(hours=2),
) -> None:
    control.open_batch(
        BatchControl(
            batch_id=batch_id,
            feed_id=FEED,
            feed_version=1,
            business_date="2026-08-30",
            state=BatchState.RECEIVED,
            started_ts=started,
        )
    )
    control.update_batch_state(batch_id, state)
    control.record_stage(
        StageStatus(
            batch_id=batch_id,
            stage=Layer.BRONZE,
            state=BatchState.COMPLETED,
            started_ts=started,
            completed_ts=started,
            records_in=500,
            records_out=500,
        )
    )


def _drift(control: MemStoreControlTables, batch_id: str, column: str) -> None:
    control.record_schema_drift(
        SchemaDrift(
            batch_id=batch_id,
            feed_id=FEED,
            classification="unmapped_column",
            column_name=column,
            detail=f"{column!r} arrived, is not under contract, and no line of the "
            "published mapping reads it",
            blocked_batch=False,
            detected_ts=NOW - timedelta(hours=2),
        )
    )


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


@pytest.fixture
def metadata() -> MemMetadataDb:
    return MemMetadataDb()


# ── the correct batch, found from the ledger, never guessed ─────────────────


def test_a_completed_batch_whose_finding_is_now_covered_gets_a_refused_candidate(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    _batch(control, "B-OLD", state=BatchState.COMPLETED)
    _drift(control, "B-OLD", "SUBSCR_REL_CD")

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    (row,) = created
    assert row.record.target == "B-OLD"
    assert row.record.action is OpsAction.REPROCESS_BATCH
    assert row.record.actor.subject == "drift-detection"
    assert row.record.actor.actor_type is ActorType.SYSTEM
    assert "SUBSCR_REL_CD" in row.record.reason
    assert "500" in row.record.reason  # the scope Bronze already landed

    # NEVER AUTO-RUN: "agents propose; humans dispose" refuses the SYSTEM
    # actor before anything downstream could execute — the same gate a human
    # actor answers to, not a bespoke check written for this trigger.
    assert row.record.phase is ActionPhase.REFUSED
    assert "Agents propose; humans dispose" in row.record.outcome

    # And genuinely never run: no new batch exists, and the original batch's
    # own state is untouched.
    assert [b.batch_id for b in control.list_batches(FEED)] == ["B-OLD"]
    assert control.get_batch("B-OLD").state is BatchState.COMPLETED

    # Findable exactly where a human reads it — the SAME ops-action ledger.
    (stored,) = metadata.list_action_records(batch_id="B-OLD")
    assert stored.record_id == row.record_id
    assert stored.record.phase is ActionPhase.REFUSED


def test_a_batch_whose_own_finding_the_new_mapping_still_does_not_cover_is_skipped(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    """The candidate is scoped to the FINDING'S OWN column, not "any drift on
    any batch of this feed" — a distractor with an unrelated ungoverned
    column earns nothing."""
    _batch(control, "B-UNRELATED", state=BatchState.COMPLETED)
    _drift(control, "B-UNRELATED", "PLAN_CODE")

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    assert created == ()
    assert metadata.list_action_records(feed_id=FEED) == ()


def test_a_batch_still_in_flight_is_not_offered_even_with_a_matching_finding(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    """REPROCESS_BATCH's own allowed states are COMPLETED or FAILED
    (`core.operations.actions.ALLOWED_STATES`) — reused here, not
    re-decided, so a batch still running is never offered a candidate."""
    _batch(control, "B-RUNNING", state=BatchState.IN_PROGRESS)
    _drift(control, "B-RUNNING", "SUBSCR_REL_CD")

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    assert created == ()


def test_two_past_batches_with_the_same_ungoverned_column_each_get_their_own_candidate(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    """ "batch(es)", plural — a feed that delivered the ungoverned column more
    than once before the mapping was fixed owes a candidate for EACH one,
    never just the newest."""
    _batch(control, "B-FIRST", state=BatchState.COMPLETED, started=NOW - timedelta(days=2))
    _drift(control, "B-FIRST", "SUBSCR_REL_CD")
    _batch(control, "B-SECOND", state=BatchState.COMPLETED, started=NOW - timedelta(days=1))
    _drift(control, "B-SECOND", "SUBSCR_REL_CD")

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    assert {row.record.target for row in created} == {"B-FIRST", "B-SECOND"}
    assert all(row.record.phase is ActionPhase.REFUSED for row in created)


def test_a_batch_already_carrying_a_reprocess_record_is_not_offered_a_second_one(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    """IDEMPOTENT PER BATCH — a republish, or a mapping publish that runs
    twice, must not grow a duplicate candidate beside one that already
    exists, whatever its phase."""
    _batch(control, "B-ALREADY", state=BatchState.COMPLETED)
    _drift(control, "B-ALREADY", "SUBSCR_REL_CD")
    metadata.record_action_event(
        ActionRecordRow(
            record_id="existing-1",
            feed_id=FEED,
            record=ActionRecord(
                action=OpsAction.REPROCESS_BATCH,
                target="B-ALREADY",
                actor=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
                requested_ts=NOW - timedelta(hours=1),
                reason="a steward already asked for this",
                phase=ActionPhase.VERIFIED,
                outcome="reprocessed clean",
            ),
        )
    )

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    assert created == ()


def test_a_mapping_with_no_coverage_at_all_proposes_nothing(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    empty_mapping = FeedMapping(feed_id=FEED, version=1, lines=())
    _batch(control, "B-OLD", state=BatchState.COMPLETED)
    _drift(control, "B-OLD", "SUBSCR_REL_CD")

    created = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=empty_mapping,
        environment=Environment.DEVELOPMENT,
        now=NOW,
    )

    assert created == ()


def test_the_refusal_reason_is_not_a_human_not_a_wrong_state_masquerade(
    control: MemStoreControlTables, metadata: MemMetadataDb
) -> None:
    """The candidate is refused for being a SYSTEM actor, not because this
    module invented a second reason — `RefusalReason.NOT_A_HUMAN` is the
    SAME enum member `test_an_agent_cannot_act_on_the_surface` already
    proves for a human-submitted agent action."""
    _batch(control, "B-OLD", state=BatchState.COMPLETED)
    _drift(control, "B-OLD", "SUBSCR_REL_CD")

    (row,) = propose_reprocess_for_newly_mapped_columns(
        metadata,
        control,
        feed_id=FEED,
        mapping=MAPPING,
        environment=Environment.PRODUCTION,
        now=NOW,
    )

    # Even in PRODUCTION, where an approval identifier would also be missing,
    # NOT_A_HUMAN fires FIRST — the same ordering `authorize` documents for
    # every caller, proved here rather than assumed.
    assert row.record.reason  # a reason was supplied regardless
    assert row.record.outcome == (
        "drift-detection is a system actor. Agents propose; humans dispose — an "
        "operations action is a human act."
    )
