"""A network blip must not fail an interpretation the analyst then retries by
hand: transport-level errors are retried with real waits; anything else is not."""

from __future__ import annotations

import httpx
import openai
import pytest

from cinqflow.intelligence.llm import (
    TRANSIENT_BACKOFF_SECONDS,
    LlmError,
    OpenAIClient,
    retry_transient,
)
from cinqflow.settings import Settings

TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/x"))


def test_a_transient_error_is_retried_with_backoff_then_succeeds():
    calls: list[int] = []
    waits: list[float] = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise _connection_error()
        return {"ok": True}

    result = retry_transient(
        call, provider="openai", transient=TRANSIENT, backoff=(2, 6, 15), sleep=waits.append
    )
    assert result == {"ok": True}
    assert len(calls) == 3
    assert waits == [2, 6]  # waited twice, succeeded on the third attempt


def test_exhausted_retries_raise_one_error_naming_provider_attempts_and_cause():
    waits: list[float] = []

    def call():
        raise _connection_error()

    with pytest.raises(LlmError) as info:
        retry_transient(
            call, provider="openai", transient=TRANSIENT, backoff=(2, 6, 15), sleep=waits.append
        )
    message = str(info.value)
    assert message.startswith("openai unreachable after 4 attempts")
    assert "APIConnectionError: Connection error." in message
    assert waits == [2, 6, 15]


def test_non_transient_errors_propagate_immediately():
    calls: list[int] = []
    waits: list[float] = []

    def call():
        calls.append(1)
        raise ValueError("bad request shape")

    with pytest.raises(ValueError):
        retry_transient(
            call, provider="openai", transient=TRANSIENT, backoff=(2, 6), sleep=waits.append
        )
    assert calls == [1] and waits == []


def test_zero_retries_means_one_attempt():
    def call():
        raise _connection_error()

    with pytest.raises(LlmError, match="after 1 attempts"):
        retry_transient(
            call, provider="openai", transient=TRANSIENT, backoff=(), sleep=lambda _: None
        )


def test_client_backoff_follows_the_setting(tmp_path):
    client = OpenAIClient(
        Settings(
            landing_root=tmp_path,
            llm_provider="openai",
            llm_api_key="sk-test",
            llm_transient_retries=2,
        )
    )
    assert client._backoff == TRANSIENT_BACKOFF_SECONDS[:2]
    assert openai.APITimeoutError in client._transient or issubclass(
        openai.APITimeoutError, client._transient[0]
    )
