"""The ONE contract suite for the remaining platform pins.

    metadata_db · queue · secrets · authn · notification · observability
    catalog · sql_query · orchestration · identity · cache · legacy_readonly
    compute_job · http_edge

Grouped rather than split into fourteen files because each is small, and the
guarantees that matter are mostly REFUSALS — which read better together, as the
platform's safety floor stated in one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from cinqflow.core.compiler.plan import LogicalPlan, compile_steps
from cinqflow.core.identity import MatchOutcome
from cinqflow.core.identity.exceptions import (
    ExceptionEventAction,
    ExceptionState,
    IdentityExceptionEvent,
    exception_key,
)
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer
from cinqflow.core.operations.actions import ActionPhase, OpsAction
from cinqflow.core.operations.actions import verify as ops_verify
from cinqflow.core.operations.fingerprint import IncidentEvent, IncidentState
from cinqflow.core.variance import Variance, VarianceKind, VarianceOutcome, Waiver
from cinqflow.ports.authn import AuthenticationError, AuthnPort, Role
from cinqflow.ports.catalog import CatalogPort
from cinqflow.ports.compute_job import ComputeError, ComputeJobPort
from cinqflow.ports.identity import IdentityPort, UnapprovedMergeError
from cinqflow.ports.legacy_readonly import LegacyReadOnlyPort
from cinqflow.ports.metadata_db import (
    ActionRecordRow,
    ConcurrentVersionError,
    MetadataDbPort,
    ObjectNotFoundError,
)
from cinqflow.ports.notification import Alert, NotificationPort, Severity
from cinqflow.ports.orchestration import OrchestrationPort, Schedule
from cinqflow.ports.queue import QueuePort
from cinqflow.ports.secrets import SecretNotFoundError, SecretsPort
from cinqflow.ports.sql_query import QueryRefusedError, SqlQueryPort

from .conftest import adapters_for

NOW = datetime(2026, 8, 1, 3, 14, tzinfo=UTC)
AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
APPROVER = Actor(subject="steve@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steve")

pytestmark = pytest.mark.contract


# ── metadata_db ──────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("metadata_db"))
def metadata(request: pytest.FixtureRequest, make: Callable[..., Any]) -> MetadataDbPort:
    return make(request.param)


def _feed(version: int = 1, state: LifecycleState = LifecycleState.DRAFT) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id="fidelis-downstate-roster",
        version=version,
        lifecycle_state=state,
        created_by=AUTHOR,
        created_ts=NOW,
        body={"domain": "enrollments", "format": "xlsx"},
    )


def test_saving_stores_a_version_and_returns_what_was_stored(metadata: MetadataDbPort) -> None:
    """Returning the stored object means the caller sees the version it ACTUALLY
    got, rather than the one it assumed."""
    stored = metadata.save(_feed())
    assert stored.version == 1
    assert metadata.get(ObjectType.FEED, "fidelis-downstate-roster").version == 1


def test_history_keeps_every_version_oldest_first(metadata: MetadataDbPort) -> None:
    """This is what makes "the engine always states which feed version a run
    used" answerable after the fact."""
    metadata.save(_feed(1))
    metadata.save(_feed(2))
    assert [o.version for o in metadata.history(ObjectType.FEED, "fidelis-downstate-roster")] == [
        1,
        2,
    ]


def test_get_without_a_version_returns_the_latest(metadata: MetadataDbPort) -> None:
    metadata.save(_feed(1))
    metadata.save(_feed(2))
    assert metadata.get(ObjectType.FEED, "fidelis-downstate-roster").version == 2


def test_writing_the_same_version_twice_is_refused(metadata: MetadataDbPort) -> None:
    """Two authors versioned from the same base. Taking the last write silently
    is how an approved configuration becomes something nobody approved."""
    metadata.save(_feed(1))
    with pytest.raises(ConcurrentVersionError):
        metadata.save(_feed(1))


def test_a_missing_object_is_distinct_from_a_store_failure(metadata: MetadataDbPort) -> None:
    with pytest.raises(ObjectNotFoundError):
        metadata.get(ObjectType.FEED, "never-registered")


