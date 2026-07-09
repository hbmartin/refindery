"""Minimal OpenAI-compatible chat-completions client.

Used for optional cosmetic work (cluster labels, LLM entity extraction).
The raw HTTP response is validated with pydantic before use.
"""

import httpx
from pydantic import BaseModel, ConfigDict


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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        """One-shot completion; returns the first choice's content."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
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
