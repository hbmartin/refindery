"""Gazetteer extractor: exact dictionary matching from a user patterns file.

Patterns file is JSONL of ``{"label": "technology", "pattern": "Kubernetes"}``.
Pure-Python matching (no spaCy dependency): case-insensitive whole-word
search — absolute precision on known terms.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from refindery.domain.entities import EntityType
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)


class GazetteerExtractor:
    """EntityExtractor over a fixed user-supplied dictionary."""

    def __init__(self, patterns_path: Path | None) -> None:
        self._patterns: list[tuple[re.Pattern[str], str, EntityType]] = []
        if patterns_path is None or not patterns_path.exists():
            return
        for line in patterns_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                entity_type = EntityType(row["label"])
                pattern = str(row["pattern"])
            except (json.JSONDecodeError, KeyError, ValueError):
                logger.warning("skipping bad gazetteer line: %s", line[:80])
                continue
            compiled = re.compile(rf"\b{re.escape(pattern)}\b", flags=re.IGNORECASE)
            self._patterns.append((compiled, pattern, entity_type))

    def health_check(self) -> bool:
        """Healthy when any patterns loaded."""
        return bool(self._patterns)

    async def extract(self, text: str) -> list[Mention]:
        """Scan for every pattern."""

        def _scan() -> list[Mention]:
            mentions: list[Mention] = []
            for compiled, _pattern, entity_type in self._patterns:
                mentions.extend(
                    Mention(
                        surface_form=match.group(0),
                        type=entity_type,
                        char_start=match.start(),
                        char_end=match.end(),
                    )
                    for match in compiled.finditer(text)
                )
            return mentions

        return await asyncio.to_thread(_scan)