def test_audit_is_append_only_with_no_deletion_path_for_anyone(
    metadata: MetadataDbPort,
) -> None:
    """ "audit is append-only; no deletion path exists for anyone, including
    administrators."

    Asserted as the ABSENCE of a verb. A permission check could be
    misconfigured; a missing method cannot be.
    """
    metadata.append_audit(
        AuditEntry(
            object_type=ObjectType.FEED,
            object_id="fidelis-downstate-roster",
            version=1,
            action="created",
            actor=AUTHOR,
            occurred_ts=NOW,
        )
    )
    assert len(metadata.read_audit(object_id="fidelis-downstate-roster")) == 1
    for forbidden in ("delete_audit", "update_audit", "purge", "truncate_audit", "clear"):
        assert not hasattr(metadata, forbidden), f"metadata_db exposes {forbidden}"


# ── ops.action_record · CF-V2-E12-03/E8-04 ───────────────────────────────────
def _requested_retry(record_id: str, *, batch_id: str = "B-1244") -> ActionRecordRow:
    from cinqflow.core.operations.actions import ActionRequest, request_action

    request = ActionRequest(
        action=OpsAction.RETRY,
        target=batch_id,
        actor=AUTHOR,
        reason="Transient cluster error.",
    )
    return ActionRecordRow(
        record_id=record_id,
        feed_id="fidelis-downstate-roster",
        record=request_action(request, now=NOW),
    )


def test_a_requested_action_is_readable_back_and_not_complete(
    metadata: MetadataDbPort,
) -> None:
    """The record used to vanish with the HTTP response — 'I clicked retry and
    nothing happened' was unanswerable. Now it is a row."""
    metadata.record_action_event(_requested_retry("act-1"))
    row = metadata.get_action_record("act-1")
    assert row.record.phase is ActionPhase.REQUESTED
    assert row.record.is_complete is False
    assert row.feed_id == "fidelis-downstate-roster"


def test_verification_is_a_second_row_and_becomes_the_current_phase(
    metadata: MetadataDbPort,
) -> None:
    """'retry requested' is not 'retry succeeded' — and the phase only moves
    when a verification that OBSERVED something is appended."""
    first = _requested_retry("act-2")
    metadata.record_action_event(first)
    verified = ops_verify(
        first.record,
        observed_state=BatchState.COMPLETED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="Batch re-ran to COMPLETED; 9,992 rows loaded.",
        now=NOW + timedelta(minutes=18),
    )
    metadata.record_action_event(ActionRecordRow("act-2", first.feed_id, verified))
    row = metadata.get_action_record("act-2")
    assert row.record.phase is ActionPhase.VERIFIED
    assert row.record.is_complete is True
    assert row.record.observed_state is BatchState.COMPLETED


def test_a_refusal_is_a_row_too(metadata: MetadataDbPort) -> None:
    """ "the system refuses, notifies a human, and RECORDS THE REFUSAL" — an
    action declined at the gate must still be findable six weeks later."""
    from cinqflow.core.operations.actions import (
        ActionRequest,
        Refusal,
        RefusalReason,
        refused_action,
    )

    request = ActionRequest(
        action=OpsAction.RETRY, target="B-1244", actor=AUTHOR, reason="Just in case."
    )
    refusal = Refusal(
        action=OpsAction.RETRY,
        target="B-1244",
        reason=RefusalReason.WRONG_STATE,
        detail="B-1244 is completed — a completed batch is not retried, it is reprocessed.",
    )
    metadata.record_action_event(
        ActionRecordRow("act-3", "fidelis-downstate-roster", refused_action(request, refusal))
    )
    row = metadata.get_action_record("act-3")
    assert row.record.phase is ActionPhase.REFUSED
    assert "not retried" in row.record.outcome


def test_the_action_history_lists_current_phases_newest_first(
    metadata: MetadataDbPort,
) -> None:
    first = _requested_retry("act-4")
    metadata.record_action_event(first)
    metadata.record_action_event(
        ActionRecordRow(
            "act-4",
            first.feed_id,
            ops_verify(
                first.record,
                observed_state=BatchState.FAILED,
                expected=frozenset({BatchState.COMPLETED}),
                outcome="Re-ran and failed again at Bronze.",
                now=NOW + timedelta(minutes=5),
            ),
        )
    )
    metadata.record_action_event(_requested_retry("act-5", batch_id="B-9999"))

    listed = metadata.list_action_records(batch_id="B-1244")
    assert [row.record_id for row in listed] == ["act-4"]
    assert listed[0].record.phase is ActionPhase.FAILED

    # Newest CURRENT PHASE first: act-4's verification (NOW+5m) is newer than
    # act-5's request (NOW), so the acted-on record leads.
    everything = metadata.list_action_records()
    assert [row.record_id for row in everything] == ["act-4", "act-5"]


