"""GLiNER zero-shot extractor (``gliner`` extra; ONNX backend).

GLiNER's torch path is broken under transformers 5.x (uniform ~0.05 scores,
GLiNER issue #324), which this repo locks. Two defenses: prefer the ONNX
backend, and run a load-time canary — a fixed sentence must yield sane,
non-uniform scores — before this adapter reports healthy. When the canary
fails the chain falls through to spaCy.
"""

import asyncio
import logging

from refindery.domain.entities import EntityType
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)

_MODEL = "urchade/gliner_small-v2.1"
_LABELS = [t.value for t in EntityType]
_CANARY = "Guido van Rossum created Python at CWI in Amsterdam."
_CANARY_MIN_MENTIONS = 2
_CANARY_MIN_SCORE = 0.3
_MAX_CHARS = 30_000
_THRESHOLD = 0.4


class GlinerExtractor:
    """EntityExtractor over GLiNER zero-shot NER."""

    def __init__(self, model: str = _MODEL) -> None:
        self._model = None
        self._healthy = False
        try:
            from gliner import (  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]
                GLiNER,
            )

            try:
                self._model = GLiNER.from_pretrained(
                    model, load_onnx_model=True, load_tokenizer=True
                )
            except (OSError, TypeError, ValueError):
                self._model = GLiNER.from_pretrained(model)
        except ImportError:
            logger.info("gliner extra not installed")
            return
        except Exception:  # noqa: BLE001 — any load failure = unhealthy
            logger.warning("GLiNER failed to load", exc_info=True)
            return
        self._healthy = self._canary()

    def _canary(self) -> bool:
        if self._model is None:
            return False
        try:
            found = self._model.predict_entities(
                _CANARY, _LABELS, threshold=_CANARY_MIN_SCORE
            )
        except Exception:  # noqa: BLE001 — broken model path
            logger.warning("GLiNER canary crashed", exc_info=True)
            return False
        scores = [float(e.get("score", 0.0)) for e in found]
        distinct = len({round(s, 3) for s in scores})
        healthy = len(found) >= _CANARY_MIN_MENTIONS and distinct > 1
        if not healthy:
            logger.error(
                "GLiNER canary failed (%d mentions, %d distinct scores) — "
                "likely the transformers-5 scoring bug "
                "(github.com/urchade/GLiNER/issues/324); falling back to spaCy",
                len(found),
                distinct,
            )
        return healthy

    def health_check(self) -> bool:
        """Canary verdict from load time."""
        return self._healthy

    async def extract(self, text: str) -> list[Mention]:
        """Zero-shot NER over the taxonomy labels."""
        if self._model is None:
            return []
        found = await asyncio.to_thread(
            self._model.predict_entities,
            text[:_MAX_CHARS],
            _LABELS,
            threshold=_THRESHOLD,
        )
        return [
            Mention(
                surface_form=str(e["text"]),
                type=str(e["label"]),
                char_start=int(e["start"]),
                char_end=int(e["end"]),
            )
            for e in found
        ]
