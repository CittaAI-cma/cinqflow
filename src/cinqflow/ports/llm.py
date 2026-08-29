"""The `llm` pin — complete, embed and route.

    verb: complete/embed/route   mock: scripted+replay   dev: REAL_subscription
    target: azure_ai_foundry
    — docs/architecture/plates/04-pin-out-map.md

    "no model credentials exist outside the LLM gateway"
    "every model call is logged with prompt hash, model version, cost and
     caller identity"
    "only endpoints declared in the connection profile may be called"
    — docs/architecture/INVARIANTS.md, intelligence

This pin is a TRANSPORT. Routing, budgets, metering, audit, prompt-hash logging
and refusal of undeclared endpoints are the platform's own governed surface and
live in core/ and the gateway — not in an SDK and not in a framework.

That distinction is what keeps CF-V0-E16-01's guardrail true by construction:
"code attempts a direct model call around the gateway -> it fails by
construction — no model credentials exist outside the gateway". Credentials
reach only the adapter behind this pin, and the adapter is only reachable
through the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class LlmPort(Protocol):
    def complete(
        self,
        *,
        prompt: str,
        task_class: TaskClass,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Completion:
        """One completion.

        `prompt` arrives ALREADY assembled by the prompt registry and ALREADY
        scrubbed — this port never assembles and never scrubs, because a port
        that could do either would become a second place prompts live.
        """
        ...

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        """Embeddings. Provisioned in Wave 0; the vector store stays EMPTY
        until the governed knowledge pipeline lands in Wave 1."""
        ...

    def declared_endpoints(self) -> frozenset[str]:
        """What the profile permits. The conformance kit asserts an adapter
        refuses everything outside this set."""
        ...
