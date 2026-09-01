"""CF-V0-E16-01 — the call pipeline, the budget, and the routing table.

    "context_assembly -> phi_scrub -> prompt_assembly -> llm_gateway ->
     schema_validation -> action_gateway"
    — docs/architecture/plates, the six-stage call pipeline

    "PHI is scrubbed before ANY prompt; the scrub-then-prompt ordering has its
     own test"
    "only endpoints declared in the connection profile may be called"
    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2 (replay)"
    — docs/architecture/INVARIANTS.md, intelligence

The stages are declared here as DATA so that "scrub happens before prompt
assembly" is a fact about an ordered tuple, checkable without running anything,
and the gateway's own test can assert it independently of what either component
does. An ordering that only exists as the sequence of statements in a function
body is an ordering that dies to a refactor and takes a PHI guarantee with it.

Budget arithmetic is pure and lives here too. Money is Decimal, never float:
a per-run cap compared with binary floating point is a cap that is sometimes
0.2500000000000001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum, unique

from cinqflow.core.model.llm import BudgetExhaustedError, TaskClass


@unique
class CallStage(IntEnum):
    """The six stages, in order. The integer value IS the order.

    `IntEnum` rather than `StrEnum` on purpose: `CallStage.PHI_SCRUB <
    CallStage.PROMPT_ASSEMBLY` is then a comparison anyone can write, and the
    ordering test reads as the sentence it is asserting.
    """

    CONTEXT_ASSEMBLY = 1
    PHI_SCRUB = 2
    PROMPT_ASSEMBLY = 3
    LLM_GATEWAY = 4
    SCHEMA_VALIDATION = 5
    ACTION_GATEWAY = 6

    @property
    def label(self) -> str:
        return self.name.lower()


CALL_PIPELINE: tuple[CallStage, ...] = tuple(sorted(CallStage))


class PipelineOrderError(RuntimeError):
    """A stage ran out of order.

    Raised, never warned. A prompt assembled before the scrub is a PHI
    disclosure that already happened; there is nothing to degrade to.
    """


@unique
class RiskClass(StrEnum):
    """Re-exported shorthand for the classes the gateway enforces.

    Wave 0's agent is R0 and only R0 — read-only, no write tool on the
    whitelist at any confidence. R4 is human-always and is not configurable,
    which is why `at_confidence` in `core/model/vocabulary` ignores its
    argument.
    """

    R0 = "R0"


@dataclass(frozen=True)
class Budget:
    """What one agent may spend. Enforced before the call, not after.

        "the gateway refuses with a clear status, the feature degrades to its
         manual path, and Operations sees the budget event — never a silent
         hang or a surprise bill."
        — CF-V0-E16-01, exception

    Checked BEFORE the call because a budget checked afterwards is a report,
    not a control.
    """

    per_run_usd: Decimal
    per_agent_per_day_usd: Decimal

    def __post_init__(self) -> None:
        if self.per_run_usd <= 0 or self.per_agent_per_day_usd <= 0:
            raise ValueError("a budget of zero is a disabled feature, not a budget")
        if self.per_run_usd > self.per_agent_per_day_usd:
            raise ValueError(
                f"per-run cap ${self.per_run_usd} exceeds the daily cap "
                f"${self.per_agent_per_day_usd} — the daily cap would never bind"
            )

    def check(
        self, *, agent: str, spent_today: Decimal, spent_this_run: Decimal, estimated: Decimal
    ) -> None:
        """Refuse before spending, and say which cap bound and by how much."""
        if spent_this_run + estimated > self.per_run_usd:
            raise BudgetExhaustedError(
                f"{agent}: this run would reach ${spent_this_run + estimated} against a "
                f"per-run cap of ${self.per_run_usd}. Refused before the call."
            )
        if spent_today + estimated > self.per_agent_per_day_usd:
            raise BudgetExhaustedError(
                f"{agent}: today would reach ${spent_today + estimated} against a daily cap "
                f"of ${self.per_agent_per_day_usd}. Refused before the call — the manual "
                "path is unaffected."
            )


@dataclass(frozen=True)
class Routing:
    """Task class -> model name, from the connection profile.

    A call site never names a model. It names a TASK CLASS, and the tenant's
    catalogue is a profile line — which is the whole reason rung 3 is a profile
    change rather than a code change.
    """

    small: str
    large: str
    pin_versions: bool = True

    def model_for(self, task_class: TaskClass) -> str:
        return self.small if task_class is TaskClass.SMALL else self.large
