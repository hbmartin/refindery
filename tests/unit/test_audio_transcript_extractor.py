"""Envelope round-trip through the audio transcript extractor."""

import pytest

from refindery.adapters.transcription.envelope import AudioTranscriptEnvelope
from refindery.adapters.transcription.extractor import AudioTranscriptExtractor


async def test_envelope_round_trip():
    envelope = AudioTranscriptEnvelope(
        title="An Episode",
        language="en",
        transcript="line one\nline two",
        source_url="https://cdn.example/audio/ep1.mp3",
    )
    extracted = await AudioTranscriptExtractor().extract(
        raw=envelope.model_dump_json().encode(), charset="utf-8"
    )
    assert extracted.body_text == "line one\nline two"
    assert extracted.title == "An Episode"


async def test_malformed_envelope_raises():
    with pytest.raises(ValueError, match="malformed audio transcript envelope"):
        await AudioTranscriptExtractor().extract(raw=b"{}", charset=None)
