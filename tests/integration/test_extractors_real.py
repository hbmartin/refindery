"""Real-model extractor tests (opt-in: model downloads + JIT warmup).

Run with ``uv run pytest -m slow tests/integration/test_extractors_real.py``.
These catch upstream regressions the stubbed tests cannot — e.g. the
transformers-5 GLiNER uniform-scoring bug the canary guards against.
"""

import pytest

from refindery.domain.entities import EntityType

pytestmark = pytest.mark.slow

_SENTENCE = "Guido van Rossum created Python at CWI in Amsterdam."


async def test_gliner_real_canary_and_extraction():
    pytest.importorskip("gliner")
    from refindery.adapters.extractors.gliner_spacy import (
        GlinerExtractor,
    )

    extractor = GlinerExtractor()
    assert extractor.health_check() is True, (
        "GLiNER canary failed — likely the transformers-5 scoring regression"
    )
    mentions = await extractor.extract(_SENTENCE)
    surfaces = {m.surface_form for m in mentions}
    assert any("Guido" in s for s in surfaces)
    assert any("Python" in s for s in surfaces)


async def test_spacy_real_extraction():
    pytest.importorskip("spacy")
    from refindery.adapters.extractors.spacy_ner import SpacyExtractor

    extractor = SpacyExtractor()
    if not extractor.health_check():
        pytest.skip("en_core_web_sm not installed (uv sync --extra ner)")
    mentions = await extractor.extract("Barack Obama visited Paris in 2015.")
    by_type = {m.type for m in mentions}
    assert EntityType.PERSON in by_type
    assert EntityType.PLACE in by_type
