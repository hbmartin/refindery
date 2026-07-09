"""LLM extractor offset-location tests."""

import json

from refindery.adapters.extractors.llm import LlmExtractor
from refindery.adapters.llm.openai_compat import OpenAiCompatClient


class _StubClient(OpenAiCompatClient):
    def __init__(self, response: str) -> None:
        super().__init__(base_url="http://localhost:9", model="stub")
        self._response = response

    async def complete(
        self,
        prompt: str,  # noqa: ARG002 — canned response
        *,
        max_tokens: int = 200,  # noqa: ARG002
    ) -> str:
        return self._response


async def test_duplicate_surface_forms_locate_distinct_offsets():
    text = "Rust is great. I love Rust. Rust forever."
    payload = json.dumps([{"surface_form": "Rust", "type": "technology"}] * 4)
    extractor = LlmExtractor(_StubClient(payload))

    mentions = await extractor.extract(text)
    await extractor.aclose()

    # Three occurrences exist; the fourth reported repeat is dropped instead
    # of double-counting an already-used offset.
    assert [mention.char_start for mention in mentions] == [0, 22, 28]
    assert all(
        text[mention.char_start : mention.char_end] == "Rust" for mention in mentions
    )