def test_an_unknown_action_record_is_a_missing_object(metadata: MetadataDbPort) -> None:
    with pytest.raises(ObjectNotFoundError):
        metadata.get_action_record("never-requested")


def test_the_action_ledger_has_no_edit_path_for_anyone(metadata: MetadataDbPort) -> None:
    """Append-only the same way the audit ledger is: by the ABSENCE of a verb,
    not a permission that could be misconfigured."""
    for forbidden in ("update_action_record", "delete_action_record", "clear_action_records"):
        assert not hasattr(metadata, forbidden), f"metadata_db exposes {forbidden}"


# ── ops.incident_event · CF-V2-E12-04 ────────────────────────────────────────
def _open_event(incident_id: str, *, batch_id: str = "B-1244") -> IncidentEvent:
    return IncidentEvent(
        incident_id=incident_id,
        batch_id=batch_id,
        feed_id="fidelis-downstate-roster",
        signature="a3f9c2d1e8b7a6f0",
        state=IncidentState.OPEN,
        actor_subject="platform@cinqflow",
        occurred_ts=NOW,
        opened_ts=NOW,
    )


def test_an_incident_transition_is_a_second_row_and_the_current_state(
    metadata: MetadataDbPort,
) -> None:
    """A transition is a NEW event — 'what did this look like when it was
    acknowledged' stays a fact the ledger can answer."""
    metadata.record_incident_event(_open_event("INC-1"))
    metadata.record_incident_event(
        replace(
            _open_event("INC-1"),
            state=IncidentState.ACKNOWLEDGED,
            acknowledged_by="ops@cinqcare.test",
            actor_subject="ops@cinqcare.test",
            occurred_ts=NOW + timedelta(minutes=3),
        )
    )
    held = metadata.get_incident_event("INC-1")
    assert held.state is IncidentState.ACKNOWLEDGED
    assert held.acknowledged_by == "ops@cinqcare.test"


def test_the_state_filter_reads_the_current_state_not_the_history(
    metadata: MetadataDbPort,
) -> None:
    """An incident that was open and is now closed must not reappear as open
    through its older rows."""
    metadata.record_incident_event(_open_event("INC-2"))
    metadata.record_incident_event(
        replace(
            _open_event("INC-2"),
            state=IncidentState.RESOLVED,
            resolution="Re-ran validate_input, retried the batch.",
            resolved_ts=NOW + timedelta(minutes=18),
            occurred_ts=NOW + timedelta(minutes=18),
        )
    )
    metadata.record_incident_event(_open_event("INC-3", batch_id="B-9999"))

    open_now = metadata.list_incident_events(state=IncidentState.OPEN)
    assert [e.incident_id for e in open_now] == ["INC-3"]
    assert [e.incident_id for e in metadata.list_incident_events(batch_id="B-1244")] == ["INC-2"]


def test_an_untouched_incident_is_a_missing_object_here(metadata: MetadataDbPort) -> None:
    """The batch route computes OPEN for an incident with no events; asking
    the ledger for a specific id it never held is a different question."""
    with pytest.raises(ObjectNotFoundError):
        metadata.get_incident_event("INC-nobody-wrote")


def test_the_incident_ledger_has_no_edit_path_and_no_lifecycle_state(
    metadata: MetadataDbPort,
) -> None:
    """Two absences, both load-bearing: no edit verb (append-only the way the
    audit ledger is), and no governed lifecycle — an incident is an
    operational fact, and pushing it through Draft -> Approved would require
    somebody to approve that a batch failed."""
    for forbidden in ("update_incident_event", "delete_incident_event", "clear_incidents"):
        assert not hasattr(metadata, forbidden), f"metadata_db exposes {forbidden}"
    from dataclasses import fields as dataclass_fields

    assert "lifecycle_state" not in {f.name for f in dataclass_fields(IncidentEvent)}


# ── identity_exception / identity_exception_event · CF-V3-E9-01/E9-02 ───────
def _occurrence_event(
    *,
    source_member_id: str = "M-1",
    batch_id: str = "B-1244",
    occurred_ts: datetime = NOW,
) -> IdentityExceptionEvent:
    return IdentityExceptionEvent(
        event_id=str(uuid.uuid4()),
        exception_key=exception_key("fidelis", source_member_id),
        action=ExceptionEventAction.OCCURRENCE,
        source_system="fidelis",
        source_member_id=source_member_id,
        occurred_ts=occurred_ts,
        batch_id=batch_id,
        outcome=MatchOutcome.UNRESOLVED,
    )


