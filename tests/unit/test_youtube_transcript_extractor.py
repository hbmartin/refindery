"""Envelope round-trip through the transcript extractor."""

import pytest

from refindery.adapters.youtube.envelope import (
    TranscriptSource,
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
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    extracted = await YoutubeTranscriptExtractor().extract(
        raw=envelope.model_dump_json().encode(), charset="utf-8"
    )
    assert extracted.body_text == "line one\nline two"
    assert extracted.title == "A Video"


async def test_malformed_envelope_raises():
    with pytest.raises(ValueError, match="malformed youtube transcript envelope"):
        await YoutubeTranscriptExtractor().extract(raw=b"{}", charset=None)
