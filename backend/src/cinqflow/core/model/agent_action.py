"""One row of `audit.agent_action` — what an agent did, and what it was refused.

    "100% of model calls carry prompt hash, model version, cost and caller
     identity in the audit log"
    "every tool invocation is written to audit.agent_action"
    — docs/architecture/INVARIANTS.md, intelligence

The refusals are recorded on the same path as the successes, and that is the
half people leave out. A ledger holding only what the agent was allowed to do
cannot answer "what did it try?", which is the question an AI review actually
asks — and it is the only evidence that the R0 whitelist ever bound anything.

`outcome` is a closed vocabulary for the same reason the seven status words
are: two spellings of "refused" make the refusal rate unqueryable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType


@unique
class ActionOutcome(StrEnum):
    COMPLETED = "completed"
    REFUSED_BUDGET = "refused_budget"
    REFUSED_PERMISSION = "refused_permission"
    REFUSED_NOT_WHITELISTED = "refused_not_whitelisted"
    REFUSED_UNDECLARED_ENDPOINT = "refused_undeclared_endpoint"
    #: CF-V1-W1-25. A knowledge chunk tripped `PhiScrubPort.detect` and was
    #: refused before it ever reached `LlmGateway.embed` — never masked and
    #: embedded anyway. The knowledge plane's own quarantine record: what a
    #: quarantined data row does for a rule, this does for a chunk.
    REFUSED_PHI = "refused_phi"
    FAILED_SCHEMA = "failed_schema"
    FAILED_COMPLETION = "failed_completion"
    ESCALATED_TO_MANUAL = "escalated_to_manual"

    @property
    def is_refusal(self) -> bool:
        return self.name.startswith("REFUSED")


@dataclass(frozen=True)
class AgentAction:
    """Immutable, append-only. There is no update path and no delete path.

    `cost_usd` is Decimal. A ledger of money in binary floating point is a
    ledger that disagrees with the invoice.
    """

    run_id: str
    agent: str
    action: str
    outcome: ActionOutcome
    actor: Actor
    occurred_ts: datetime
    risk_class: str = "R0"
    prompt_ref: str = ""
    prompt_hash: str = ""
    model: str = ""
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0
    detail: str = ""

    @property
    def actor_type(self) -> ActorType:
        """Recorded, never inferred. An AI action that reads as human defeats
        the entire trail."""
        return self.actor.actor_type

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("an agent action without a run is unattributable")
        if self.outcome is ActionOutcome.COMPLETED and self.action.startswith("llm:"):
            missing = [
                name
                for name, value in (
                    ("prompt_hash", self.prompt_hash),
                    ("model", self.model),
                    ("model_version", self.model_version),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"a completed model call recorded without {', '.join(missing)}. "
                    "100% of model calls carry prompt hash, model version, cost and caller."
                )
