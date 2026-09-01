"""CF-V3-E10-03 — G5: the ODS certification gate. I/O and the governance act.

    "Silver ODS publication per batch [must] pass a final gate: relationship
     validation ..., certification checks, and compatibility validation for
     existing consumers, so that downstream systems can consume ODS data
     with the same confidence as a payer file."
    "Happy path — ... relationships validate, certification attaches,
     consumers are notified, and the batch appears in their views
     atomically."
    "Exception — Given 0.3% of claims reference members absent from the
     member table, when the gate runs, then publication holds, the
     orphaned claims are listed with their source batches, and the
     incident flow picks it up with the evidence attached."
    "Don't — Publish partially: a batch is visible downstream in full or
     not at all."
    — CF-V3-E10-03

REUSES `core.certification.certify()` UNCHANGED. `CheckKind.RELATIONSHIP_
INTEGRITY` is not in `MANDATORY` — `certify()`'s own policy already fails
certification on any completed-and-failed check, mandatory or not
(`core.relationship_integrity`'s own docstring proves this). So this gate
builds BALANCE/RECONCILIATION/DROP_LEDGER the same way the existing
Wave-2 certification screen does (reading `control.get_reconciliation`,
which ALREADY carries Silver-ODS rows once `workers.ods_load` writes them),
adds its own relationship checks, and hands the whole set to the SAME
`certify()` the rest of the platform trusts.

WHY A NEW GOVERNED OBJECT, NOT A NEW `BatchState`. `BatchState` is a closed,
8-member vocabulary the module's own docstring calls out as a plate change
to extend — and "certified" is a DECISION, not a state a pipeline computes.
`ObjectType.ODS_BATCH_CERTIFICATION` travels the SAME lifecycle engine
`ODS_MODEL` does, which is where "author never approves own change" and
"nothing reaches Published without a named approver" come from, for free.

ATOMICITY IS BY CONSTRUCTION. "The batch appears in their views atomically"
because visibility is gated on ONE fact: does a PUBLISHED
`ODS_BATCH_CERTIFICATION` exist for this `batch_id`? There is no
intermediate state a downstream reader could observe — the object is
DRAFT/PENDING_REVIEW/APPROVED (invisible) or PUBLISHED (visible), never
"half of it."

A FAILED GATE HOLDS AND OPENS ONE INCIDENT, NEVER DRAFTS ANYTHING.
`BatchCertificationRecord.__post_init__` refuses to even construct for a
verdict that is not Certified/Certified-with-Waiver — this worker's `run()`
checks the verdict itself first for the same reason `IdentityWorker._hold`
checks before ever building a `CrosswalkEntry`: a refusal deep inside a
constructor is correct but late; the gate should never reach for it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.certification import Certification, Check, CheckKind, certify
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.lifecycle import submit
from cinqflow.core.model.governed import Actor, GovernedObject
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, Layer
from cinqflow.core.registry.ods_batch_certification import as_governed, from_certification
from cinqflow.core.relationship_integrity import (
    Relationship,
    RelationshipCheckResult,
    check_relationship,
)
from cinqflow.ports.control_tables import ControlTablesPort, ErrorRecord
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.notification import Alert, NotificationPort, Severity
from cinqflow.ports.ods_load import OdsLoadPort
from cinqflow.workers.incidents import IncidentWorker


@dataclass(frozen=True)
class GateOutcome:
    """What the gate did, for whoever called it to log or assert against."""

    batch_id: str
    certification: Certification
    draft: GovernedObject | None

    @property
    def held(self) -> bool:
        return self.draft is None


class OdsCertificationGate:
    def __init__(
        self,
        *,
        ods: OdsLoadPort,
        control: ControlTablesPort,
        metadata: MetadataDbPort,
        incidents: IncidentWorker,
        notify: NotificationPort,
    ) -> None:
        self._ods = ods
        self._control = control
        self._metadata = metadata
        self._incidents = incidents
        self._notify = notify

    def run(
        self,
        *,
        batch_id: str,
        feed_id: str,
        model_version: str,
        relationships: Sequence[Relationship],
        author: Actor,
        submit_comment: str = "Automated by the Silver ODS certification gate.",
        now: datetime | None = None,
    ) -> GateOutcome:
        stamp = now or datetime.now(UTC)
        checks = self._standard_checks(batch_id)
        relationship_results = [
            self._check(relationship, batch_id=batch_id) for relationship in relationships
        ]
        checks.extend(result.as_check() for result in relationship_results)
        # CF-V3-E13-02. Whatever the financial and member-universe packs
        # already opened against this batch — `certify()`'s own policy
        # already fails a batch on any BLOCKING variance regardless of
        # which check kind ran, so "the pack attaches to the month's
        # certification" costs this gate nothing beyond reading the same
        # ledger the manual variance route already writes to.
        variances = self._metadata.list_variances(batch_id=batch_id, feed_id=feed_id)

        certification = certify(
            batch_id=batch_id, feed_id=feed_id, checks=checks, variances=variances, now=stamp
        )

        if not certification.publishable:
            self._hold(certification, relationship_results, stamp=stamp)
            return GateOutcome(batch_id=batch_id, certification=certification, draft=None)

        record = from_certification(certification, model_version=model_version)
        draft = self._metadata.save(as_governed(record, author=author, created_ts=stamp))
        submitted, entry = submit(draft, actor=author, comment=submit_comment, now=stamp)
        recorded = self._metadata.record_transition(submitted, entry)
        return GateOutcome(batch_id=batch_id, certification=certification, draft=recorded)

    def _standard_checks(self, batch_id: str) -> list[Check]:
        """BALANCE / RECONCILIATION / DROP_LEDGER, read fresh from
        reconciliation — the same three facts the existing Wave-2
        certification screen derives, scoped here to whichever stages
        already wrote a `Reconciliation` row for this batch (Silver Raw
        AND, once `workers.ods_load` has run, Silver ODS)."""
        recons = self._control.get_reconciliation(batch_id)
        citation = CitationId(kind=CitationKind.RECON, subject=batch_id)
        return [
            Check(
                kind=CheckKind.BALANCE,
                passed=bool(recons) and all(r.balances for r in recons),
                completed=bool(recons),
                evidence=(
                    f"rows_in == rows_out + quarantined + attributed_drops on {len(recons)} "
                    "stage(s)"
                    if recons
                    else "no reconciliation recorded yet"
                ),
                citation=citation,
            ),
            Check(
                kind=CheckKind.RECONCILIATION,
                passed=bool(recons) and all(r.unexplained == 0 for r in recons),
                completed=bool(recons),
                evidence=(
                    f"{sum(r.unexplained for r in recons)} unexplained rows across "
                    f"{len(recons)} stage(s)"
                    if recons
                    else "no reconciliation recorded yet"
                ),
                citation=citation,
            ),
            Check(
                kind=CheckKind.DROP_LEDGER,
                passed=bool(recons)
                and all(
                    entry.rule_id not in {"other", "unknown", ""}
                    for recon in recons
                    for entry in recon.drop_ledger
                ),
                completed=bool(recons),
                evidence=(
                    f"{sum(e.record_count for r in recons for e in r.drop_ledger)} excluded "
                    "row(s), every one attributed to a rule"
                ),
                citation=citation,
            ),
        ]

    def _check(self, relationship: Relationship, *, batch_id: str) -> RelationshipCheckResult:
        checked = self._ods.count_rows(relationship.child_entity, batch_id=batch_id)
        sample = self._ods.orphans(
            relationship.child_entity,
            relationship.child_column,
            relationship.parent_entity,
            relationship.parent_column,
            batch_id=batch_id,
        )
        total = self._ods.count_orphans(
            relationship.child_entity,
            relationship.child_column,
            relationship.parent_entity,
            relationship.parent_column,
            batch_id=batch_id,
        )
        return check_relationship(
            child_entity=relationship.child_entity,
            child_column=relationship.child_column,
            parent_entity=relationship.parent_entity,
            parent_column=relationship.parent_column,
            checked=checked,
            orphan_rows=sample,
            orphan_count=total,
        )

    def _hold(
        self,
        certification: Certification,
        relationship_results: Sequence[RelationshipCheckResult],
        *,
        stamp: datetime,
    ) -> None:
        """ "Publication holds, the orphaned claims are listed with their
        source batches, and the incident flow picks it up with the
        evidence attached" — one `ErrorRecord` per failed CHECK (the
        aggregate), plus one per ORPHANED ROW for a failed relationship
        check, so an operator sees the individual claims, not only a count.
        """
        self._control.update_batch_state(certification.batch_id, BatchState.BLOCKED)
        failed_kinds = {failed.kind for failed in certification.failed}
        for failed in certification.failed:
            self._control.record_error(
                ErrorRecord(
                    error_id_hash=_error_hash(certification.batch_id, failed.kind.value),
                    batch_id=certification.batch_id,
                    stage=Layer.SILVER_ODS,
                    category=ErrorCategory.VALIDATION,
                    message=failed.evidence,
                    occurred_ts=stamp,
                    rule_id=f"CERT-{failed.kind.value.upper()}",
                )
            )
        if CheckKind.RELATIONSHIP_INTEGRITY in failed_kinds:
            for result in relationship_results:
                if result.passed:
                    continue
                for orphan in result.orphans:
                    self._control.record_error(
                        ErrorRecord(
                            error_id_hash=_error_hash(
                                certification.batch_id,
                                f"{result.child_entity}.{result.child_column}."
                                f"{orphan.key_value}.{orphan.source_batch}",
                            ),
                            batch_id=certification.batch_id,
                            stage=Layer.SILVER_ODS,
                            category=ErrorCategory.VALIDATION,
                            message=(
                                f"{result.child_entity}.{result.child_column}="
                                f"{orphan.key_value!r} has no matching "
                                f"{result.parent_entity}.{result.parent_column}"
                            ),
                            occurred_ts=stamp,
                            record_key=str(orphan.key_value),
                            rule_id=f"CERT-ORPHAN-{result.child_entity.upper()}",
                        )
                    )
        self._incidents.on_batch_failed(certification.batch_id, now=stamp)

    def notify_consumers(self, consumers: Sequence[str], *, batch_id: str, entity: str) -> None:
        """Called by the PUBLISH side-effect, once the Data Steward
        approves — never at draft time, "not after the break" cuts both
        ways: not too early either, or a consumer is told about data that
        might still be rejected."""
        if not consumers:
            return
        self._notify.alert(
            Alert(
                severity=Severity.INFO,
                summary=f"{entity} published for batch {batch_id}",
                detail=f"Registered consumers: {', '.join(consumers)}",
                citations=(CitationId(kind=CitationKind.RECON, subject=batch_id),),
            )
        )


def _error_hash(batch_id: str, kind: str) -> str:
    return hashlib.sha256(f"ods-certification:{batch_id}:{kind}".encode()).hexdigest()
