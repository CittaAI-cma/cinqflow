"""CF-V3-E10-01 — deploy the canonical ODS model as versioned, governed truth.

    "the client's canonical model workbooks ... deployed as versioned, managed
     table definitions ... so that the model stops living in contested
     spreadsheets ('draft' vs 'final') and becomes one deployed, versioned
     truth every mapping targets."
    "Given the claims model review resolved all differences, when the model
     deploys, then the ODS structures exist with audit columns and version
     tags."
    "Given two workbook versions disagree on a column's nullability ...
     deployment waits for the steward's call, which is recorded with
     rationale."
    — CF-V3-E10-01

WHY THE DISCREPANCY GATE FIRES AT PUBLISH, NEVER AT DRAFT OR REVIEW.
`UndecidedDiscrepancyError`'s own docstring: "a model may be BUILT with open
discrepancies (that is what review is for); it may not be DEPLOYED with one."
`publish_ods_model` checks `refuse_undecided` immediately before the
Approved -> Published transition — the last possible moment — so a model
with open questions can still be drafted, reviewed and discussed; only its
actual deployment is what the gate blocks.

WHY THIS IS A WORKER, NOT A CORE FUNCTION. `core.registry.ods_model` is
provably correct with no database in the room — `render()`, `refuse_undecided`,
`diff()` are all pure. Saving a governed object and recording a lifecycle
transition are both `MetadataDbPort` calls, which is I/O core may never
perform (`core-purity`). This module is the thin seam: it calls core's own
pure functions for every DECISION and a port only to PERSIST what was
decided — the same shape `IdentityWorker` keeps between `core.identity` and
`ControlTablesPort`.

VERSION STAYS FIXED ACROSS DRAFT -> REVIEW -> APPROVED -> PUBLISHED — the same
object moving through states, not a new version per transition
(`GovernedObject.transition_to` never bumps `version`; only a NEW candidate
model, built from a diff against what is currently published, would carry
`version + 1`). "Version the model so any batch can state which model version
it loaded into" is `BatchControl.model_version`, already a field on that
dataclass since Wave 0 — filling it in is CF-V3-E8-05's load stage, not this
module's concern.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from cinqflow.core.lifecycle import approve as lifecycle_approve
from cinqflow.core.lifecycle import publish as lifecycle_publish
from cinqflow.core.lifecycle import submit as lifecycle_submit
from cinqflow.core.model.governed import Actor, GovernedObject
from cinqflow.core.model.identity import Role
from cinqflow.core.registry.ods_model import (
    ModelDiscrepancy,
    OdsModel,
    as_governed,
    refuse_undecided,
)
from cinqflow.ports.metadata_db import MetadataDbPort


def publish_ods_model(
    metadata: MetadataDbPort,
    model: OdsModel,
    discrepancies: Sequence[ModelDiscrepancy],
    *,
    author: Actor,
    reviewer: Actor,
    reviewer_roles: frozenset[Role],
    publisher: Actor,
    publisher_roles: frozenset[Role],
    review_comment: str,
    approval_comment: str,
    now: datetime | None = None,
) -> GovernedObject:
    """Draft -> In Review -> Approved -> Published, in one call.

    A REAL model deploy is a multi-day human process (draft, discuss,
    decide, review, sign off) that a single function call cannot honestly
    represent — this exists for the ENGINEERED route's realistic case,
    where drafting and reviewing are close together in time and the
    interesting gate is the discrepancy check at the end, not a long-running
    conversation the generic `/api/objects/ods_model/{id}/submit` etc. routes
    already carry for the cases that need one. Both paths persist through
    the SAME `core.lifecycle` functions and the same port calls; this is a
    convenience composition of them, not a second lifecycle.
    """
    stamp = now or datetime.now(UTC)
    draft = metadata.save(as_governed(model, author=author, created_ts=stamp))

    in_review, review_entry = lifecycle_submit(
        draft, actor=author, comment=review_comment, now=stamp
    )
    metadata.record_transition(in_review, review_entry)

    approved, approve_entry = lifecycle_approve(
        in_review,
        actor=reviewer,
        roles=reviewer_roles,
        comment=approval_comment,
        now=stamp,
    )
    metadata.record_transition(approved, approve_entry)

    # The deploy-time gate — the LAST possible moment, never earlier.
    refuse_undecided(discrepancies)

    published, publish_entry = lifecycle_publish(
        approved, actor=publisher, roles=publisher_roles, now=stamp
    )
    return metadata.record_transition(published, publish_entry)