def test_a_second_occurrence_grows_the_same_exception_not_a_new_one(
    metadata: MetadataDbPort,
) -> None:
    """ "The same person failing in three batches is one exception with three
    occurrences, not three items." — CF-V3-E9-02"""
    metadata.record_identity_exception_event(_occurrence_event(batch_id="B-1"))
    metadata.record_identity_exception_event(
        _occurrence_event(batch_id="B-2", occurred_ts=NOW + timedelta(days=1))
    )
    exc = metadata.get_identity_exception(exception_key("fidelis", "M-1"))
    assert exc.occurrence_count == 2
    assert exc.state is ExceptionState.OPEN


def test_an_assignment_is_a_transition_row_and_the_current_answer(
    metadata: MetadataDbPort,
) -> None:
    metadata.record_identity_exception_event(_occurrence_event(source_member_id="M-2"))
    metadata.record_identity_exception_event(
        IdentityExceptionEvent(
            event_id=str(uuid.uuid4()),
            exception_key=exception_key("fidelis", "M-2"),
            action=ExceptionEventAction.ASSIGNED,
            source_system="fidelis",
            source_member_id="M-2",
            occurred_ts=NOW + timedelta(hours=1),
            actor_subject="rule-engine",
            detail="enrollment-steward@cinqcare.test",
        )
    )
    exc = metadata.get_identity_exception(exception_key("fidelis", "M-2"))
    assert exc.state is ExceptionState.ASSIGNED
    assert exc.assigned_to == "enrollment-steward@cinqcare.test"


def test_the_state_filter_reads_the_folded_state_not_the_occurrence_rows(
    metadata: MetadataDbPort,
) -> None:
    """A resolved exception must not reappear as open through its own
    occurrence rows — the same discipline `list_incident_events` holds."""
    metadata.record_identity_exception_event(_occurrence_event(source_member_id="M-3"))
    metadata.record_identity_exception_event(
        IdentityExceptionEvent(
            event_id=str(uuid.uuid4()),
            exception_key=exception_key("fidelis", "M-3"),
            action=ExceptionEventAction.RESOLVED,
            source_system="fidelis",
            source_member_id="M-3",
            occurred_ts=NOW + timedelta(hours=2),
            actor_subject="steward@cinqcare.test",
        )
    )
    metadata.record_identity_exception_event(_occurrence_event(source_member_id="M-4"))

    open_now = metadata.list_identity_exceptions(state=ExceptionState.OPEN)
    assert exception_key("fidelis", "M-4") in {exc.key for exc in open_now}
    assert exception_key("fidelis", "M-3") not in {exc.key for exc in open_now}


def test_an_untouched_identity_exception_is_a_missing_object_here(
    metadata: MetadataDbPort,
) -> None:
    with pytest.raises(ObjectNotFoundError):
        metadata.get_identity_exception(exception_key("fidelis", "nobody-failed-for-this-one"))


def test_the_identity_exception_ledger_has_no_edit_path_for_anyone(
    metadata: MetadataDbPort,
) -> None:
    for forbidden in (
        "update_identity_exception_event",
        "delete_identity_exception_event",
        "clear_identity_exceptions",
    ):
        assert not hasattr(metadata, forbidden), f"metadata_db exposes {forbidden}"


# ── ops.variance_event · CF-V2-E13-03 ────────────────────────────────────────
def _count_variance(variance_id: str) -> Variance:
    return Variance(
        variance_id=variance_id,
        batch_id="B-1244",
        feed_id="fidelis-downstate-roster",
        kind=VarianceKind.COUNT,
        expected=Decimal("200"),
        actual=Decimal("150"),
        tolerance=Decimal("10"),
        opened_by="ops@cinqcare.test",
        opened_ts=NOW,
    )


