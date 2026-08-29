"""What a model call is made of, and what it costs. Value types, not verbs.

    "every model call is logged with prompt hash, model version, cost and
     caller identity"
    — docs/architecture/INVARIANTS.md, intelligence

`Completion` carries everything metering and audit need, as part of the RETURN
VALUE rather than as something the caller is trusted to record separately. It
lives in core because the prompt registry, the budget and the gateway's own
records all reason about it — and none of them should have to import a pin to
talk about a completion.

`ports/llm.py` re-exports every name here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique
from typing import Any


@unique
class TaskClass(StrEnum):
    """Routing is by TASK, from a table in the connection profile.

    Not by model name at the call site: a call site naming a model is a call
    site that has to be edited when the tenant's model catalogue differs.
    """

    SMALL = "small"  # classify · extract · rerank
    LARGE = "large"  # generate · reason


@dataclass(frozen=True)
class Completion:
    """One model response, with everything metering and audit need.

    Every field here is required by "100% of model calls carry prompt hash,
    model version, cost and caller identity in the audit log" — so they are
    part of the return value rather than something the caller is trusted to
    record separately.
    """

    text: str
    model: str
    model_version: str
    prompt_hash: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Embedding:
    vector: tuple[float, ...]
    model: str
    model_version: str
    cost_usd: Decimal


class LlmError(RuntimeError):
    """The model call could not be made or could not be trusted."""


class UndeclaredEndpointError(LlmError):
    """An endpoint not named in the connection profile.

    Refused AT THE ADAPTER, which is the only place that can see the endpoint
    at all. A policy document cannot enforce this; a constructor can.
    """


class BudgetExhaustedError(LlmError):
    """The agent's daily budget is spent.

    "the gateway refuses with a clear status, the feature degrades to its
     manual path, and Operations sees the budget event — never a silent
     hang or a surprise bill."
    — CF-V0-E16-01, exception
    """
