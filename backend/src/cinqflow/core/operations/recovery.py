"""CF-V2-E8-04 — the recovery toolkit, and the proof that it cannot double-load.

    "Make 'reprocess only failed records' a first-class operation — full reruns
     of 22-million-row batches to fix 200 records is the waste we are ending."
    "Handle the replay bookkeeping automatically (the manual 'delete the control
     rows so bronze will accept the replay' era ends here)."
    — CF-V2-E8-04

    "Given a mapping fix is approved for a batch with 214 quarantined records,
     when the operator runs reprocess-failed-only, then exactly 214 records
     re-enter at Silver Raw, 209 pass, 5 re-quarantine with the same reasons,
     and the batch's ledger shows the whole episode."
    — CF-V2-E8-04, happy path

THE INSIGHT THAT MAKES ALL FOUR RECOVERIES ONE THING. Every recovery is a
COMPILED PLAN with a narrowed input set and a starting stage. The engine that
runs a fresh batch runs a recovery — same compiler, same steps, same
reconciliation — because `LogicalPlan` already parameterises both. So there is
no second execution path to keep honest, which is the usual place a recovery
feature grows its own bugs.

    restart_from_stage  → same rows, start at stage S
    reprocess_batch     → same rows, start at READ
    reprocess_failed    → quarantined rows only, start at the quarantining stage
    backdate            → a different business date's rows, start at READ

WHY THE CONTROL-ROW SURGERY ERA ENDS. Bronze is append-only and refuses an
UPDATE at the database layer, so a replay that re-lands the same rows would be
refused — which is why the incumbent's operators deleted control rows first. The
platform's own answer already exists: the input registry dedups by fingerprint,
and Bronze is keyed by (batch_id, record_hash). A recovery therefore either
(a) reuses the SAME batch_id and writes nothing new to Bronze, or (b) allocates
a SUPERSEDING batch_id and marks the prior one superseded. `supersedes` on the
plan is which, and `prove_idempotent` is the check that one of them is true.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum, unique

from cinqflow.core.model.vocabulary import Layer
from cinqflow.core.operations.actions import DoubleLoadError, OpsAction, Preview

#: What one row costs to move, per stage, in the dev plane. A profile value at
#: rungs 3-4; a constant here so the ESTIMATE exists from day one. An estimate
#: that waits for real billing data is an estimate nobody ever ships.
COST_PER_MILLION_ROWS = Decimal("0.084")


@unique
class ReplayMode(StrEnum):
    """How the recovery avoids loading a row twice. Exactly two answers.

    IN_PLACE     reuse the batch_id. Bronze already holds these rows under it,
                 so nothing new lands and the append-only law is untouched.
    SUPERSEDING  a new batch_id, and the prior batch is marked superseded. Used
                 when the INPUT differs — a corrected file, a backdated period —
                 because those rows are genuinely new facts.
    """

    IN_PLACE = "in_place"
    SUPERSEDING = "superseding"


@dataclass(frozen=True)
class RecoveryPlan:
    """A recovery, fully described before it runs.

    Frozen and inspectable on purpose: `preview()` renders it, `prove_idempotent`
    checks it, and the executor consumes it. Three readers, one description —
    the same discipline that makes `LogicalPlan` serve engine, agent and eval.
    """

    action: OpsAction
    batch_id: str
    feed_id: str
    start_stage: Layer
    row_count: int
    mode: ReplayMode
    #: Only for REPROCESS_FAILED_ONLY: the quarantined record keys.
    record_keys: tuple[str, ...] = field(default_factory=tuple)
    #: Only for SUPERSEDING: the batch this one replaces.
    supersedes: str = ""
    #: Only for BACKDATE.
    business_date: date | None = None
    overlapping_batches: tuple[str, ...] = field(default_factory=tuple)
    supersede_acknowledged: bool = False

    @property
    def stages(self) -> tuple[Layer, ...]:
        """Every stage this plan will touch, from the start stage onward.

        Landing is never re-entered: the file is already registered, immutable
        and archived, and re-landing it would trip the fingerprint refusal —
        correctly, but confusingly. Recovery starts at Bronze or later.
        """
        order = list(Layer)
        start = order.index(self.start_stage)
        return tuple(layer for layer in order[start:] if layer is not Layer.LANDING)

    @property
    def estimated_cost_usd(self) -> Decimal:
        millions = Decimal(self.row_count) / Decimal(1_000_000)
        return (millions * COST_PER_MILLION_ROWS * Decimal(len(self.stages))).quantize(
            Decimal("0.01")
        )

    @property
    def replaces_a_period(self) -> bool:
        """Whether this plan lands rows on top of work that already exists.

        THE DISTINCTION THAT MAKES CONDITION 1 CORRECT. A superseding plan is
        dangerous when something is already there — a reprocess always replaces
        the batch it names, and a backdate replaces whatever overlapped. A
        backdate for a period that was NEVER processed replaces nothing: there
        is no earlier copy to double, and the second copy the rule imagines
        does not exist.

        Refusing that case made a legitimate first-time backdate unreachable,
        which is worse than untidy — it sends an operator back to the manual
        control-row surgery this story exists to end.
        """
        return self.action is not OpsAction.BACKDATE or bool(self.overlapping_batches)

    def prove_idempotent(self) -> None:
        """Refuse, BEFORE executing, any plan that could load a row twice.

            "Allow a recovery that would double-load data — every path proves
             idempotency before executing."
            — CF-V2-E8-04, a documented don't

        Three conditions, and each corresponds to a real way this goes wrong:

          1. A SUPERSEDING plan that replaces existing work while naming no
             superseded batch is a second copy of the same period wearing a
             different batch_id. This is the duplicate-Feb-2025-roster
             incident, mechanised. A backdate onto an empty period is exempt —
             see `replaces_a_period`.
          2. An IN_PLACE plan that starts before the layer its rows already
             occupy would re-append to Bronze under the same batch_id — refused
             by the database, so refuse it here where the message is useful.
          3. A BACKDATE with overlaps needs an EXPLICIT supersede decision, as
             the story's exception path requires. Silence is not consent.
        """
        if self.mode is ReplayMode.SUPERSEDING and not self.supersedes and self.replaces_a_period:
            raise DoubleLoadError(
                f"{self.action.value} on {self.batch_id} is superseding but names no "
                "batch to supersede — that is a second copy of the same period, which "
                "is the duplicate-roster incident with extra steps."
            )
        if self.mode is ReplayMode.IN_PLACE and self.start_stage is Layer.BRONZE:
            raise DoubleLoadError(
                f"{self.action.value} on {self.batch_id} would re-append to Bronze under "
                "an existing batch_id. Bronze is append-only and would refuse it. Restart "
                "at silver_raw, or allocate a superseding batch."
            )
        if self.overlapping_batches and not self.supersede_acknowledged:
            overlaps = ", ".join(self.overlapping_batches)
            raise DoubleLoadError(
                f"a backdated run for {self.business_date} overlaps existing batches "
                f"({overlaps}). An explicit supersede decision is required — no "
                "accidental double-count."
            )

    def preview(self) -> Preview:
        """What the operator confirms against, in the surface's own shape.

        ONE `Preview` type for every action, recovery included. A recovery
        preview of its own would be a second thing for the console to render
        and a second place for "an approval identifier is required" to be
        forgotten.
        """
        if self.mode is ReplayMode.SUPERSEDING and self.supersedes:
            headline = f"supersedes batch {self.supersedes}"
        elif self.record_keys:
            headline = f"{len(self.record_keys):,} quarantined records only"
        else:
            headline = f"{self.action.value} on {self.batch_id}"
        return Preview(
            action=self.action,
            target=self.batch_id,
            what_will_happen=headline,
            scope_records=self.row_count,
            scope_stages=self.stages,
            estimated_cost_usd=self.estimated_cost_usd,
            overlaps=self.overlapping_batches,
            requires_approval_identifier=self.action.mutates_production,
        )


# ── the four constructors ────────────────────────────────────────────────────


def restart_from(*, batch_id: str, feed_id: str, stage: Layer, rows: int) -> RecoveryPlan:
    """Resume a failed batch from the stage after the last completed one.

    IN_PLACE: the rows are already in Bronze under this batch_id, and the
    stages downstream of `stage` are recomputed. This is the ordinary
    restart-from-stage guarantee Wave 0 already proves as an invariant — the
    recovery surface just gives it a button.
    """
    if stage is Layer.LANDING:
        raise DoubleLoadError(
            "landing is not a restart point — the file is registered, immutable and "
            "archived, and re-landing it trips the fingerprint refusal by design"
        )
    return RecoveryPlan(
        action=OpsAction.RESTART_FROM_STAGE,
        batch_id=batch_id,
        feed_id=feed_id,
        start_stage=stage,
        row_count=rows,
        mode=ReplayMode.IN_PLACE,
    )


def reprocess_batch(*, batch_id: str, feed_id: str, rows: int, new_batch_id: str) -> RecoveryPlan:
    """Re-run everything after a configuration fix.

    SUPERSEDING: the mapping or rule changed, so the OUTPUT will differ. A new
    batch_id keeps both versions in the ledger, which is what makes "what did
    this look like before the fix" answerable four months later.
    """
    return RecoveryPlan(
        action=OpsAction.REPROCESS_BATCH,
        batch_id=new_batch_id,
        feed_id=feed_id,
        start_stage=Layer.SILVER_RAW,
        row_count=rows,
        mode=ReplayMode.SUPERSEDING,
        supersedes=batch_id,
    )


def reprocess_failed_only(
    *,
    batch_id: str,
    feed_id: str,
    record_keys: Sequence[str],
    stage: Layer = Layer.SILVER_RAW,
) -> RecoveryPlan:
    """THE ONE THIS STORY EXISTS FOR.

        "full reruns of 22-million-row batches to fix 200 records is the waste
         we are ending"

    IN_PLACE with a narrowed input: exactly the quarantined records re-enter,
    at the stage that quarantined them. The batch_id is unchanged, so the
    ledger shows one episode rather than two batches — which is what the happy
    path means by *"the batch's ledger shows the whole episode"*.
    """
    if not record_keys:
        raise DoubleLoadError(
            "reprocess-failed-only with no failed records would re-run the whole batch "
            "under its own batch_id — say what you mean and use reprocess_batch"
        )
    return RecoveryPlan(
        action=OpsAction.REPROCESS_FAILED_ONLY,
        batch_id=batch_id,
        feed_id=feed_id,
        start_stage=stage,
        row_count=len(record_keys),
        mode=ReplayMode.IN_PLACE,
        record_keys=tuple(record_keys),
    )


def backdate(
    *,
    feed_id: str,
    business_date: date,
    new_batch_id: str,
    rows: int,
    overlapping: Sequence[str] = (),
    supersede_acknowledged: bool = False,
) -> RecoveryPlan:
    """Run a corrected file for a period that may already have been processed.

    SUPERSEDING always: a backdated run is by definition a new fact about an
    old period. `overlapping` is computed by the caller from `batch_control`
    filtered on business date — and if it is non-empty, `prove_idempotent`
    refuses until somebody has explicitly decided to supersede.
    """
    return RecoveryPlan(
        action=OpsAction.BACKDATE,
        batch_id=new_batch_id,
        feed_id=feed_id,
        start_stage=Layer.SILVER_RAW,
        row_count=rows,
        mode=ReplayMode.SUPERSEDING,
        supersedes=overlapping[0] if overlapping else "",
        business_date=business_date,
        overlapping_batches=tuple(overlapping),
        supersede_acknowledged=supersede_acknowledged,
    )
