"""Extractor chain, GLiNER canary, and spaCy label mapping (stubbed models)."""

import sys
import types

from refindery.adapters.extractors.chain import ChainExtractor
from refindery.adapters.extractors.gliner_spacy import GlinerExtractor
from refindery.adapters.extractors.spacy_ner import SpacyExtractor
from refindery.domain.entities import EntityType
from refindery.domain.models import Mention

# -- ChainExtractor -----------------------------------------------------------


class _StubExtractor:
    def __init__(
        self,
        *,
        healthy: bool = True,
        mentions: list[Mention] | None = None,
        fail: Exception | None = None,
    ) -> None:
        self._healthy = healthy
        self._mentions = mentions or []
        self._fail = fail
        self.calls = 0
        self.closed = False

    def health_check(self) -> bool:
        return self._healthy

    async def extract(self, text: str) -> list[Mention]:  # noqa: ARG002 — port signature
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        return self._mentions

    def close(self) -> None:
        self.closed = True


def _mention(surface: str) -> Mention:
    return Mention(
        surface_form=surface,
        type=EntityType.PERSON,
        char_start=0,
        char_end=len(surface),
    )


async def test_first_healthy_extractor_wins():
    first = _StubExtractor(mentions=[_mention("Ada")])
    second = _StubExtractor(mentions=[_mention("Grace")])
    result = await ChainExtractor([first, second]).extract("text")
    assert [m.surface_form for m in result] == ["Ada"]
    assert second.calls == 0


async def test_unhealthy_links_are_skipped():
    unhealthy = _StubExtractor(healthy=False)
    healthy = _StubExtractor(mentions=[_mention("Ada")])
    result = await ChainExtractor([unhealthy, healthy]).extract("text")
    assert [m.surface_form for m in result] == ["Ada"]
    assert unhealthy.calls == 0


async def test_exception_falls_through_to_next_link():
    broken = _StubExtractor(fail=RuntimeError("model crashed"))
    fallback = _StubExtractor(mentions=[_mention("Ada")])
    result = await ChainExtractor([broken, fallback]).extract("text")
    assert [m.surface_form for m in result] == ["Ada"]
    assert broken.calls == 1


async def test_empty_when_every_link_fails():
    chain = ChainExtractor(
        [_StubExtractor(fail=RuntimeError("a")), _StubExtractor(fail=RuntimeError("b"))]
    )
    assert await chain.extract("text") == []


async def test_aclose_calls_close_hooks():
    first = _StubExtractor()
    second = _StubExtractor()
    await ChainExtractor([first, second]).aclose()
    assert first.closed
    assert second.closed


# -- GLiNER canary ------------------------------------------------------------


def _install_fake_gliner(monkeypatch, predictions, *, predict_fail=False) -> None:
    class _FakeModel:
        def predict_entities(self, text, labels, threshold) -> list[dict]:  # noqa: ARG002
            if predict_fail:
                msg = "broken model path"
                raise RuntimeError(msg)
            return predictions

    class _FakeGLiNER:
        @staticmethod
        def from_pretrained(model, **_kwargs) -> "_FakeModel":  # noqa: ARG004
            return _FakeModel()

    module = types.ModuleType("gliner")
    module.__dict__["GLiNER"] = _FakeGLiNER
    monkeypatch.setitem(sys.modules, "gliner", module)


_HEALTHY_PREDICTIONS = [
    {
        "text": "Guido van Rossum",
        "label": "person",
        "score": 0.92,
        "start": 0,
        "end": 16,
    },
    {"text": "Python", "label": "technology", "score": 0.71, "start": 25, "end": 31},
]


def test_canary_passes_with_distinct_scores(monkeypatch):
    _install_fake_gliner(monkeypatch, _HEALTHY_PREDICTIONS)
    assert GlinerExtractor().health_check() is True


def test_canary_fails_on_uniform_scores(monkeypatch):
    # The transformers-5 GLiNER regression: every score identical (~0.05).
    uniform = [dict(p, score=0.05) for p in _HEALTHY_PREDICTIONS]
    _install_fake_gliner(monkeypatch, uniform)
    assert GlinerExtractor().health_check() is False


def test_canary_fails_on_too_few_mentions(monkeypatch):
    _install_fake_gliner(monkeypatch, _HEALTHY_PREDICTIONS[:1])
    assert GlinerExtractor().health_check() is False


def test_canary_fails_when_predict_crashes(monkeypatch):
    _install_fake_gliner(monkeypatch, [], predict_fail=True)
    assert GlinerExtractor().health_check() is False


async def test_extract_maps_predictions_to_mentions(monkeypatch):
    _install_fake_gliner(monkeypatch, _HEALTHY_PREDICTIONS)
    mentions = await GlinerExtractor().extract("Guido van Rossum created Python.")
    assert [(m.surface_form, m.char_start, m.char_end) for m in mentions] == [
        ("Guido van Rossum", 0, 16),
        ("Python", 25, 31),
    ]


# -- spaCy label mapping ------------------------------------------------------


def _install_fake_spacy(monkeypatch, ents, *, load_fail: bool = False) -> None:
    class _FakeDoc:
        def __init__(self) -> None:
            self.ents = ents

    def _load(model, exclude) -> object:
        if load_fail:
            msg = f"can't find model {model}"
            raise OSError(msg)
        return lambda _text: _FakeDoc()

    module = types.ModuleType("spacy")
    module.__dict__["load"] = _load
    monkeypatch.setitem(sys.modules, "spacy", module)


def _ent(label: str, text: str, start: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        label_=label, text=text, start_char=start, end_char=start + len(text)
    )


async def test_spacy_maps_labels_and_drops_unmapped(monkeypatch):
    _install_fake_spacy(
        monkeypatch,
        [
            _ent("PERSON", "Barack Obama"),
            _ent("GPE", "Paris", start=20),
            _ent("DATE", "yesterday", start=30),
        ],
    )
    extractor = SpacyExtractor()
    assert extractor.health_check() is True
    mentions = await extractor.extract("Barack Obama visited Paris yesterday.")
    assert [(m.surface_form, m.type) for m in mentions] == [
        ("Barack Obama", EntityType.PERSON),
        ("Paris", EntityType.PLACE),
    ]


async def test_spacy_unavailable_model_is_unhealthy(monkeypatch):
    _install_fake_spacy(monkeypatch, [], load_fail=True)
    extractor = SpacyExtractor()
    assert extractor.health_check() is False
    assert await extractor.extract("anything") == []
