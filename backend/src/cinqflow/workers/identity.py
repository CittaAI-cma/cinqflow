"""CF-V3-E9-01 — the identity worker: I/O, retries, and the control rows.

    "Store full request and response payloads with hashes ... Retry transient
     failures with backoff ... Never let an unresolved identity silently
     disappear — every record has a disposition."
    "Given Verato is unreachable for an extended period, when the stage runs,
     then the batch holds at identity with a clear status, retries follow the
     configured schedule, Operations sees ONE incident (not thousands of
     errors), and nothing proceeds to ODS unresolved."
    "Given the same input arrives twice, when the process runs again, then it
     is safely skipped — data is never duplicated."
    — CF-V3-E9-01

WHY THIS IS A WORKER AND NOT A COMPILED PLAN STEP. `core.compiler.plan.
StepKind.RESOLVE_IDENTITY` exists in the vocabulary but `compile_steps` has
never emitted it, and no renderer runs it — Wave 0-2 plans terminate at
Silver Raw. This worker deliberately does not change that. It is a plain,
directly-callable class in the `IncidentWorker`/`SlaWorker` idiom: reachable
from a pipeline's own completion hook, a CLI command, or a test, with its own
dispatch left to whatever calls it. Wiring `RESOLVE_IDENTITY` into the
compiled plan is Silver-ODS integration work (CF-V3-E10-*) and touches a
question this module does not need an answer to: whether `StepKind`'s
declared order — RESOLVE_IDENTITY before LOAD, LOAD writing Silver Raw — is
itself correct for a story where G3 (silver_raw -> identity) must precede G4
(identity -> silver_ods). This worker takes already-minimized Silver Raw rows
from its caller and has no opinion about how they got there.

PAGES, NOT RECORDS, ARE THE RETRY UNIT. `IdentityPort.submit()` takes a whole
list and returns one entry per record — there is no per-record retry in the
port's contract, only per-CALL success or `IdentityError`. Chunking the batch
into pages and retrying a page independently is what turns "Verato is briefly
flaky" into a handful of slow pages rather than a failed batch, without
requiring a richer port. A page that exhausts every retry stops the run: the
batch holds, one incident opens (via the SAME `IncidentWorker` every other
failure uses — this reuses it rather than reimplementing "one incident, not
one per error"), and every page already committed stays committed. The next
run of `resolve_batch` on the same batch resubmits only what
`ControlTablesPort.get_crosswalk` does not already have an answer for — which
is the story's own idempotency guarantee, not a check this worker performs
before deciding whether to call it.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from cinqflow.core.delivery import DEFAULT_RETRY_POLICY, RetryPolicy
from cinqflow.core.identity import (
    REQUIRED_ATTRIBUTES,
    CrosswalkEntry,
    IdentityDisposition,
    MatchOutcome,
    dispose,
    prepare,
)
from cinqflow.core.identity.exceptions import (
    ExceptionEventAction,
    IdentityException,
    IdentityExceptionEvent,
    escalate_if_breached,
    exception_key,
)
from cinqflow.core.identity.telemetry import (
    CoverageSnapshot,
    ParityCheckSummary,
    coverage_by_source,
)
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, Layer
from cinqflow.ports.control_tables import (
    ControlTablesPort,
    ErrorRecord,
    IdentityRequestLogEntry,
    IdentityResponseLogEntry,
    StageStatus,
)
from cinqflow.ports.identity import IdentityError, IdentityPort
from cinqflow.ports.legacy_readonly import LegacyReadOnlyPort, ParityDifference
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.workers.incidents import IncidentWorker

#: The subject every occurrence and every held-batch error is written under.
#: The platform ran the stage; blaming an engineer for a payer's outage would
#: misattribute an operational fact as somebody's mistake.
PLATFORM_SUBJECT = "platform@cinqflow"


class IdentityWorkerError(RuntimeError):
    """The identity worker refused to proceed."""


def _payload_hash(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _chunks(records: Sequence[Mapping[str, str]], size: int) -> list[Sequence[Mapping[str, str]]]:
    return [records[i : i + size] for i in range(0, len(records), size)]


class IdentityWorker:
    def __init__(
        self,
        *,
        identity: IdentityPort,
        control: ControlTablesPort,
        metadata: MetadataDbPort,
        incidents: IncidentWorker,
        retry: RetryPolicy = DEFAULT_RETRY_POLICY,
        page_size: int = 500,
    ) -> None:
        if page_size < 1:
            raise IdentityWorkerError("a page of fewer than one record submits nothing")
        self._identity = identity
        self._control = control
        self._metadata = metadata
        self._incidents = incidents
        self._retry = retry
        self._page_size = page_size

    def resolve_batch(
        self,
        batch_id: str,
        records: Sequence[Mapping[str, str]],
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: datetime | None = None,
    ) -> IdentityDisposition | None:
        """Submit every record this batch has not already resolved.

        Returns the balanced `IdentityDisposition` once every record has an
        outcome. Returns `None` when the batch HOLDS — Verato exhausted its
        retries partway through — which is a distinct, ordinary answer, not
        an error: `dispose()` on a partially-processed batch would raise
        `UnbalancedIdentityError` for a batch that is not unbalanced, only
        unfinished, and conflating "still waiting" with "the accounting is
        wrong" is exactly the failure mode G4 exists to catch.
        """
        stamp = now or datetime.now(UTC)
        already = {
            (e.source_system, e.source_member_id): e for e in self._control.list_crosswalk(batch_id)
        }
        pending = [
            record
            for record in records
            if (record["source_system"], record["source_member_id"]) not in already
        ]
        entries: list[CrosswalkEntry] = list(already.values())

        held = False
        for page in _chunks(pending, self._page_size):
            prepared = prepare(page)
            page_entries = self._submit_page(prepared, batch_id=batch_id, sleep=sleep, stamp=stamp)
            if page_entries is None:
                self._hold(batch_id, stamp=stamp)
                held = True
                break
            for record, entry in zip(page, page_entries, strict=True):
                self._persist(record, entry, batch_id=batch_id, stamp=stamp)
                entries.append(entry)

        self._control.record_stage(
            StageStatus(
                batch_id=batch_id,
                stage=Layer.IDENTITY,
                state=BatchState.BLOCKED if held else BatchState.COMPLETED,
                started_ts=stamp,
                completed_ts=None if held else stamp,
                records_in=len(records),
                records_out=sum(1 for e in entries if e.outcome is MatchOutcome.RESOLVED),
                quarantined=0,
                attributed_drops=sum(1 for e in entries if e.outcome is not MatchOutcome.RESOLVED),
            )
        )
        if held:
            return None
        disposition = IdentityDisposition(
            batch_id=batch_id, submitted=len(records), entries=tuple(entries)
        )
        return dispose(disposition)

    # ── the call, retried per page ────────────────────────────────────────────

    def _submit_page(
        self,
        prepared: Sequence[dict[str, str]],
        *,
        batch_id: str,
        sleep: Callable[[float], None],
        stamp: datetime,
    ) -> Sequence[CrosswalkEntry] | None:
        for attempt in range(1, self._retry.max_attempts + 1):
            delay = self._retry.delay_seconds(attempt)
            if delay:
                sleep(delay)
            try:
                return self._identity.submit(prepared, batch_id=batch_id)
            except IdentityError:
                continue
        return None

    # ── one held batch, one incident — never one per record ──────────────────

    def _hold(self, batch_id: str, *, stamp: datetime) -> None:
        self._control.update_batch_state(batch_id, BatchState.BLOCKED)
        self._control.record_error(
            ErrorRecord(
                error_id_hash=hashlib.sha256(
                    f"identity-unreachable:{batch_id}".encode()
                ).hexdigest(),
                batch_id=batch_id,
                stage=Layer.IDENTITY,
                category=ErrorCategory.INTEGRATION,
                message="Verato was unreachable after every retry; the batch holds at identity.",
                occurred_ts=stamp,
            )
        )
        # The SAME worker every other failure opens an incident through —
        # idempotent on the batch, so a worker retried after the outage
        # clears never opens a second incident for the same hold.
        self._incidents.on_batch_failed(batch_id, now=stamp)

    # ── one record, resolved or not ───────────────────────────────────────────

    def _persist(
        self,
        record: Mapping[str, str],
        entry: CrosswalkEntry,
        *,
        batch_id: str,
        stamp: datetime,
    ) -> None:
        source_system = record["source_system"]
        source_member_id = record["source_member_id"]
        request_id = str(uuid.uuid4())
        # The prepared, minimized payload IS the request: `prepare()` already
        # subtracted everything Verato's own spec does not name, so logging
        # the record as-is here logs exactly what a real adapter would send.
        request_payload = {k: v for k, v in record.items() if k in REQUIRED_ATTRIBUTES}
        self._control.record_identity_request(
            IdentityRequestLogEntry(
                request_id=request_id,
                batch_id=batch_id,
                source_system=source_system,
                source_member_id=source_member_id,
                payload=request_payload,
                payload_hash=_payload_hash(request_payload),
                sent_ts=stamp,
            )
        )
        # `IdentityPort.submit()` returns the abstracted CrosswalkEntry, not
        # wire bytes — this is the richest "response" the pin's own contract
        # exposes today. A real Verato adapter logging actual HTTP bodies is
        # a change to what THIS call receives, not to this call site.
        confidence = entry.match_confidence_score
        response_payload = {
            "outcome": entry.outcome.value,
            "verato_person_id": entry.verato_person_id,
            "match_confidence_score": str(confidence) if confidence is not None else None,
        }
        self._control.record_identity_response(
            IdentityResponseLogEntry(
                response_id=str(uuid.uuid4()),
                request_id=request_id,
                batch_id=batch_id,
                payload=response_payload,
                payload_hash=_payload_hash(response_payload),
                outcome=entry.outcome,
                received_ts=stamp,
            )
        )
        self._control.record_crosswalk(entry)

        if entry.outcome is not MatchOutcome.RESOLVED:
            self._metadata.record_identity_exception_event(
                IdentityExceptionEvent(
                    event_id=str(uuid.uuid4()),
                    exception_key=exception_key(source_system, source_member_id),
                    action=ExceptionEventAction.OCCURRENCE,
                    source_system=source_system,
                    source_member_id=source_member_id,
                    occurred_ts=stamp,
                    batch_id=batch_id,
                    outcome=entry.outcome,
                    actor_subject=PLATFORM_SUBJECT,
                )
            )


#: CF-V3-E9-02's own SLA. Every existing test for `escalate_if_breached` and
#: `health_by_source` already assumes five days; a per-source configurable
#: duration is a real future need (a payer whose data quality never
#: improves deserves a shorter fuse) but `feed_sla_config` has no seat for a
#: queue that spans every feed one source touches, and inventing a second
#: config table for one number nobody has asked to tune yet is not this
#: phase's job.
IDENTITY_EXCEPTION_SLA = timedelta(days=5)

#: The daily evaluation reads the whole queue, not a page of it — an
#: exception that ages out of a bounded page would silently stop being
#: eligible for escalation, which is exactly the "quietly disappears"
#: failure mode this queue exists to prevent.
_EVALUATION_SPAN = 100_000


def evaluate_identity_exception_slas(
    metadata: MetadataDbPort,
    *,
    sla: timedelta = IDENTITY_EXCEPTION_SLA,
    now: datetime | None = None,
) -> tuple[IdentityException, ...]:
    """ "Given an exception ages past its SLA without action, when the daily
    evaluation runs, then it escalates to the steward's manager per the
    escalation chain, visibly on the item." — CF-V3-E9-02

    Decides with the SAME pure `escalate_if_breached` a test exercises with
    no ledger at all, then persists only what actually changed: a queue with
    nothing overdue writes nothing, rather than re-recording every open
    exception's unchanged state once a day. Returns what was escalated this
    run — the notification the story implies ("visibly on the item") is a
    read of the ledger this call already wrote to, not a second concern
    this function needs to own.
    """
    stamp = now or datetime.now(UTC)
    before = metadata.list_identity_exceptions(limit=_EVALUATION_SPAN)
    after = escalate_if_breached(before, sla=sla, now=stamp)
    changed = tuple(
        moved for was, moved in zip(before, after, strict=True) if moved.state is not was.state
    )
    for exc in changed:
        metadata.record_identity_exception_event(
            IdentityExceptionEvent(
                event_id=str(uuid.uuid4()),
                exception_key=exc.key,
                action=ExceptionEventAction.ESCALATED,
                source_system=exc.source_system,
                source_member_id=exc.source_member_id,
                occurred_ts=stamp,
                actor_subject=PLATFORM_SUBJECT,
            )
        )
    return changed


def gather_crosswalk_entries(
    control: ControlTablesPort, batch_ids: Sequence[str]
) -> tuple[CrosswalkEntry, ...]:
    """Every crosswalk entry across a set of batches — "today's batches",
    handed in by whatever already knows which ones ran today (a feed
    registry walk, a CLI's own arguments). Deliberately not this module's
    concern: `resolve_batch` does not discover Silver Raw rows either, and
    for the same reason — a worker that reaches across the registry to find
    its own input is a second, harder-to-test copy of whatever already
    enumerates feeds."""
    return tuple(entry for batch_id in batch_ids for entry in control.list_crosswalk(batch_id))


def record_daily_coverage(
    control: ControlTablesPort, entries: Sequence[CrosswalkEntry], *, business_date: str
) -> tuple[CoverageSnapshot, ...]:
    """CF-V3-E9-04 — one snapshot per source touched today, persisted.

    Upserting on `(source_system, business_date)` is what makes a re-run
    after a correction — or a chaos test replaying the same day — safe: the
    second call corrects the row rather than doubling it, the same
    idempotency guarantee `resolve_batch` gives the crosswalk itself.
    """
    snapshots = coverage_by_source(entries, business_date=business_date)
    for snapshot in snapshots:
        control.record_coverage_snapshot(snapshot)
    return snapshots


def check_daily_parity(
    control: ControlTablesPort,
    legacy: LegacyReadOnlyPort,
    entries: Sequence[CrosswalkEntry],
    *,
    source_system: str,
    business_date: str,
    query_name: str = "ourid_link_id_crosswalk",
) -> tuple[ParityCheckSummary, tuple[ParityDifference, ...]]:
    """ "Automate the parity check that was historically done by hand once
    ('validate LinkID matches between lake and legacy') into a standing
    daily scorecard." — CF-V3-E9-04

    Read-only on the legacy side, structurally: `LegacyReadOnlyPort` has no
    write verb at all (see its own module note), so there is no call this
    function could make that would touch the system being decommissioned.
    Only members this source has already resolved a LinkId for are checked
    — a record Verato has not yet answered for has nothing to compare, and
    entering it as a mismatch would inflate the drop the story's own alert
    is meant to catch.
    """
    resolved = tuple(e for e in entries if e.source_system == source_system and e.verato_person_id)
    ours = [
        {"key": e.internal_member_id, "link_id": e.verato_person_id}
        for e in resolved
        if e.internal_member_id
    ]
    differences = tuple(legacy.compare(query_name, ours))
    summary = ParityCheckSummary(
        source_system=source_system,
        business_date=business_date,
        checked=len(ours),
        matched=len(ours) - len(differences),
        mismatched=len(differences),
    )
    control.record_parity_check(summary)
    return summary, differences