def test_a_waiver_is_a_second_row_and_the_current_answer(metadata: MetadataDbPort) -> None:
    """Waiving writes a row rather than updating one — 'was this waived when
    March certified?' is answerable from what is stored."""
    opened = _count_variance("VAR-1")
    metadata.record_variance_event(opened, actor_subject=opened.opened_by, occurred_ts=NOW)
    waived = opened.waive(
        Waiver(
            waived_by="steward@cinqcare.test",
            reason="Payer confirmed a one-off enrollment freeze.",
            granted_on=NOW.date(),
            expires_on=NOW.date() + timedelta(days=30),
        )
    )
    metadata.record_variance_event(
        waived, actor_subject="steward@cinqcare.test", occurred_ts=NOW + timedelta(hours=1)
    )
    held = metadata.get_variance("VAR-1")
    assert held.outcome is VarianceOutcome.WAIVED
    assert held.waiver is not None
    assert held.waiver.waived_by == "steward@cinqcare.test"

    (listed,) = metadata.list_variances(batch_id="B-1244")
    assert listed.outcome is VarianceOutcome.WAIVED


def test_an_unrecorded_variance_is_a_missing_object(metadata: MetadataDbPort) -> None:
    with pytest.raises(ObjectNotFoundError):
        metadata.get_variance("VAR-nobody-opened")


def test_the_variance_ledger_has_no_edit_path_for_anyone(metadata: MetadataDbPort) -> None:
    for forbidden in ("update_variance", "delete_variance", "clear_variances"):
        assert not hasattr(metadata, forbidden), f"metadata_db exposes {forbidden}"


def test_every_audit_row_names_its_actor_type(metadata: MetadataDbPort) -> None:
    """An AI action that reads as human defeats the entire audit trail."""
    for actor_type in ActorType:
        metadata.append_audit(
            AuditEntry(
                object_type=ObjectType.FEED,
                object_id=f"feed-{actor_type.value}",
                version=1,
                action="touched",
                actor=Actor(subject=f"{actor_type.value}@test", actor_type=actor_type),
                occurred_ts=NOW,
            )
        )
    assert {e.actor_type for e in metadata.read_audit()} == set(ActorType)


# ── queue ────────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("queue"))
def queue(request: pytest.FixtureRequest, make: Callable[..., Any]) -> QueuePort:
    return make(request.param)


def test_a_repeated_dedupe_key_returns_the_existing_message(queue: QueuePort) -> None:
    """Replay safety starts at the producer, not the consumer."""
    first = queue.enqueue("run_feed", {"feed_id": "f"}, dedupe_key="f/2026-08-01")
    second = queue.enqueue("run_feed", {"feed_id": "f"}, dedupe_key="f/2026-08-01")
    assert first == second
    assert queue.stats().pending == 1


def test_a_claimed_message_is_acknowledged_when_the_body_succeeds(queue: QueuePort) -> None:
    queue.enqueue("run_feed", {"feed_id": "f"})
    with queue.claim("run_feed") as message:
        assert message is not None
    assert queue.stats() == queue.stats().__class__(pending=0, in_flight=0, failed=0, by_topic={})


def test_a_crashed_worker_returns_its_message_to_the_queue(queue: QueuePort) -> None:
    """A worker that crashes cannot strand work, and cannot half-acknowledge
    it either. Getting this wrong is a whole class of double-processing bug."""
    queue.enqueue("run_feed", {"feed_id": "f"})
    with pytest.raises(RuntimeError, match="worker died"), queue.claim("run_feed"):
        raise RuntimeError("worker died")
    assert queue.stats().pending == 1
    (returned,) = queue.drain("run_feed")
    assert returned.attempts == 1


def test_claiming_an_empty_topic_yields_none_rather_than_blocking(queue: QueuePort) -> None:
    with queue.claim("nothing-here") as message:
        assert message is None


# ── secrets ──────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("secrets"))
def secrets(request: pytest.FixtureRequest, make: Callable[..., Any]) -> SecretsPort:
    return make(request.param)


def test_a_missing_secret_raises_rather_than_returning_empty(secrets: SecretsPort) -> None:
    """An empty credential reaching an adapter produces a confusing auth
    failure at a distance, instead of a clear error where the profile is
    wrong."""
    with pytest.raises(SecretNotFoundError):
        secrets.fetch("llm-key")


def test_resolve_passes_non_references_through_untouched(secrets: SecretsPort) -> None:
    """A profile can be read without knowing which of its fields are secrets —
    the reference form carries that, in the profile."""
    assert secrets.resolve("pg-control") == "pg-control"
    assert secrets.resolve("postgresql://localhost/cinqflow") == "postgresql://localhost/cinqflow"


