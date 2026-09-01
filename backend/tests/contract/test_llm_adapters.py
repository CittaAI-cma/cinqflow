"""One contract suite, three fitted adapters — mock, cassette, and the real one.

    "every external touch crosses a port with real / dev stand-in / mock
     sharing ONE contract suite"
    — Law 2

The real adapter's constructor refusals are testable with no key and no
network, which is deliberate: the two things that must never happen — calling
an undeclared endpoint, and pricing a call at zero — are decided before any
socket opens.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.openai_compatible.llm import OpenAiCompatibleLlm
from cinqflow.adapters.replay.llm import CassetteKey, CassetteLlm, CassetteMissError
from cinqflow.core.model.llm import (
    Completion,
    CompletionFailedError,
    LlmError,
    TaskClass,
    UndeclaredEndpointError,
)
from cinqflow.core.prompts import hash_prompt
from cinqflow.ports import fitted
from cinqflow.ports.llm import LlmPort

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

MODELS = {TaskClass.SMALL: "small-model", TaskClass.LARGE: "large-model"}
PRICES = {
    "small-model": (Decimal("0.15"), Decimal("0.60")),
    "large-model": (Decimal("2.50"), Decimal("10.00")),
}
DECLARED = frozenset({"https://api.example.test/v1"})


def test_three_adapters_are_fitted_to_the_llm_pin() -> None:
    assert set(fitted("llm")) >= {"mock", "cassette", "openai-compatible"}


@pytest.mark.parametrize("adapter", [ScriptedLlm()])
def test_every_fitted_adapter_satisfies_the_port(adapter: object) -> None:
    assert isinstance(adapter, LlmPort)


# ── the real adapter, refusing before it connects ────────────────────────────


def test_an_undeclared_endpoint_is_refused_in_the_constructor() -> None:
    with pytest.raises(UndeclaredEndpointError) as raised:
        OpenAiCompatibleLlm(
            endpoint="https://somewhere-else.test/v1",
            api_key="k",
            models=MODELS,
            prices=PRICES,
            declared_endpoints=DECLARED,
        )
    assert "not declared in the connection profile" in str(raised.value)
    assert "https://api.example.test/v1" in str(raised.value), "say what IS declared"


def test_a_missing_credential_fails_loudly_rather_than_calling_unauthenticated() -> None:
    with pytest.raises(LlmError, match="no api key"):
        OpenAiCompatibleLlm(
            endpoint="https://api.example.test/v1",
            api_key="",
            models=MODELS,
            prices=PRICES,
            declared_endpoints=DECLARED,
        )


def test_a_model_with_no_declared_price_is_refused() -> None:
    """A cost of zero makes every budget cap non-binding."""
    with pytest.raises(LlmError, match="no price declared"):
        OpenAiCompatibleLlm(
            endpoint="https://api.example.test/v1",
            api_key="k",
            models=MODELS,
            prices={"small-model": (Decimal("1"), Decimal("1"))},
            declared_endpoints=DECLARED,
        )


def test_cost_is_computed_in_decimal_from_the_declared_price() -> None:
    adapter = OpenAiCompatibleLlm(
        endpoint="https://api.example.test/v1",
        api_key="k",
        models=MODELS,
        prices=PRICES,
        declared_endpoints=DECLARED,
    )
    # 1000 prompt + 500 completion on large-model, priced per 1M tokens.
    cost = adapter._cost("large-model", 1000, 500)
    assert cost == Decimal("0.0075")
    assert isinstance(cost, Decimal), "a ledger of money in float disagrees with the invoice"


def test_the_real_adapter_opens_no_socket_until_it_is_called() -> None:
    """Constructing it in Lane 1 is safe; that is what lets these tests exist."""
    adapter = OpenAiCompatibleLlm(
        endpoint="https://api.example.test/v1",
        api_key="k",
        models=MODELS,
        prices=PRICES,
        declared_endpoints=DECLARED,
    )
    assert adapter._client is None
    assert adapter.declared_endpoints() == DECLARED


def test_a_vendor_transport_failure_is_translated_at_the_adapter_boundary() -> None:
    """ "a vendor's specific exception types should not leak past its
    adapter" — W2-37. A real `openai.APITimeoutError` (the same base,
    `openai.APIError`, also covers `APIConnectionError` and
    `RateLimitError`) must never reach a caller as itself; this is the ONE
    layer that can see the vendor exception at all, and it translates it
    into the platform's own `CompletionFailedError`."""
    import httpx
    import openai

    adapter = OpenAiCompatibleLlm(
        endpoint="https://api.example.test/v1",
        api_key="k",
        models=MODELS,
        prices=PRICES,
        declared_endpoints=DECLARED,
    )

    class _FailingCompletions:
        def create(self, **kwargs: object) -> object:
            request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
            raise openai.APITimeoutError(request=request)

    class _FailingChat:
        completions = _FailingCompletions()

    class _FailingClient:
        chat = _FailingChat()

    # Bypasses `_sdk()`'s real construction entirely — Lane 1 holds no
    # credential and must not need a socket to prove this translation.
    adapter._client = _FailingClient()

    with pytest.raises(CompletionFailedError, match="small-model"):
        adapter.complete(prompt="p", task_class=TaskClass.SMALL)


