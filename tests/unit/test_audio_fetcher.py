"""AudioTranscriptFetcher: download to a temp file, transcribe, clean up."""

import pytest

from refindery.adapters.transcription.audio_fetcher import AudioTranscriptFetcher
from refindery.adapters.transcription.envelope import (
    AUDIO_TRANSCRIPT_CONTENT_TYPE,
    AudioTranscriptEnvelope,
)
from refindery.domain.audio import is_audio_content_type
from refindery.domain.errors import FetchFailedError
from tests.fakes.extraction import FakeFileDownloader
from tests.fakes.youtube import FakeTranscriber

AUDIO_URL = "https://cdn.example/episodes/42.mp3?token=abc"


def _fetcher(
    downloader: FakeFileDownloader, transcriber: FakeTranscriber
) -> AudioTranscriptFetcher:
    return AudioTranscriptFetcher(downloader=downloader, transcriber=transcriber)


async def test_envelope_round_trip_and_temp_cleanup():
    downloader = FakeFileDownloader({AUDIO_URL: (b"ID3fake", "audio/mpeg")})
    transcriber = FakeTranscriber("spoken episode words")

    result = await _fetcher(downloader, transcriber).fetch(AUDIO_URL)

    assert result.content_type == AUDIO_TRANSCRIPT_CONTENT_TYPE
    envelope = AudioTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript == "spoken episode words"
    assert envelope.source_url == AUDIO_URL
    assert envelope.title is None
    # The downloaded file keeps the URL's suffix (helps ffmpeg) and is gone.
    audio_path = transcriber.calls[0]
    assert audio_path.suffix == ".mp3"
    assert not audio_path.exists()


async def test_download_failure_propagates_and_skips_transcription():
    downloader = FakeFileDownloader()
    transcriber = FakeTranscriber()
    with pytest.raises(FetchFailedError, match="no fake response"):
        await _fetcher(downloader, transcriber).fetch(AUDIO_URL)
    assert transcriber.calls == []


async def test_non_audio_content_type_is_rejected_before_transcription():
    downloader = FakeFileDownloader({AUDIO_URL: (b"<html>404</html>", "text/html")})
    transcriber = FakeTranscriber()
    with pytest.raises(FetchFailedError, match="unexpected content type"):
        await _fetcher(downloader, transcriber).fetch(AUDIO_URL)
    assert transcriber.calls == []


async def test_empty_transcript_is_a_fetch_failure():
    downloader = FakeFileDownloader({AUDIO_URL: (b"ID3fake", "audio/mpeg")})
    transcriber = FakeTranscriber("   \n ")
    with pytest.raises(FetchFailedError, match="empty transcript"):
        await _fetcher(downloader, transcriber).fetch(AUDIO_URL)
    assert not transcriber.calls[0].exists()


def test_audio_content_type_predicate():
    assert is_audio_content_type("audio/mpeg")
    assert is_audio_content_type("audio/x-m4a")
    assert is_audio_content_type("application/octet-stream")
    assert is_audio_content_type("application/ogg")
    assert is_audio_content_type(" Application/Ogg; charset=binary ")
    assert not is_audio_content_type("text/html")
    assert not is_audio_content_type("video/mp4")
