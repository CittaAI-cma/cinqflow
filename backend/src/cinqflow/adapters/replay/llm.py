"""cassette — LANE 2. Recorded responses, replayed at the PORT boundary.

    "test lanes: 1 mock · 2 replay · 3 real API"
    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2 (replay)"
    — docs/architecture/INVARIANTS.md, testing

The recording boundary is the decision that matters. Recording HTTP would tie
every cassette to one SDK's request shape, and an SDK bump would invalidate a
library that took real money to record. Recording at the PORT means a cassette
is `(prompt_hash, model, version, params) -> response` — the platform's own
vocabulary — so cassettes survive SDK upgrades and can be DIFFED at the Wave-4
re-baseline to show what a hosting change actually did.

A miss is an error, never a live call. A cassette lane that can fall through to
the network is a lane that holds a credential, and Lanes 1 and 2 hold none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from cinqflow.core.model.llm import Completion, Embedding, LlmError, TaskClass
from cinqflow.ports import port


class CassetteMissError(LlmError):
    """No recording for this call.

    Loud on purpose, and it names the key: the fix is to re-record in Lane 3,
    which is a deliberate act with a cost, not something a test run should do
    on its own.
    """


@dataclass(frozen=True)
class CassetteKey:
    """What identifies a recorded exchange. Deliberately not the prompt text —
    the hash is shorter, stable, and already what the audit log carries."""

    prompt_hash: str
    model: str
    model_version: str
    max_tokens: int
    temperature: float

    def as_id(self) -> str:
        return (
            f"{self.prompt_hash}.{self.model}.{self.model_version}"
            f".{self.max_tokens}.{self.temperature}"
        )


@port("llm", "cassette")
class CassetteLlm:
    """Replays a recorded library. Holds no credential and opens no socket."""

    def __init__(
        self,
        directory: str | Path,
        *,
        models: dict[TaskClass, str],
        model_version: str = "recorded",
    ) -> None:
        self._directory = Path(directory)
        self._models = dict(models)
        self._model_version = model_version
        self.misses: list[CassetteKey] = []

    def complete(
        self,
        *,
        prompt: str,
        task_class: TaskClass,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Completion:
        from cinqflow.core.prompts import hash_prompt

        key = CassetteKey(
            prompt_hash=hash_prompt(prompt),
            model=self._models[task_class],
            model_version=self._model_version,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        path = self._directory / f"{key.as_id()}.json"
        if not path.exists():
            self.misses.append(key)
            raise CassetteMissError(
                f"no cassette for {key.as_id()}. Lane 2 never falls through to a live "
                "call — re-record in Lane 3 deliberately."
            )
        recorded = json.loads(path.read_text(encoding="utf-8"))
        return Completion(
            text=recorded["text"],
            model=key.model,
            model_version=key.model_version,
            prompt_hash=key.prompt_hash,
            prompt_tokens=int(recorded.get("prompt_tokens", 0)),
            completion_tokens=int(recorded.get("completion_tokens", 0)),
            # The cost that was REALLY paid when this was recorded. Zeroing it
            # would make Lane 2 quietly disable every budget test.
            cost_usd=Decimal(str(recorded.get("cost_usd", "0"))),
            latency_ms=int(recorded.get("latency_ms", 0)),
            finish_reason=str(recorded.get("finish_reason", "stop")),
        )

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        raise CassetteMissError(
            "embeddings are not recorded in Wave 0 — the vector store is provisioned and EMPTY "
            "until the governed knowledge pipeline lands in Wave 1"
        )

    def declared_endpoints(self) -> frozenset[str]:
        return frozenset({f"cassette://{self._directory}"})

    def record(self, key: CassetteKey, completion: Completion) -> Path:
        """Write a cassette. Called only from a Lane-3 recording run."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{key.as_id()}.json"
        path.write_text(
            json.dumps(
                {
                    "text": completion.text,
                    "prompt_tokens": completion.prompt_tokens,
                    "completion_tokens": completion.completion_tokens,
                    "cost_usd": str(completion.cost_usd),
                    "latency_ms": completion.latency_ms,
                    "finish_reason": completion.finish_reason,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
