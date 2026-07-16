"""YoutubeCaptionFetcher: caption path, transcription fallback, failure modes."""

import json

import pytest

from refindery.adapters.youtube.backend import (
    CaptionTrack,
    VideoCaptionsResult,
    YoutubeChapter,
)
from refindery.adapters.youtube.caption_fetcher import YoutubeCaptionFetcher
from refindery.adapters.youtube.envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.application.ports.transcriber import TranscriptionSegment
from refindery.domain.errors import FetchFailedError
from tests.fakes.youtube import FakeTranscriber, FakeYoutubeBackend

VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"
WATCH_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

JSON3 = json.dumps({"events": [{"segs": [{"utf8": "caption text"}]}]})


def _probe(
    track: CaptionTrack | None,
    *,
    chapters: tuple[YoutubeChapter, ...] = (),
) -> VideoCaptionsResult:
    return VideoCaptionsResult(
        video_id="dQw4w9WgXcQ",
        title="A Video",
        track=track,
        chapters=chapters,
    )


def _fetcher(
    backend: FakeYoutubeBackend,
    *,
    transcriber: FakeTranscriber | None = None,
    transcribe_fallback: bool = True,
) -> YoutubeCaptionFetcher:
    return YoutubeCaptionFetcher(
        backend=backend,
        transcriber=transcriber,
        langs=("en",),
        allow_auto=True,
        transcribe_fallback=transcribe_fallback,
        timeout_s=5.0,
    )


async def test_manual_captions_become_envelope():
    track = CaptionTrack(language="en", is_automatic=False, fmt="json3", content=JSON3)
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(track)})
    result = await _fetcher(backend).fetch(VIDEO_URL)

    assert result.content_type == YOUTUBE_TRANSCRIPT_CONTENT_TYPE
    assert result.final_url == WATCH_URL
    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript == "caption text"
    assert envelope.title == "A Video"
    assert envelope.source is TranscriptSource.MANUAL_CAPTIONS
    assert envelope.language == "en"


async def test_auto_vtt_captions_parse_and_mark_source():
    vtt = "WEBVTT\n\n00:00.000 --> 00:01.000\nauto text\n"
    track = CaptionTrack(language="en", is_automatic=True, fmt="vtt", content=vtt)
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(track)})
    result = await _fetcher(backend).fetch(VIDEO_URL)
    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript == "auto text"
    assert envelope.source is TranscriptSource.AUTO_CAPTIONS


async def test_caption_timing_maps_video_chapters_to_transcript_sections():
    content = json.dumps(
        {
            "events": [
                {"tStartMs": 0, "segs": [{"utf8": "intro words"}]},
                {"tStartMs": 60_000, "segs": [{"utf8": "main words"}]},
                {"tStartMs": 120_000, "segs": [{"utf8": "outro words"}]},
            ]
        }
    )
    track = CaptionTrack(
        language="en", is_automatic=False, fmt="json3", content=content
    )
    chapters = (
        YoutubeChapter(title="Intro", start_time_s=0.0),
        YoutubeChapter(title="Main", start_time_s=60.0),
        YoutubeChapter(title="Outro", start_time_s=120.0),
    )
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(track, chapters=chapters)})

    result = await _fetcher(backend).fetch(VIDEO_URL)

    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert [section.title for section in envelope.sections] == [
        "Intro",
        "Main",
        "Outro",
    ]
    assert [section.char_start for section in envelope.sections] == [0, 12, 23]
    assert envelope.sections[-1].char_end == len(envelope.transcript)
    assert envelope.sections[1].start_time_s == 60.0


async def test_no_captions_falls_back_to_transcription():
    backend = FakeYoutubeBackend(
        captions={VIDEO_URL: _probe(None)}, audio={VIDEO_URL: b"fake-audio"}
    )
    transcriber = FakeTranscriber("spoken words")
    result = await _fetcher(backend, transcriber=transcriber).fetch(VIDEO_URL)
    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript == "spoken words"
    assert envelope.source is TranscriptSource.TRANSCRIBED
    assert len(transcriber.calls) == 1
    # The temp audio dir is cleaned up after transcription.
    assert not transcriber.calls[0].exists()


async def test_transcription_timing_maps_video_chapters_to_sections():
    chapters = (
        YoutubeChapter(title="Intro", start_time_s=0.0),
        YoutubeChapter(title="Main", start_time_s=60.0),
        YoutubeChapter(title="Outro", start_time_s=120.0),
    )
    backend = FakeYoutubeBackend(
        captions={VIDEO_URL: _probe(None, chapters=chapters)},
        audio={VIDEO_URL: b"fake-audio"},
    )
    transcriber = FakeTranscriber(
        "intro words\nmain words\noutro words",
        segments=(
            TranscriptionSegment(text="intro words", start_time_s=0.0, end_time_s=30.0),
            TranscriptionSegment(text="main words", start_time_s=60.0, end_time_s=90.0),
            TranscriptionSegment(
                text="outro words", start_time_s=120.0, end_time_s=150.0
            ),
        ),
    )

    result = await _fetcher(backend, transcriber=transcriber).fetch(VIDEO_URL)

    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.source is TranscriptSource.TRANSCRIBED
    assert [section.title for section in envelope.sections] == [
        "Intro",
        "Main",
        "Outro",
    ]
    assert [section.char_start for section in envelope.sections] == [0, 12, 23]
    assert envelope.sections[1].start_time_s == 60.0


async def test_empty_captions_fall_back_to_transcription():
    track = CaptionTrack(
        language="en", is_automatic=False, fmt="json3", content='{"events": []}'
    )
    backend = FakeYoutubeBackend(
        captions={VIDEO_URL: _probe(track)}, audio={VIDEO_URL: b"fake-audio"}
    )
    transcriber = FakeTranscriber("fallback words")

    result = await _fetcher(backend, transcriber=transcriber).fetch(VIDEO_URL)

    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript == "fallback words"
    assert envelope.source is TranscriptSource.TRANSCRIBED
    assert len(transcriber.calls) == 1


async def test_no_captions_and_no_transcriber_fails():
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(None)})
    with pytest.raises(FetchFailedError, match="transcription unavailable"):
        await _fetcher(backend, transcriber=None).fetch(VIDEO_URL)


async def test_transcribe_fallback_disabled_fails_even_with_transcriber():
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(None)})
    fetcher = _fetcher(
        backend, transcriber=FakeTranscriber(), transcribe_fallback=False
    )
    with pytest.raises(FetchFailedError, match="transcription unavailable"):
        await fetcher.fetch(VIDEO_URL)


async def test_backend_error_propagates_as_fetch_failure():
    backend = FakeYoutubeBackend()  # nothing configured -> backend raises
    with pytest.raises(FetchFailedError, match="no fake captions"):
        await _fetcher(backend).fetch(VIDEO_URL)


async def test_empty_transcript_fails():
    track = CaptionTrack(
        language="en", is_automatic=False, fmt="json3", content='{"events": []}'
    )
    backend = FakeYoutubeBackend(captions={VIDEO_URL: _probe(track)})
    with pytest.raises(FetchFailedError, match="empty transcript"):
        await _fetcher(backend).fetch(VIDEO_URL)
