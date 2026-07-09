"""spaCy NER extractor (en_core_web_sm via the ``ner`` extra).

spaCy's label set maps onto the fixed taxonomy; unmapped labels (DATE,
CARDINAL, ...) are dropped. ``technology``/``concept`` are underserved by
spaCy — acceptable degradation, the gazetteer and GLiNER cover them.

Inference runs in a thread (models are expensive to pickle into a process
pool and release the GIL).
"""

import asyncio
import logging
from typing import Any

from refindery.domain.entities import EntityType
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)

_LABEL_MAP: dict[str, EntityType] = {
    "PERSON": EntityType.PERSON,
    "ORG": EntityType.ORG,
    "GPE": EntityType.PLACE,
    "LOC": EntityType.PLACE,
    "FAC": EntityType.PLACE,
    "PRODUCT": EntityType.PRODUCT,
    "WORK_OF_ART": EntityType.WORK,
    "EVENT": EntityType.CONCEPT,
    "LAW": EntityType.WORK,
    "LANGUAGE": EntityType.CONCEPT,
    "NORP": EntityType.CONCEPT,
}
_MAX_CHARS = 100_000


class SpacyExtractor:
    """EntityExtractor over en_core_web_sm."""

    def __init__(self, model: str = "en_core_web_sm") -> None:
        self._nlp: Any | None = None
        try:
            import spacy  # noqa: PLC0415 — optional extra

            self._nlp = spacy.load(model, exclude=["lemmatizer", "textcat"])
        except (ImportError, OSError):
            logger.warning("spaCy model %s unavailable", model)

    def health_check(self) -> bool:
        """Model loaded."""
        return self._nlp is not None

    async def extract(self, text: str) -> list[Mention]:
        """Run NER in a thread; map labels; keep char offsets."""
        if self._nlp is None:
            return []
        doc = await asyncio.to_thread(self._nlp, text[:_MAX_CHARS])
        mentions: list[Mention] = []
        for ent in doc.ents:
            if (entity_type := _LABEL_MAP.get(ent.label_)) is None:
                continue
            mentions.append(
                Mention(
                    surface_form=ent.text,
                    type=entity_type,
                    char_start=ent.start_char,
                    char_end=ent.end_char,
                )
            )
        return mentions
