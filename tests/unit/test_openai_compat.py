"""OpenAiCompatClient tests over a mocked httpx transport (pytest-httpx)."""

import httpx
import pytest
from pydantic import ValidationError

from refindery.adapters.llm.openai_compat import OpenAiCompatClient
from refindery.adapters.resilience.circuit_breaker import (
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
)
from refindery.adapters.resilience.retry import RetryPolicy
from refindery.domain.errors import ProviderUnavailableError
from tests.fakes.clock import FakeClock

BASE_URL = "http://llm.test/v1"
COMPLETIONS = f"{BASE_URL}/chat/completions"
OK_BODY = {"choices": [{"message": {"content": "  labelled  "}}]}


def _client(**kwargs) -> OpenAiCompatClient:
    return OpenAiCompatClient(base_url=BASE_URL, model="test-model", **kwargs)


async def test_complete_happy_path_sends_auth(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, json=OK_BODY)
    client = _client(api_key="sekret")
    try:
        assert await client.complete("label this") == "labelled"
    finally:
        await client.aclose()
    request = httpx_mock.get_request()
    assert request.headers["authorization"] == "Bearer sekret"
    assert request.url == COMPLETIONS


async def test_empty_choices_raises(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, json={"choices": []})
    client = _client()
    try:
        with pytest.raises(RuntimeError, match="no choices"):
            await client.complete("p")
    finally:
        await client.aclose()


async def test_malformed_payload_raises_validation_error(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, json={"choices": [{"message": {}}]})
    client = _client()
    try:
        with pytest.raises(ValidationError):
            await client.complete("p")
    finally:
        await client.aclose()


async def test_http_error_propagates_without_breaker(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, status_code=500)
    client = _client()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("p")
    finally:
        await client.aclose()


async def test_retries_transient_error_then_succeeds(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, status_code=500)
    httpx_mock.add_response(url=COMPLETIONS, json=OK_BODY)
    client = _client(
        breaker=CircuitBreaker(
            name="llm:test",
            config=BreakerConfig(failure_threshold=5, cooldown_s=30.0),
            clock=FakeClock(),
        ),
        retry=RetryPolicy(attempts=2, base_delay_s=0.001, max_delay_s=0.002),
    )
    try:
        assert await client.complete("p") == "labelled"
    finally:
        await client.aclose()


async def test_retries_rate_limit_then_succeeds(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, status_code=429)
    httpx_mock.add_response(url=COMPLETIONS, json=OK_BODY)
    client = _client(
        retry=RetryPolicy(attempts=2, base_delay_s=0.001, max_delay_s=0.002)
    )
    try:
        assert await client.complete("p") == "labelled"
    finally:
        await client.aclose()


async def test_transport_timeout_retries_then_exhausts(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"), is_reusable=True)
    client = _client(
        retry=RetryPolicy(attempts=2, base_delay_s=0.001, max_delay_s=0.002)
    )
    try:
        with pytest.raises(httpx.ReadTimeout, match="timed out"):
            await client.complete("p")
    finally:
        await client.aclose()
    assert len(httpx_mock.get_requests()) == 2


async def test_non_retryable_client_error_is_attempted_once(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, status_code=400)
    client = _client(
        retry=RetryPolicy(attempts=3, base_delay_s=0.001, max_delay_s=0.002)
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("p")
    finally:
        await client.aclose()
    assert len(httpx_mock.get_requests()) == 1


async def test_breaker_opens_and_fast_fails(httpx_mock):
    httpx_mock.add_response(url=COMPLETIONS, status_code=500)
    httpx_mock.add_response(url=COMPLETIONS, status_code=500)
    breaker = CircuitBreaker(
        name="llm:test",
        config=BreakerConfig(failure_threshold=2, cooldown_s=30.0),
        clock=FakeClock(),
    )
    client = _client(
        breaker=breaker,
        retry=RetryPolicy(attempts=2, base_delay_s=0.001, max_delay_s=0.002),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete("p")  # two failed attempts -> breaker opens
        assert breaker.state is BreakerState.OPEN
        with pytest.raises(ProviderUnavailableError):
            await client.complete("p")  # fast-fail, no HTTP request
    finally:
        await client.aclose()
    assert len(httpx_mock.get_requests()) == 2
