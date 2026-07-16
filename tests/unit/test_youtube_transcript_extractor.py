"""Tests for the YouTube transcript envelope extractor."""

from refindery.adapters.extraction.youtube_envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.adapters.extraction.youtube_transcript_extractor import (
    YoutubeTranscriptExtractor,
)


def test_content_types():
    extractor = YoutubeTranscriptExtractor()
    assert extractor.content_types == frozenset({YOUTUBE_TRANSCRIPT_CONTENT_TYPE})


async def test_extract_unwraps_envelope():
    envelope = YoutubeTranscriptEnvelope(
        video_id="v1",
        title="My Video",
        language="en",
        source=TranscriptSource.AUTO_CAPTIONS,
        transcript="line one\nline two",
        source_url="https://youtu.be/v1",
    )
    extractor = YoutubeTranscriptExtractor()
    result = await extractor.extract(
        raw=envelope.model_dump_json().encode("utf-8"), charset="utf-8"
    )
    assert result.body_text == "line one\nline two"
    assert result.title == "My Video"
