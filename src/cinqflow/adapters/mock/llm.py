"""scripted — a deterministic model. THE LANE-1 RUNNER.

    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2 (replay)"
    — docs/architecture/INVARIANTS.md, testing

This adapter proves MACHINERY: that the graph runs, that routing picks a class,
that a schema-invalid response is rejected and retried once, that budgets
refuse, that the manual path is reachable. It proves NOTHING about quality, and
it holds no credential so it cannot accidentally start to.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from cinqflow.ports import port
from cinqflow.ports.llm import Completion, Embedding, TaskClass, UndeclaredEndpointError

Responder = Callable[[str, TaskClass], str]


@port("llm", "mock")
class ScriptedLlm:
    """Deterministic responses, keyed by prompt hash or supplied by a script.

    Determinism is the whole value: a machinery test that sometimes passes is
    worse than no machinery test, because it teaches the team to re-run CI.
    """

    def __init__(
        self,
        responder: Responder | None = None,
        *,
        scripted: dict[str, str] | None = None,
        declared_endpoints: frozenset[str] = frozenset({"mock://scripted"}),
    ) -> None:
        self._responder = responder
        self._scripted = dict(scripted or {})
        self._declared = declared_endpoints
        self.calls: list[tuple[str, TaskClass]] = []

    def complete(
        self,
        *,
        prompt: str,
        task_class: TaskClass,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Completion:
        _ = (max_tokens, temperature)
        self.calls.append((prompt, task_class))
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:32]

        if prompt_hash in self._scripted:
            text = self._scripted[prompt_hash]
        elif self._responder is not None:
            text = self._responder(prompt, task_class)
        elif response_schema is not None:
            # Shape-valid but content-free. A mock that invented plausible
            # content would let an ungrounded answer pass a machinery test.
            text = json.dumps(_empty_for(response_schema))
        else:
            text = ""

        return Completion(
            text=text,
            model=f"mock-{task_class.value}",
            model_version="scripted-1",
            prompt_hash=prompt_hash,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            cost_usd=Decimal("0"),  # a mock costs nothing, and must not pretend to
            latency_ms=0,
        )

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        return tuple(
            Embedding(
                vector=_deterministic_vector(text),
                model="mock-embed",
                model_version="scripted-1",
                cost_usd=Decimal("0"),
            )
            for text in texts
        )

    def declared_endpoints(self) -> frozenset[str]:
        return self._declared

    def check_endpoint(self, endpoint: str) -> None:
        if endpoint not in self._declared:
            raise UndeclaredEndpointError(f"{endpoint!r} is not declared in the connection profile")


def _deterministic_vector(text: str, dimensions: int = 8) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode()).digest()
    return tuple(digest[i] / 255.0 for i in range(dimensions))


def _empty_for(schema: dict[str, Any]) -> Any:
    match schema.get("type"):
        case "object":
            return {k: _empty_for(v) for k, v in schema.get("properties", {}).items()}
        case "array":
            return []
        case "string":
            return ""
        case "number" | "integer":
            return 0
        case "boolean":
            return False
        case _:
            return None
