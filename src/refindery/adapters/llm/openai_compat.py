"""Minimal OpenAI-compatible chat-completions client.

Used for optional cosmetic work (cluster labels, LLM entity extraction).
The raw HTTP response is validated with pydantic before use. When a
circuit breaker / retry policy is supplied, transient HTTP failures are
retried and a provider outage fast-fails with ProviderUnavailableError.
"""

import httpx
from pydantic import BaseModel, ConfigDict

from refindery.adapters.resilience.circuit_breaker import CircuitBreaker
from refindery.adapters.resilience.retry import RetryPolicy
from refindery.adapters.resilience.wrappers import guarded_call, is_transient_http


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str


class _Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: _Message


class ChatResponse(BaseModel):
    """Validated /chat/completions response."""

    model_config = ConfigDict(extra="ignore")

    choices: list[_Choice]


class OpenAiCompatClient:
    """POST /chat/completions against any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        breaker: CircuitBreaker | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._breaker = breaker
        self._retry = retry
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        """One-shot completion; returns the first choice's content."""
        if self._breaker is None and self._retry is None:
            return await self._complete(prompt, max_tokens=max_tokens)
        return await guarded_call(
            lambda: self._complete(prompt, max_tokens=max_tokens),
            breaker=self._breaker,
            policy=self._retry
            or RetryPolicy(attempts=1, base_delay_s=0.001, max_delay_s=0.001),
            timeout_s=self._timeout_s,
            retryable=is_transient_http,
        )

    async def _complete(self, prompt: str, *, max_tokens: int) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            headers=headers,
        )
        response.raise_for_status()
        parsed = ChatResponse.model_validate(response.json())
        if not parsed.choices:
            msg = "chat completion returned no choices"
            raise RuntimeError(msg)
        return parsed.choices[0].message.content.strip()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