def test_resolve_dereferences_the_secret_form(make: Callable[..., Any]) -> None:
    from cinqflow.adapters.mock.secrets import MemSecrets

    resolved = MemSecrets({"llm-key": "value-from-the-vault"}).resolve("secret://llm-key")
    assert resolved == "value-from-the-vault"


# ── authn ────────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("authn"))
def authn(request: pytest.FixtureRequest, make: Callable[..., Any]) -> AuthnPort:
    return make(request.param)


def test_an_unknown_token_is_refused__nobody_is_anonymous(authn: AuthnPort) -> None:
    """Never returns an anonymous principal: a falsy return would be checked
    inconsistently, and the one site that forgot would be an anonymous write."""
    with pytest.raises(AuthenticationError):
        authn.verify("not-a-real-token")


def test_a_read_only_user_is_marked_as_unable_to_change_things(authn: AuthnPort) -> None:
    """ "Give Read-Only users full visibility but no buttons that change
    anything" — and the server refuses too, not just the menu."""
    analyst = authn.verify("dev-analyst@cinqcare.test")
    assert Role.READ_ONLY in analyst.roles
    assert analyst.may_change_things is False
    assert analyst.has_access is True


def test_an_engineer_may_change_things(authn: AuthnPort) -> None:
    engineer = authn.verify("dev-engineer@cinqcare.test")
    assert engineer.may_change_things is True


def test_a_user_in_no_group_is_a_valid_principal_without_access(authn: AuthnPort) -> None:
    """ "they see a clear 'no access assigned — contact your administrator'
    page ... they are NEVER shown a broken or empty application."

    Which requires this to be a STATE, not an error.
    """
    nobody = authn.verify("dev-nogroup@cinqcare.test")
    assert nobody.roles == frozenset()
    assert nobody.has_access is False
    assert nobody.display_name  # a person, with a name, who simply has no role


def test_the_port_never_accepts_a_password(authn: AuthnPort) -> None:
    """CF-V0-E2-01's first don't: "Store any credentials of its own"."""
    for forbidden in ("authenticate", "login", "password", "set_password", "credentials"):
        assert not hasattr(authn, forbidden), f"authn exposes {forbidden}"


# ── notification ─────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("notification"))
def notifier(request: pytest.FixtureRequest, make: Callable[..., Any]) -> NotificationPort:
    return make(request.param)


def test_an_alert_can_carry_the_citations_that_make_it_openable(
    notifier: NotificationPort,
) -> None:
    """The incumbent's standing complaint: "nothing explains why a feed is
    late, what is affected, or who to contact. Every alert becomes an
    investigation task." Citations are what end that."""
    from cinqflow.core.citations import parse

    notifier.alert(
        Alert(
            severity=Severity.CRITICAL,
            summary="Batch 8842 failed reconciliation at silver_raw",
            citations=(parse("recon:8842"),),
        )
    )
    (dispatched,) = notifier.dispatched
    assert dispatched.severity is Severity.CRITICAL
    assert str(dispatched.citations[0]) == "recon:8842"


# ── catalog ──────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("catalog"))
def catalog(request: pytest.FixtureRequest, make: Callable[..., Any]) -> CatalogPort:
    return make(request.param)


def test_introspection_reports_nothing_for_a_schema_the_engine_lacks(
    catalog: CatalogPort,
) -> None:
    """The conformance kit compares this against the portable DDL SPEC, not
    against another engine — so a drift is attributed to one engine instead of
    producing a diff nobody can adjudicate.

    An ABSENT schema introspects to nothing rather than raising, because the
    kit's job is to report "the spec declares this and the plane lacks it" as a
    finding. An exception here would make the kit's own failure indistinguish-
    able from the drift it exists to find.
    """
    assert list(catalog.introspect_schema("no_such_schema_exists")) == []


def test_introspection_reports_what_the_engine_actually_has(catalog: CatalogPort) -> None:
    """Whatever a schema holds, introspection describes it — every table named
    with its own schema, and every column carrying a PHI verdict.

    Asserted as a SHAPE rather than as a fixed table list, because this one
    suite runs against every fitted adapter: the dict stand-in holds what a
    test put in it and the Postgres adapter holds whatever `cinqflow install`
    provisioned. Pinning either engine's inventory here would assert the
    fixture, not the contract.
    """
    for table in catalog.introspect_schema("control"):
        assert table.schema == "control"
        assert table.qualified_name == f"control.{table.name}"
        for column in table.columns:
            assert column.name
            assert column.data_type
            assert isinstance(column.is_phi, bool)


