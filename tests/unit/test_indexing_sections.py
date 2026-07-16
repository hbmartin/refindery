"""Section boundaries round-trip through Page.metadata for the indexing path."""

import json

from refindery.application.services.indexing import (
    _opt_str,
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


def test_sections_from_metadata_ignores_absent_or_malformed():
    assert _sections_from_metadata(None) is None
    assert _sections_from_metadata({}) is None
    assert _sections_from_metadata({"other": 1}) is None
    assert _sections_from_metadata({"sections": "nope"}) is None
    assert _sections_from_metadata({"sections": []}) is None


def test_sections_from_metadata_skips_entries_missing_offsets():
    metadata: dict[str, object] = {
        "sections": [
            {"title": "ok", "char_start": 0, "char_end": 10, "start_time_s": 1.0},
            {"title": "bad", "char_start": "x", "char_end": 20},
            {"title": "no-end", "char_start": 20},
        ]
    }
    sections = _sections_from_metadata(metadata)
    assert sections == (
        Section(title="ok", char_start=0, char_end=10, start_time_s=1.0),
    )


def test_opt_str_narrows_non_strings_to_none():
    assert _opt_str("hello") == "hello"
    assert _opt_str(None) is None
    assert _opt_str(42) is None
