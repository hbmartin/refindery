"""Tests for the yt-dlp caption fetcher (backend + transcriber faked)."""

from pathlib import Path

import pytest

from refindery.adapters.extraction.youtube_envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.adapters.extraction.youtube_fetcher import (
    YoutubeCaptionFetcher,
    _AudioResult,
    _CaptionData,
)
from refindery.domain.errors import FetchFailedError
from tests.fakes.extraction import FakeTranscriber, FakeYtDlpBackend

_URL = "https://youtu.be/v1"


def _captions(payload: str, fmt: str = "json3") -> _CaptionData:
    return _CaptionData(
        video_id="v1",
        title="Title",
        language="en",
        source=TranscriptSource.MANUAL_CAPTIONS,
        payload_format=fmt,
        payload=payload,
    )


def _audio() -> _AudioResult:
    return _AudioResult(
        path=Path("audio.m4a"), video_id="v1", title="Title", language="en"
    )


def _envelope(result) -> YoutubeTranscriptEnvelope:
    return YoutubeTranscriptEnvelope.model_validate_json(result.body)


def _fetcher(
    backend, *, transcriber=None, transcribe_fallback=True
) -> YoutubeCaptionFetcher:
    return YoutubeCaptionFetcher(
        langs=("en",),
        allow_auto=True,
        transcribe_fallback=transcribe_fallback,
        timeout_s=1.0,
        backend=backend,
        transcriber=transcriber,
    )


async def test_fetch_uses_captions():
    backend = FakeYtDlpBackend(
        captions=_captions('{"events":[{"segs":[{"utf8":"hello world"}]}]}')
    )
    result = await _fetcher(backend, transcribe_fallback=False).fetch(_URL)
    assert result.content_type == YOUTUBE_TRANSCRIPT_CONTENT_TYPE
    envelope = _envelope(result)
    assert envelope.transcript == "hello world"
    assert envelope.title == "Title"
    assert envelope.source == TranscriptSource.MANUAL_CAPTIONS
    assert backend.audio_calls == []


async def test_fetch_falls_back_to_transcription():
    backend = FakeYtDlpBackend(captions=None, audio=_audio())
    transcriber = FakeTranscriber("transcribed text")
    result = await _fetcher(backend, transcriber=transcriber).fetch(_URL)
    envelope = _envelope(result)
    assert envelope.transcript == "transcribed text"
    assert envelope.source == TranscriptSource.TRANSCRIBED
    assert backend.audio_calls == [_URL]
    assert len(transcriber.calls) == 1


async def test_fetch_empty_captions_fall_through_to_transcription():
    backend = FakeYtDlpBackend(captions=_captions('{"events":[]}'), audio=_audio())
    transcriber = FakeTranscriber("fallback text")
    result = await _fetcher(backend, transcriber=transcriber).fetch(_URL)
    envelope = _envelope(result)
    assert envelope.transcript == "fallback text"
    assert envelope.source == TranscriptSource.TRANSCRIBED


async def test_fetch_no_captions_no_transcriber_raises():
    backend = FakeYtDlpBackend(captions=None)
    with pytest.raises(FetchFailedError):
        await _fetcher(backend, transcriber=None).fetch(_URL)
