"""PodcastTranscriptExtractor unwraps the envelope into ExtractedContent."""

import pytest

from refindery.adapters.podcast.envelope import (
    PODCAST_TRANSCRIPT_CONTENT_TYPE,
    PodcastSection,
    PodcastTranscriptEnvelope,
)
from refindery.adapters.podcast.extractor import PodcastTranscriptExtractor


def _envelope(**overrides) -> bytes:
    base = {
        "episode_url": "https://pod.example/ep1",
        "title": "Episode 1",
        "language": "en",
        "transcript": "hello world\nsecond part",
        "sections": (
            PodcastSection(title="Intro", char_start=0, char_end=11, start_time_s=0.0),
            PodcastSection(title="Part", char_start=11, char_end=23, start_time_s=12.5),
        ),
        "source_url": "https://pod.example/ep1.vtt",
    }
    base.update(overrides)
    return PodcastTranscriptEnvelope(**base).model_dump_json().encode("utf-8")


def test_content_type_is_the_synthetic_podcast_type():
    assert PodcastTranscriptExtractor().content_types == frozenset(
        {PODCAST_TRANSCRIPT_CONTENT_TYPE}
    )


async def test_extract_surfaces_transcript_title_and_sections():
    extracted = await PodcastTranscriptExtractor().extract(
        raw=_envelope(), charset=None
    )
    assert extracted.body_text == "hello world\nsecond part"
    assert extracted.title == "Episode 1"
    assert extracted.sections is not None
    assert [(s.title, s.char_start, s.char_end) for s in extracted.sections] == [
        ("Intro", 0, 11),
        ("Part", 11, 23),
    ]
    assert extracted.sections[1].start_time_s == 12.5


async def test_empty_sections_become_none_for_flat_chunking():
    extracted = await PodcastTranscriptExtractor().extract(
        raw=_envelope(sections=()), charset=None
    )
    assert extracted.sections is None


async def test_malformed_envelope_raises_value_error():
    with pytest.raises(ValueError, match="malformed podcast transcript envelope"):
        await PodcastTranscriptExtractor().extract(raw=b"{not json", charset=None)
