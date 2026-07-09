"""LLM entity extractor (user-configurable OpenAI-compatible endpoint).

Strict JSON output validated with pydantic; offsets are verified against the
text and re-located with ``str.find`` when the model miscounts. Mentions
whose surface form cannot be located are dropped.
"""

import json
import logging

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from refindery.adapters.llm.openai_compat import OpenAiCompatClient
from refindery.domain.entities import EntityType
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)

_MAX_CHARS = 12_000

_PROMPT = """Extract named entities from the text below.
Return ONLY a JSON array, no prose. Each element:
{{"surface_form": "...", "type": "person|org|product|technology|concept|place|work"}}

Text:
{text}
"""


class _RawMention(BaseModel):
    surface_form: str = Field(min_length=1)
    type: EntityType


_MENTIONS = TypeAdapter(list[_RawMention])


class LlmExtractor:
    """EntityExtractor over an OpenAI-compatible endpoint."""

    def __init__(self, client: OpenAiCompatClient | None) -> None:
        self._client = client

    def health_check(self) -> bool:
        """Report healthy when an endpoint is configured."""
        return self._client is not None

    async def extract(self, text: str) -> list[Mention]:
        """Prompt for strict JSON; validate; locate offsets."""
        if self._client is None:
            return []
        snippet = text[:_MAX_CHARS]
        raw = await self._client.complete(
            _PROMPT.format(text=snippet), max_tokens=1_000
        )
        raw = (
            raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        )
        try:
            parsed = _MENTIONS.validate_python(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            logger.warning("LLM extractor returned unparseable output")
            return []
        mentions: list[Mention] = []
        cursors: dict[str, int] = {}
        for item in parsed:
            cursor = cursors.get(item.surface_form, 0)
            if (start := snippet.find(item.surface_form, cursor)) < 0:
                # More repeats reported than occurrences exist; dropping the
                # extra beats double-counting an already-used offset.
                continue
            cursors[item.surface_form] = start + len(item.surface_form)
            mentions.append(
                Mention(
                    surface_form=item.surface_form,
                    type=item.type,
                    char_start=start,
                    char_end=start + len(item.surface_form),
                )
            )
        return mentions

    async def aclose(self) -> None:
        """Close the configured LLM client."""
        if self._client is not None:
            await self._client.aclose()
