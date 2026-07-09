"""Composition-root validation tests."""

import pytest

import refindery.application.container as container_module
from refindery.config import EntitySettings
from refindery.domain.errors import ConfigurationError
from tests.fakes.container import make_test_settings


def test_build_container_requires_healthy_entity_extractor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        container_module, "_build_surface_embedder", lambda _settings: None
    )
    settings = make_test_settings(tmp_path).model_copy(
        update={
            "entity": EntitySettings(
                extractor_chain=("gazetteer",), gazetteer_patterns_path=None
            )
        }
    )

    with pytest.raises(ConfigurationError, match="uv sync --extra ner"):
        container_module.build_container(settings)


def test_surface_embedder_can_be_disabled(tmp_path):
    settings = make_test_settings(tmp_path).model_copy(
        update={"entity": EntitySettings(surface_embedder="none")}
    )
    assert container_module._build_surface_embedder(settings) is None  # noqa: SLF001
