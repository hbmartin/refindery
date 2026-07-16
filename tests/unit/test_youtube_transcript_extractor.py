"""Envelope round-trip through the transcript extractor."""

import pytest

from refindery.adapters.youtube.envelope import (
    TranscriptSource,
    YoutubeSection,
    YoutubeTranscriptEnvelope,
)
from refindery.adapters.youtube.extractor import YoutubeTranscriptExtractor


async def test_envelope_round_trip():
    envelope = YoutubeTranscriptEnvelope(
        video_id="dQw4w9WgXcQ",
        title="A Video",
        language="en",
        source=TranscriptSource.MANUAL_CAPTIONS,
        transcript="line one\nline two",
        sections=(
            YoutubeSection(
                title="First",
                char_start=0,
                char_end=9,
                start_time_s=0.0,
            ),
            YoutubeSection(
                title="Second",
                char_start=9,
                char_end=17,
                start_time_s=30.0,
            ),
        ),
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    extracted = await YoutubeTranscriptExtractor().extract(
        raw=envelope.model_dump_json().encode(), charset="utf-8"
    )
    assert extracted.body_text == "line one\nline two"
    assert extracted.title == "A Video"
    assert extracted.sections is not None
    assert [section.title for section in extracted.sections] == ["First", "Second"]
    assert extracted.sections[1].start_time_s == 30.0


async def test_malformed_envelope_raises():
    with pytest.raises(ValueError, match="malformed youtube transcript envelope"):
        await YoutubeTranscriptExtractor().extract(raw=b"{}", charset=None)
