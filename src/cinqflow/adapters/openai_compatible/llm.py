"""openai-compatible — THE ONLY PLACE A MODEL CREDENTIAL EXISTS.

    "no model credentials exist outside the LLM gateway"
    "only endpoints declared in the connection profile may be called"
    — docs/architecture/INVARIANTS.md, intelligence

    verb: complete/embed/route   dev: REAL_subscription   target: azure_ai_foundry
    — docs/architecture/plates/04-pin-out-map.md

One SDK covers both verbs and both routing classes, and Azure AI Foundry serves
the same request shape — which is what makes rung 3 an endpoint and a
credential rather than a rewrite (CF-V4-E16-08 becomes a verification).

Two things are refused HERE rather than anywhere else, because this is the only
layer that can see them:

  • an endpoint the connection profile does not declare — refused in the
    CONSTRUCTOR, so an undeclared endpoint cannot be reached even once;
  • a price the profile does not state — refused rather than guessed, because
    a cost of zero would make every budget cap non-binding and every "cost per
    run" figure a lie.

The SDK is imported lazily. Lanes 1 and 2 hold no credentials and must not even
need the package installed; a module-level import would make the mock lane
depend on the vendor it exists to avoid.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from cinqflow.core.model.llm import (
    Completion,
    Embedding,
    LlmError,
    TaskClass,
    UndeclaredEndpointError,
)
from cinqflow.core.prompts import hash_prompt
from cinqflow.ports import port

#: Per 1M tokens, from the connection profile. There is no default: see above.
Prices = dict[str, tuple[Decimal, Decimal]]


@port("llm", "openai-compatible")
class OpenAiCompatibleLlm:
    """A transport. Routing, budgets, metering and audit belong to the gateway."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        models: dict[TaskClass, str],
        prices: Prices,
        declared_endpoints: frozenset[str],
        embedding_model: str = "",
        timeout_s: float = 60.0,
    ) -> None:
        if endpoint not in declared_endpoints:
            raise UndeclaredEndpointError(
                f"{endpoint!r} is not declared in the connection profile. Declared: "
                f"{', '.join(sorted(declared_endpoints)) or 'nothing'}. An endpoint is "
                "refused in the constructor so it cannot be reached even once."
            )
        if not api_key:
            raise LlmError(
                "no api key. The gateway is the only holder of a model credential, and a "
                "gateway without one must fail loudly rather than fall through to an "
                "unauthenticated endpoint."
            )
        missing_prices = {model for model in models.values() if model not in prices}
        if missing_prices:
            raise LlmError(
                f"no price declared for {', '.join(sorted(missing_prices))}. A cost of zero "
                "makes every budget cap non-binding and every cost figure a lie."
            )
        self._endpoint = endpoint
        self._api_key = api_key
        self._models = dict(models)
        self._prices = dict(prices)
        self._declared = declared_endpoints
        self._embedding_model = embedding_model
        self._timeout = timeout_s
        self._client: Any | None = None

    # ── the pin ──────────────────────────────────────────────────────────────

    def complete(
        self,
        *,
        prompt: str,
        task_class: TaskClass,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Completion:
        model = self._models[task_class]
        started = time.monotonic()
        response = self._sdk().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=max_tokens,
            temperature=temperature,
            timeout=self._timeout,
            **({"response_format": {"type": "json_object"}} if response_schema else {}),
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        choice = response.choices[0]
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0))
        completion_tokens = int(getattr(usage, "completion_tokens", 0))
        return Completion(
            text=choice.message.content or "",
            model=model,
            # Pinned, never floating: the served version is what the eval
            # threshold was measured against, and a floating version is an
            # unpinned experiment running in production.
            model_version=str(getattr(response, "model", model)),
            prompt_hash=hash_prompt(prompt),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=self._cost(model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            finish_reason=str(choice.finish_reason or "stop"),
        )

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        if not self._embedding_model:
            raise LlmError("no embedding model declared in the connection profile")
        response = self._sdk().embeddings.create(model=self._embedding_model, input=list(texts))
        prices = self._prices.get(self._embedding_model)
        if prices is None:
            raise LlmError(f"no price declared for {self._embedding_model}")
        tokens = int(getattr(response.usage, "prompt_tokens", 0))
        each = (prices[0] * Decimal(tokens) / Decimal(1_000_000)) / Decimal(max(len(texts), 1))
        return tuple(
            Embedding(
                vector=tuple(item.embedding),
                model=self._embedding_model,
                model_version=str(getattr(response, "model", self._embedding_model)),
                cost_usd=each,
            )
            for item in response.data
        )

    def declared_endpoints(self) -> frozenset[str]:
        return self._declared

    # ── internals ────────────────────────────────────────────────────────────

    def _cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        prompt_price, completion_price = self._prices[model]
        million = Decimal(1_000_000)
        return (
            prompt_price * Decimal(prompt_tokens) + completion_price * Decimal(completion_tokens)
        ) / million

    def _sdk(self) -> Any:
        """Imported lazily so Lanes 1 and 2 need neither the package nor a key."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self._endpoint, api_key=self._api_key, timeout=self._timeout
            )
        return self._client