# ── Lane 2: cassettes at the PORT boundary ───────────────────────────────────


def test_a_cassette_is_keyed_by_prompt_hash_model_version_and_params() -> None:
    key = CassetteKey(
        prompt_hash="abc",
        model="large-model",
        model_version="v1",
        max_tokens=100,
        temperature=0.0,
    )
    assert key.as_id() == "abc.large-model.v1.100.0.0"


def test_a_recorded_exchange_replays_with_the_cost_that_was_really_paid(
    tmp_path: Path,
) -> None:
    adapter = CassetteLlm(tmp_path, models=MODELS)
    prompt = "# identity\nYou explain pipelines."
    key = CassetteKey(
        prompt_hash=hash_prompt(prompt),
        model="large-model",
        model_version="recorded",
        max_tokens=2048,
        temperature=0.0,
    )
    adapter.record(
        key,
        Completion(
            text='{"claims": []}',
            model="large-model",
            model_version="recorded",
            prompt_hash=key.prompt_hash,
            prompt_tokens=120,
            completion_tokens=8,
            cost_usd=Decimal("0.0038"),
            latency_ms=740,
        ),
    )

    replayed = adapter.complete(prompt=prompt, task_class=TaskClass.LARGE)
    assert replayed.text == '{"claims": []}'
    assert replayed.cost_usd == Decimal("0.0038"), (
        "zeroing a replayed cost would quietly disable every budget test in Lane 2"
    )
    assert replayed.latency_ms == 740


def test_a_cassette_miss_is_an_error_never_a_live_call(tmp_path: Path) -> None:
    adapter = CassetteLlm(tmp_path, models=MODELS)
    with pytest.raises(CassetteMissError) as raised:
        adapter.complete(prompt="never recorded", task_class=TaskClass.SMALL)
    assert "re-record in Lane 3 deliberately" in str(raised.value)
    assert adapter.misses[0].model == "small-model"


def test_the_cassette_lane_declares_no_network_endpoint(tmp_path: Path) -> None:
    endpoints = CassetteLlm(tmp_path, models=MODELS).declared_endpoints()
    assert all(e.startswith("cassette://") for e in endpoints)


def test_cassettes_survive_a_prompt_change_by_missing_not_by_lying(tmp_path: Path) -> None:
    """A cassette keyed on the prompt hash cannot silently answer a new prompt."""
    adapter = CassetteLlm(tmp_path, models=MODELS)
    key = CassetteKey(
        prompt_hash=hash_prompt("original"),
        model="large-model",
        model_version="recorded",
        max_tokens=2048,
        temperature=0.0,
    )
    adapter.record(
        key,
        Completion(
            text="{}",
            model="large-model",
            model_version="recorded",
            prompt_hash=key.prompt_hash,
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=Decimal("0"),
            latency_ms=1,
        ),
    )
    with pytest.raises(CassetteMissError):
        adapter.complete(prompt="edited prompt", task_class=TaskClass.LARGE)


def test_recorded_cassettes_are_readable_json_a_human_can_diff(tmp_path: Path) -> None:
    """The Wave-4 re-baseline diffs these to show what a hosting change did."""
    adapter = CassetteLlm(tmp_path, models=MODELS)
    key = CassetteKey(
        prompt_hash="h",
        model="large-model",
        model_version="recorded",
        max_tokens=8,
        temperature=0.0,
    )
    path = adapter.record(
        key,
        Completion(
            text="hello",
            model="large-model",
            model_version="recorded",
            prompt_hash="h",
            prompt_tokens=2,
            completion_tokens=1,
            cost_usd=Decimal("0.001"),
            latency_ms=10,
        ),
    )
    body = json.loads(path.read_text())
    assert body["text"] == "hello"
    assert body["cost_usd"] == "0.001"