def test_a_column_absent_from_the_contract_is_phi(catalog: CatalogPort) -> None:
    """Unclassified is masked, never public.

    `information_schema` has no opinion about whether a column is protected,
    so a plane that has drifted ahead of the schema contract carries columns
    nothing has classified. Defaulting those to `is_phi=False` would make a
    forgotten contract update a disclosure; defaulting to True makes it a
    legibility complaint, which is the failure worth having.
    """
    described = catalog.introspect_schema("information_schema")
    assert all(c.is_phi for t in described for c in t.columns)


# ── sql_query ────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("sql_query"))
def sql(request: pytest.FixtureRequest, make: Callable[..., Any]) -> SqlQueryPort:
    return make(request.param)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO bronze.members VALUES (1)",
        "UPDATE control.batch_control SET state = 'COMPLETED'",
        "DELETE FROM quarantine.records",
        "DROP TABLE bronze.members",
        "TRUNCATE control.error_log",
        "GRANT ALL ON bronze TO PUBLIC",
    ],
)
def test_the_governed_query_pin_refuses_every_write(sql: SqlQueryPort, statement: str) -> None:
    """Refusing writes in the MOCK matters: otherwise the negative test passes
    at rung 0 and nobody discovers the real adapter never implemented it."""
    with pytest.raises(QueryRefusedError):
        sql.query(statement)


# ── orchestration ────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("orchestration"))
def orchestration(request: pytest.FixtureRequest, make: Callable[..., Any]) -> OrchestrationPort:
    return make(request.param)


def test_registering_a_feed_takes_a_schedule_and_nothing_else(
    orchestration: OrchestrationPort,
) -> None:
    """ONE generic DAG parameterised by feed_id. There is no hook for per-feed
    logic, which is what stops feed-specific code hiding in the orchestrator
    where CF-V0-E8-01's lint would never look."""
    orchestration.register("fidelis-downstate-roster", Schedule(cron="0 3 1 * *"))
    assert [r.feed_id for r in orchestration.due(NOW)] == ["fidelis-downstate-roster"]


def test_a_pause_requires_a_stated_reason(orchestration: OrchestrationPort) -> None:
    """A paused feed with no stated reason becomes a mystery nobody dares
    unpause."""
    orchestration.register("f", Schedule(cron="0 3 1 * *"))
    with pytest.raises(ValueError, match="reason"):
        orchestration.pause("f", reason="   ")


def test_a_paused_feed_does_not_come_due(orchestration: OrchestrationPort) -> None:
    """ "automatically pause downstream processing when something upstream
    fails" — bad upstream data never cascades."""
    orchestration.register("f", Schedule(cron="0 3 1 * *"))
    orchestration.pause("f", reason="upstream Mirth channel is down")
    assert list(orchestration.due(NOW)) == []
    orchestration.resume("f")
    assert [r.feed_id for r in orchestration.due(NOW)] == ["f"]


# ── identity ─────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("identity"))
def identity(request: pytest.FixtureRequest, make: Callable[..., Any]) -> IdentityPort:
    return make(request.param)


def test_an_unapproved_merge_is_refused_at_any_confidence(identity: IdentityPort) -> None:
    """R4 is human-always, never automated, NOT CONFIGURABLE.

    The refusal ships in Wave 0 even though identity resolution does not,
    because R4 is a property of the platform rather than a Wave-3 feature — and
    a port that would accept an unapproved merge today is one someone could
    call today.
    """
    with pytest.raises(UnapprovedMergeError):
        identity.merge(left="MBR000001", right="MBR000002")


def test_an_approved_merge_names_its_steward(identity: IdentityPort) -> None:
    identity.merge(left="MBR000001", right="MBR000002", steward_approval_id="APPROVAL-4471")


def test_submission_accounting_balances(identity: IdentityPort) -> None:
    """G4: submitted == resolved + unresolved + failed."""
    records = [{"source_system": "fidelis", "source_member_id": f"MBR{i:06d}"} for i in range(1, 4)]
    entries = identity.submit(records, batch_id="8842")
    assert len(entries) == len(records)


