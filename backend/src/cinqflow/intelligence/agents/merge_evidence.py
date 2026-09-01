"""CF-V3-E9-03 — the merge evidence agent, wired.

    "an AI-prepared evidence card — demographics side by side, source
     history, prior identity events — and a preview of exactly which records
     would repoint or separate, with the decision itself always mine"
    — CF-V3-E9-03

The graph is data in `core/agents/merge_evidence/graph.py`; this is the node
implementation, which touches the gateway. Two properties are enforced here
and tested independently:

  1. `_gather` reaches NO model. A test walks this module's AST and asserts
     its body never reaches the gateway.
  2. Nothing here writes anything, ever — not a proposal (R4's `RiskClass.
     automatable` is `False`, and `core.proposals.Proposal.__post_init__`
     would refuse one anyway), not a governed object, not a database row.
     `prepare()` returns an `EvidenceCard`, a plain value, straight to the
     caller — there is no persistence layer between this agent and the
     steward's own screen because there is no decision here to persist.

DEGRADATION, NOT BLOCKING. A gateway failure (`ManualPathRequiredError`)
returns a card with an empty narrative rather than propagating — the plan and
the comparison `_gather` already assembled are exactly what a steward needs
to review a merge even when the model cannot be reached, and a review screen
that 500s because a narration service is down would be a worse failure than
one line missing from the card.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.agents.merge_evidence.graph import AGENT
from cinqflow.core.identity.merge import DemographicComparison, MergePlan
from cinqflow.core.model.governed import Actor
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError


@dataclass(frozen=True)
class EvidenceCard:
    """What a steward reviews before approving or declining a merge.

    `narrative`/`grounded_fields` are empty together, never one without the
    other — see `_narrate`'s degraded-path return.
    """

    merged_away_member_id: str
    survivor_member_id: str
    comparison: DemographicComparison
    plan: MergePlan
    narrative: str
    grounded_fields: tuple[str, ...]
    model_called: bool


@dataclass
class MergeEvidenceAgent:
    """One gateway. No credentials of its own, no store — this agent writes
    nothing, so it needs nothing to write to."""

    llm: LlmGateway

    def prepare(
        self,
        *,
        plan: MergePlan,
        comparison: DemographicComparison,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> EvidenceCard:
        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)
        bundle = self._gather(plan, comparison)
        narrative, grounded, called = self._narrate(
            bundle, comparison=comparison, caller=caller, run_id=run, stamp=stamp
        )
        return EvidenceCard(
            merged_away_member_id=plan.merged_away_member_id,
            survivor_member_id=plan.survivor_member_id,
            comparison=comparison,
            plan=plan,
            narrative=narrative,
            grounded_fields=grounded,
            model_called=called,
        )

    # ── node 1 · gather (NO MODEL) ────────────────────────────────────────────

    def _gather(self, plan: MergePlan, comparison: DemographicComparison) -> str:
        """Deterministic. Renders exactly what core already computed — never
        a raw demographic value, only the categorical comparison and the
        plan's shape."""
        lines = [f"{field}: {result.value}" for field, result in sorted(comparison.fields.items())]
        lines.append(f"repoints: {len(plan.repoints)}")
        lines.append(f"collapses: {len(plan.collapses)}")
        return "\n".join(lines)

    # ── node 2 · narrate (small) ──────────────────────────────────────────────

    def _narrate(
        self,
        bundle: str,
        *,
        comparison: DemographicComparison,
        caller: Actor,
        run_id: str,
        stamp: datetime,
    ) -> tuple[str, tuple[str, ...], bool]:
        try:
            result = self.llm.complete(
                agent=AGENT,
                run_id=run_id,
                prompt_id="merge-evidence.narrate",
                caller=caller,
                input_text=bundle,
            )
        except ManualPathRequiredError:
            return "", (), False

        raw = result.value if isinstance(result.value, dict) else {}
        narrative = str(raw.get("narrative", ""))
        # The platform decides what counts as grounded: a field the model
        # named that `_gather` never handed it is discarded, same rule
        # `schema_inference`/`mapping_suggestion` apply to an invented name.
        grounded = tuple(
            field for field in raw.get("grounded_fields", ()) if field in comparison.fields
        )
        return narrative, grounded, True
