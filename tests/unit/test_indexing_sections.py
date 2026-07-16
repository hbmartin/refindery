"""Section boundaries round-trip through Page.metadata for the indexing path."""

import json

import pytest
from pydantic import ValidationError

from refindery.application.services.indexing import (
    _sections_from_metadata,
    _sections_metadata,
)
from refindery.domain.models import Section


def test_sections_metadata_round_trips_through_json():
    sections = (
        Section(title="Intro", char_start=0, char_end=40, start_time_s=0.0),
        Section(title=None, char_start=40, char_end=90, start_time_s=None),
        Section(title="Outro", char_start=90, char_end=120, start_time_s=300.5),
    )
    persisted = _sections_metadata(sections)
    assert persisted is not None
    # Simulate the SQLite metadata JSON round-trip.
    reloaded = json.loads(json.dumps(persisted))
    assert _sections_from_metadata(reloaded) == sections


def test_sections_metadata_is_none_for_no_sections():
    assert _sections_metadata(None) is None
    assert _sections_metadata(()) is None


def test_sections_from_metadata_ignores_absent_or_empty():
    assert _sections_from_metadata(None) is None
    assert _sections_from_metadata({}) is None
    assert _sections_from_metadata({"other": 1}) is None
    assert _sections_from_metadata({"sections": []}) is None


def test_sections_from_metadata_rejects_malformed_entries():
    metadata: dict[str, object] = {
        "sections": [
            {"title": "ok", "char_start": 0, "char_end": 10, "start_time_s": 1.0},
            {"title": "bad", "char_start": "x", "char_end": 20},
        ]
    }
    with pytest.raises(ValidationError):
        _sections_from_metadata(metadata)


def test_sections_from_metadata_rejects_incomplete_body_tiling():
    metadata: dict[str, object] = {
        "sections": [
            {"title": "late", "char_start": 2, "char_end": 10},
        ]
    }
    with pytest.raises(ValueError, match="tile the complete page body"):
        _sections_from_metadata(metadata, body_len=10)