# ── legacy_readonly · CF-V3-E9-04's first real caller ────────────────────────
#
# "Fitted in Wave 0 as a NAMED SEAT so conformance can report it unenergized
#  ... Parallel run is CF-V5-E15-*." Coverage/parity telemetry is a narrower,
# earlier use of the SAME socket — a standing daily comparison of one column
# (LinkId) for OurIds the lake already knows about, not the full row-for-row
# migration comparator Wave 5's parallel run needs. The port's own contract
# (`fetch`/`compare`, read-only, no free-form query) is unchanged either way.
@pytest.fixture(params=adapters_for("legacy_readonly"))
def legacy(request: pytest.FixtureRequest, make: Callable[..., Any]) -> LegacyReadOnlyPort:
    return make(request.param)


def test_the_port_offers_no_write_verb_at_all(legacy: LegacyReadOnlyPort) -> None:
    """Structural, not a policy comment — ADR-0013's additive-only estate has
    no seat for a comparator that could write back to it."""
    for forbidden in ("write", "update", "delete", "insert", "execute"):
        assert not hasattr(legacy, forbidden), f"legacy_readonly exposes {forbidden}"


def test_fetching_an_unfitted_query_name_answers_empty_not_an_error(
    legacy: LegacyReadOnlyPort,
) -> None:
    assert legacy.fetch("no-such-named-query") == ()


def test_compare_matches_on_key_and_reports_only_what_disagrees(
    legacy: LegacyReadOnlyPort,
) -> None:
    ours = [{"key": "OUR-1", "link_id": "LINK-1"}, {"key": "OUR-2", "link_id": "LINK-2"}]
    differences = legacy.compare("ourid_link_id_crosswalk", ours)
    # An unfitted query has no legacy rows at all — every key is a difference,
    # never a silent pass, which is what "report coverage without its
    # denominator visible" would look like for a parity check instead.
    assert {d.key for d in differences} == {"OUR-1", "OUR-2"}


# ── compute_job ──────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("compute_job"))
def compute(request: pytest.FixtureRequest, make: Callable[..., Any]) -> ComputeJobPort:
    return make(request.param)


def _plan() -> LogicalPlan:
    return LogicalPlan(
        feed_id="fidelis-downstate-roster",
        feed_version=1,
        steps=compile_steps(
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            contract_version=1,
            mapping_version=1,
            file_pattern="_CINQDOWNSTATE_Member_Roster_*.xlsx",
            column_count=47,
            cast_columns=("date_of_birth",),
            rule_ids=("DQ-002",),
            target_table="silver_raw.members",
        ),
    )


def test_a_run_reports_which_stages_completed(compute: ComputeJobPort) -> None:
    run = compute.run(_plan(), batch_id="8842")
    assert run.completed_stages == (Layer.BRONZE, Layer.SILVER_RAW)
    assert run.succeeded is True


def test_resuming_does_not_rerun_earlier_stages(compute: ComputeJobPort) -> None:
    """ "processing resumes from Silver Raw only — Bronze is NOT re-loaded, no
    duplicates appear" — CF-V0-E8-01, exception.

    Bronze is append-only, so a re-run either duplicates or is refused. Both
    are defects, which is why this is a contract and not an optimisation.
    """
    run = compute.run(_plan(), batch_id="8842", resume_from=Layer.SILVER_RAW)
    assert run.completed_stages == (Layer.SILVER_RAW,)
    assert Layer.BRONZE not in run.completed_stages


def test_resuming_at_a_stage_the_plan_does_not_write_is_refused(
    compute: ComputeJobPort,
) -> None:
    with pytest.raises(ComputeError, match="resume"):
        compute.run(_plan(), batch_id="8842", resume_from=Layer.GOLD)


def test_a_completed_run_is_pollable_by_id(compute: ComputeJobPort) -> None:
    run = compute.run(_plan(), batch_id="8842")
    assert compute.poll(run.run_id).run_id == run.run_id
    assert list(compute.metrics(run.run_id)) == list(run.results)


# ── cache ────────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("cache"))
def cache(request: pytest.FixtureRequest, make: Callable[..., Any]) -> Any:
    return make(request.param)


def test_the_cache_pin_answers_every_read_with_a_miss(cache: Any) -> None:
    """ADR-0014: no cache until measurement demands one.

    Code written against this port is correct whether or not a cache is ever
    fitted — which is the property that makes adding one later safe.
    """
    cache.set("feed:fidelis", {"anything": True})
    assert cache.get("feed:fidelis") is None
