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

from typing import Any, Protocol, runtime_checkable

from cinqflow.core.model.llm import (
    BudgetExhaustedError,
    Completion,
    CompletionFailedError,
    Embedding,
    LlmError,
    TaskClass,
    UndeclaredEndpointError,
)

__all__ = [
    "BudgetExhaustedError",
    "Completion",
    "CompletionFailedError",
    "Embedding",
    "LlmError",
    "LlmPort",
    "TaskClass",
    "UndeclaredEndpointError",
]


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
