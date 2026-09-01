"""CF-V3-E9-01 — the identity worker: retries, idempotency, the one incident.

"Given a roster batch reaches the identity stage ... 9,940 ... resolve
 ... 42 fail transiently and succeed on retry, 18 remain unresolved."
"Given Verato is unreachable for an extended period ... the batch holds
 at identity with a clear status ... Operations sees one incident (not
 thousands of errors), and nothing proceeds to ODS unresolved."
"Given the same input arrives twice ... it is safely skipped."
— CF-V3-E9-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.identity import ScenarioIdentity
from cinqflow.adapters.mock.legacy_readonly import SeededLegacyDb
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.delivery import RetryPolicy
from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.identity.exceptions import (
    ExceptionEventAction,
    ExceptionState,
    IdentityExceptionEvent,
    exception_key,
)
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.ports.control_tables import BatchControl
from cinqflow.ports.identity import IdentityError
from cinqflow.ports.metadata_db import ObjectNotFoundError
from cinqflow.workers.identity import (
    IdentityWorker,
    IdentityWorkerError,
    check_daily_parity,
    evaluate_identity_exception_slas,
    gather_crosswalk_entries,
    record_daily_coverage,
)
from cinqflow.workers.incidents import IncidentWorker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, 3, 14, tzinfo=UTC)
BATCH = "B-8842"
FEED = "fidelis-downstate-roster"


def _records(n: int) -> list[dict[str, str]]:
    return [
        {
            "source_system": "fidelis",
            "source_member_id": f"MBR{i:06d}",
            "first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": "1980-01-01",
            "gender": "F",
        }
        for i in range(1, n + 1)
    ]


class CountingIdentity:
    """Wraps a real `IdentityPort` to count calls, for the idempotency test."""

    def __init__(self, inner: ScenarioIdentity) -> None:
        self._inner = inner
        self.calls = 0

    def submit(
        self, records: Sequence[dict[str, str]], *, batch_id: str
    ) -> Sequence[CrosswalkEntry]:
        self.calls += 1
        return self._inner.submit(records, batch_id=batch_id)

    def crosswalk(self, source_system: str, source_member_id: str) -> CrosswalkEntry | None:
        return self._inner.crosswalk(source_system, source_member_id)


class FlakyIdentity:
    """Raises `IdentityError` for the first `fail_times` calls, then succeeds."""

    def __init__(self, *, fail_times: int, outcomes: dict[str, MatchOutcome] | None = None) -> None:
        self._remaining_failures = fail_times
        self._inner = ScenarioIdentity(outcomes)
        self.calls = 0

    def submit(
        self, records: Sequence[dict[str, str]], *, batch_id: str
    ) -> Sequence[CrosswalkEntry]:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise IdentityError("Verato is briefly unreachable")
        return self._inner.submit(records, batch_id=batch_id)

    def crosswalk(self, source_system: str, source_member_id: str) -> CrosswalkEntry | None:
        return self._inner.crosswalk(source_system, source_member_id)


def _opened(control: MemStoreControlTables, batch_id: str = BATCH) -> None:
    control.open_batch(
        BatchControl(
            batch_id=batch_id,
            feed_id=FEED,
            feed_version=1,
            business_date="2026-08-31",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )


DEFAULT_RETRY = RetryPolicy(max_attempts=2, base_seconds=0.0)


def _worker(
    identity,
    *,
    control=None,
    metadata=None,
    retry: RetryPolicy = DEFAULT_RETRY,
) -> tuple[IdentityWorker, MemStoreControlTables, MemMetadataDb]:
    control = control or MemStoreControlTables()
    metadata = metadata or MemMetadataDb()
    incidents = IncidentWorker(control=control, metadata=metadata)
    worker = IdentityWorker(
        identity=identity, control=control, metadata=metadata, incidents=incidents, retry=retry
    )
    return worker, control, metadata


def test_a_fully_resolved_batch_balances_and_writes_the_stage() -> None:
    identity = ScenarioIdentity({f"MBR{i:06d}": MatchOutcome.RESOLVED for i in range(1, 4)})
    worker, control, _metadata = _worker(identity)
    _opened(control)

    disposition = worker.resolve_batch(BATCH, _records(3), now=NOW, sleep=lambda _: None)

    assert disposition is not None
    assert disposition.balances
    assert disposition.resolved == 3
    assert disposition.unresolved == 0
    (stage,) = control.get_stages(BATCH)
    assert stage.stage is Layer.IDENTITY
    assert stage.state is BatchState.COMPLETED
    assert stage.records_out == 3
    assert stage.attributed_drops == 0
    assert len(control.list_crosswalk(BATCH)) == 3


def test_unresolved_records_never_disappear_they_open_an_exception() -> None:
    identity = ScenarioIdentity({"MBR000001": MatchOutcome.RESOLVED})  # MBR2 defaults UNRESOLVED
    worker, control, metadata = _worker(identity)
    _opened(control)

    disposition = worker.resolve_batch(BATCH, _records(2), now=NOW, sleep=lambda _: None)

    assert disposition is not None
    assert disposition.unresolved == 1
    exc = metadata.get_identity_exception(exception_key("fidelis", "MBR000002"))
    assert exc.state is ExceptionState.OPEN
    assert exc.occurrence_count == 1
    assert exc.occurrences[0].batch_id == BATCH


def test_the_same_batch_run_twice_resubmits_nothing() -> None:
    """ "Given the same input arrives twice ... it is safely skipped." """
    inner = ScenarioIdentity({f"MBR{i:06d}": MatchOutcome.RESOLVED for i in range(1, 4)})
    identity = CountingIdentity(inner)
    worker, control, _metadata = _worker(identity)
    _opened(control)
    records = _records(3)

    first = worker.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)
    second = worker.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)

    assert identity.calls == 1
    assert first == second
    assert len(control.list_crosswalk(BATCH)) == 3


def test_a_transient_failure_recovers_within_the_retry_budget() -> None:
    identity = FlakyIdentity(
        fail_times=1, outcomes={f"MBR{i:06d}": MatchOutcome.RESOLVED for i in range(1, 4)}
    )
    worker, control, metadata = _worker(
        identity, retry=RetryPolicy(max_attempts=3, base_seconds=0.0)
    )
    _opened(control)

    disposition = worker.resolve_batch(BATCH, _records(3), now=NOW, sleep=lambda _: None)

    assert disposition is not None
    assert disposition.balances
    assert control.get_batch(BATCH).state is not BatchState.BLOCKED
    assert metadata.list_incident_events(batch_id=BATCH) == ()


def test_verato_unreachable_for_every_retry_holds_the_batch_with_one_incident() -> None:
    identity = FlakyIdentity(fail_times=99)
    worker, control, metadata = _worker(
        identity, retry=RetryPolicy(max_attempts=2, base_seconds=0.0)
    )
    _opened(control)

    result = worker.resolve_batch(BATCH, _records(3), now=NOW, sleep=lambda _: None)

    assert result is None
    assert control.get_batch(BATCH).state is BatchState.BLOCKED
    (stage,) = control.get_stages(BATCH)
    assert stage.state is BatchState.BLOCKED
    assert stage.completed_ts is None
    assert len(metadata.list_incident_events(batch_id=BATCH)) == 1
    assert control.list_crosswalk(BATCH) == ()


def test_a_second_run_while_still_down_opens_no_second_incident() -> None:
    """Idempotent the same way `IncidentWorker` already is on its own."""
    identity = FlakyIdentity(fail_times=99)
    worker, control, metadata = _worker(
        identity, retry=RetryPolicy(max_attempts=1, base_seconds=0.0)
    )
    _opened(control)
    records = _records(2)

    worker.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)
    worker.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)

    assert len(metadata.list_incident_events(batch_id=BATCH)) == 1


def test_recovering_after_a_hold_resubmits_only_what_never_resolved() -> None:
    """A batch that held partway resumes exactly where it stopped — the
    idempotency check applies even mid-outage, not only on a fresh re-run."""
    always_down = FlakyIdentity(fail_times=99)
    worker, control, metadata = _worker(
        always_down, retry=RetryPolicy(max_attempts=1, base_seconds=0.0)
    )
    _opened(control)
    records = _records(2)
    held = worker.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)
    assert held is None
    assert control.list_crosswalk(BATCH) == ()

    recovered = ScenarioIdentity(
        {"MBR000001": MatchOutcome.RESOLVED, "MBR000002": MatchOutcome.RESOLVED}
    )
    counting = CountingIdentity(recovered)
    worker2, _, _ = _worker(counting, control=control, metadata=metadata)
    disposition = worker2.resolve_batch(BATCH, records, now=NOW, sleep=lambda _: None)

    assert disposition is not None
    assert disposition.balances
    assert counting.calls == 1


def test_page_size_must_be_positive() -> None:
    control = MemStoreControlTables()
    metadata = MemMetadataDb()
    with pytest.raises(IdentityWorkerError):
        IdentityWorker(
            identity=ScenarioIdentity(),
            control=control,
            metadata=metadata,
            incidents=IncidentWorker(control=control, metadata=metadata),
            page_size=0,
        )


def test_an_untouched_member_has_no_exception_at_all() -> None:
    _, _, metadata = _worker(ScenarioIdentity())
    with pytest.raises(ObjectNotFoundError):
        metadata.get_identity_exception(exception_key("fidelis", "nobody-failed-for-this-one"))


# ── evaluate_identity_exception_slas · CF-V3-E9-02's daily evaluation ────────


def test_an_exception_past_its_sla_escalates() -> None:
    identity = ScenarioIdentity()  # every record defaults UNRESOLVED
    worker, control, metadata = _worker(identity)
    _opened(control)
    worker.resolve_batch(BATCH, _records(1), now=NOW, sleep=lambda _: None)

    escalated = evaluate_identity_exception_slas(
        metadata, sla=timedelta(days=5), now=NOW + timedelta(days=10)
    )

    assert len(escalated) == 1
    exc = metadata.get_identity_exception(exception_key("fidelis", "MBR000001"))
    assert exc.state is ExceptionState.ESCALATED


def test_an_exception_still_inside_its_sla_is_untouched() -> None:
    identity = ScenarioIdentity()
    worker, control, metadata = _worker(identity)
    _opened(control)
    worker.resolve_batch(BATCH, _records(1), now=NOW, sleep=lambda _: None)

    escalated = evaluate_identity_exception_slas(
        metadata, sla=timedelta(days=5), now=NOW + timedelta(hours=1)
    )

    assert escalated == ()
    exc = metadata.get_identity_exception(exception_key("fidelis", "MBR000001"))
    assert exc.state is ExceptionState.OPEN


def test_a_second_evaluation_run_writes_nothing_for_an_already_escalated_item() -> None:
    identity = ScenarioIdentity()
    worker, control, metadata = _worker(identity)
    _opened(control)
    worker.resolve_batch(BATCH, _records(1), now=NOW, sleep=lambda _: None)

    first = evaluate_identity_exception_slas(
        metadata, sla=timedelta(days=5), now=NOW + timedelta(days=10)
    )
    second = evaluate_identity_exception_slas(
        metadata, sla=timedelta(days=5), now=NOW + timedelta(days=20)
    )

    assert len(first) == 1
    assert second == ()


def test_a_resolved_exception_never_escalates() -> None:
    identity = ScenarioIdentity()
    worker, control, metadata = _worker(identity)
    _opened(control)
    worker.resolve_batch(BATCH, _records(1), now=NOW, sleep=lambda _: None)
    key = exception_key("fidelis", "MBR000001")
    metadata.record_identity_exception_event(
        IdentityExceptionEvent(
            event_id="EVT-resolve",
            exception_key=key,
            action=ExceptionEventAction.RESOLVED,
            source_system="fidelis",
            source_member_id="MBR000001",
            occurred_ts=NOW + timedelta(hours=1),
            actor_subject="steward@cinqcare.test",
        )
    )

    escalated = evaluate_identity_exception_slas(
        metadata, sla=timedelta(days=5), now=NOW + timedelta(days=30)
    )

    assert escalated == ()
    assert metadata.get_identity_exception(key).state is ExceptionState.RESOLVED


# ── coverage and parity telemetry · CF-V3-E9-04 ──────────────────────────────


def test_gather_crosswalk_entries_flattens_across_the_given_batches() -> None:
    control = MemStoreControlTables()
    control.record_crosswalk(
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-1",
            internal_member_id="OUR-1",
            verato_person_id="LINK-1",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        )
    )
    control.record_crosswalk(
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-2",
            internal_member_id="OUR-2",
            verato_person_id="LINK-2",
            batch_id="B-2",
            outcome=MatchOutcome.RESOLVED,
        )
    )
    entries = gather_crosswalk_entries(control, ["B-1", "B-2", "a-batch-with-nothing"])
    assert {e.source_member_id for e in entries} == {"M-1", "M-2"}


def test_record_daily_coverage_persists_one_snapshot_per_source() -> None:
    control = MemStoreControlTables()
    entries = (
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-1",
            internal_member_id="OUR-1",
            verato_person_id="LINK-1",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        ),
        CrosswalkEntry(
            source_system="optum",
            source_member_id="M-2",
            internal_member_id="",
            verato_person_id="LINK-2",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        ),
    )
    snapshots = record_daily_coverage(control, entries, business_date="2026-08-31")
    assert len(snapshots) == 2
    (fidelis_history,) = control.coverage_history("fidelis")
    assert fidelis_history.with_both == 1
    (optum_history,) = control.coverage_history("optum")
    assert optum_history.with_both == 0  # LinkId present, but no legacy OurId


def test_recomputing_the_same_day_corrects_rather_than_duplicates() -> None:
    control = MemStoreControlTables()
    first_pass = (
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-1",
            internal_member_id="OUR-1",
            verato_person_id="LINK-1",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        ),
    )
    # A corrected re-run of the same day finds a second member too.
    second_pass = (
        *first_pass,
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-2",
            internal_member_id="OUR-2",
            verato_person_id="LINK-2",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        ),
    )
    record_daily_coverage(control, first_pass, business_date="2026-08-31")
    record_daily_coverage(control, second_pass, business_date="2026-08-31")
    (only,) = control.coverage_history("fidelis")
    assert only.total == 2


def _crosswalk(
    *, source_member_id: str, our_id: str, link_id: str, source_system: str = "fidelis"
) -> CrosswalkEntry:
    return CrosswalkEntry(
        source_system=source_system,
        source_member_id=source_member_id,
        internal_member_id=our_id,
        verato_person_id=link_id,
        batch_id="B-1",
        outcome=MatchOutcome.RESOLVED,
    )


def test_parity_check_finds_zero_mismatches_when_the_lake_and_legacy_agree() -> None:
    control = MemStoreControlTables()
    legacy = SeededLegacyDb({"ourid_link_id_crosswalk": [{"key": "OUR-1", "link_id": "LINK-1"}]})
    entries = (_crosswalk(source_member_id="M-1", our_id="OUR-1", link_id="LINK-1"),)

    summary, differences = check_daily_parity(
        control, legacy, entries, source_system="fidelis", business_date="2026-08-31"
    )

    assert summary.checked == 1
    assert summary.matched == 1
    assert summary.mismatched == 0
    assert differences == ()
    (persisted,) = control.parity_check_history("fidelis")
    assert persisted.matched == 1


def test_parity_check_flags_a_disagreeing_link_id() -> None:
    control = MemStoreControlTables()
    legacy = SeededLegacyDb(
        {"ourid_link_id_crosswalk": [{"key": "OUR-1", "link_id": "LINK-STALE"}]}
    )
    entries = (_crosswalk(source_member_id="M-1", our_id="OUR-1", link_id="LINK-FRESH"),)

    summary, differences = check_daily_parity(
        control, legacy, entries, source_system="fidelis", business_date="2026-08-31"
    )

    assert summary.mismatched == 1
    assert len(differences) == 1
    assert differences[0].ours == "LINK-FRESH"
    assert differences[0].theirs == "LINK-STALE"


def test_a_new_member_with_no_legacy_our_id_is_never_checked() -> None:
    """A member with no OurId was never in the legacy estate — comparing it
    against Cinq DB would inflate the mismatch count with members legacy was
    never going to know about."""
    control = MemStoreControlTables()
    legacy = SeededLegacyDb({})
    entries = (
        CrosswalkEntry(
            source_system="fidelis",
            source_member_id="M-new",
            internal_member_id="",
            verato_person_id="LINK-NEW",
            batch_id="B-1",
            outcome=MatchOutcome.RESOLVED,
        ),
    )

    summary, differences = check_daily_parity(
        control, legacy, entries, source_system="fidelis", business_date="2026-08-31"
    )

    assert summary.checked == 0
    assert differences == ()


def test_legacy_is_never_written_to() -> None:
    """Structural, not a policy comment: `LegacyReadOnlyPort` has no write
    verb at all, so asserting one is absent is asserting the contract."""
    assert not hasattr(SeededLegacyDb, "write")
    assert not hasattr(SeededLegacyDb, "update")
    assert not hasattr(SeededLegacyDb, "delete")
