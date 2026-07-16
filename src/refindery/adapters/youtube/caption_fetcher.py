"""Fetcher that resolves a YouTube video URL to its transcript.

Preference order: manual captions, auto-generated captions, then (opt-in)
audio download + local Whisper transcription. The result is a FetchResult
whose body is a YoutubeTranscriptEnvelope routed to the transcript extractor
by its synthetic content type. No captions and no transcriber -> the fetch
fails and the page eventually goes DEAD; watch-page HTML is never indexed.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from refindery.adapters.youtube.backend import (
    VideoCaptionsResult,
    YoutubeBackend,
    YoutubeChapter,
)
from refindery.adapters.youtube.captions import parse_json3, parse_vtt
from refindery.adapters.youtube.envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeSection,
    YoutubeTranscriptEnvelope,
)
from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.transcriber import Transcriber, TranscriptionResult
from refindery.domain.errors import FetchFailedError


class YoutubeCaptionFetcher:
    """Fetcher implementation for YouTube video URLs."""

    def __init__(
        self,
        *,
        backend: YoutubeBackend,
        transcriber: Transcriber | None,
        langs: tuple[str, ...],
        allow_auto: bool,
        transcribe_fallback: bool,
        timeout_s: float,
    ) -> None:
        self._backend = backend
        self._transcriber = transcriber
        self._langs = langs
        self._allow_auto = allow_auto
        self._transcribe_fallback = transcribe_fallback
        self._timeout_s = timeout_s

    async def fetch(self, url: str) -> FetchResult:
        """Resolve the video's transcript and wrap it in an envelope."""
        probe = await self._backend.fetch_captions(
            url,
            langs=self._langs,
            allow_auto=self._allow_auto,
            timeout_s=self._timeout_s,
        )
        if probe.track is not None:
            parsed = (
                parse_json3(probe.track.content)
                if probe.track.fmt == "json3"
                else parse_vtt(probe.track.content)
            )
            if parsed.text.strip():
                source = (
                    TranscriptSource.AUTO_CAPTIONS
                    if probe.track.is_automatic
                    else TranscriptSource.MANUAL_CAPTIONS
                )
                return self._envelope_result(
                    url=url,
                    probe=probe,
                    transcript=parsed.text,
                    source=source,
                    language=probe.track.language,
                    sections=_sections_from_chapters(
                        chapters=probe.chapters,
                        transcript=parsed.text,
                        offsets=parsed.offsets,
                    ),
                )
        if self._transcribe_fallback and self._transcriber is not None:
            transcription = await self._transcribe_audio(
                url,
                language=probe.language,
            )
        else:
            detail = (
                "empty transcript"
                if probe.track is not None
                else "no captions available and transcription unavailable"
            )
            raise FetchFailedError(url=url, detail=detail)
        if not transcription.text.strip():
            raise FetchFailedError(url=url, detail="empty transcript")
        return self._envelope_result(
            url=url,
            probe=probe,
            transcript=transcription.text,
            source=TranscriptSource.TRANSCRIBED,
            language=probe.language,
            sections=_sections_from_chapters(
                chapters=probe.chapters,
                transcript=transcription.text,
                offsets=transcription.timed_offsets,
            ),
        )

    async def _transcribe_audio(
        self,
        url: str,
        *,
        language: str | None,
    ) -> TranscriptionResult:
        assert self._transcriber is not None  # noqa: S101 — guarded by caller
        tmp_dir = await asyncio.to_thread(
            lambda: tempfile.mkdtemp(prefix="refindery-yt-")
        )
        try:
            audio_path = await self._backend.download_audio(
                url, dest_dir=Path(tmp_dir), timeout_s=self._timeout_s
            )
            return await self._transcriber.transcribe(audio_path, language=language)
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp_dir, ignore_errors=True)

    def _envelope_result(
        self,
        *,
        url: str,
        probe: VideoCaptionsResult,
        transcript: str,
        source: TranscriptSource,
        language: str | None,
        sections: tuple[YoutubeSection, ...] = (),
    ) -> FetchResult:
        final_url = (
            f"https://www.youtube.com/watch?v={probe.video_id}"
            if probe.video_id
            else url
        )
        envelope = YoutubeTranscriptEnvelope(
            video_id=probe.video_id,
            title=probe.title,
            language=language,
            source=source,
            transcript=transcript,
            sections=sections,
            source_url=final_url,
        )
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=200,
            content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
            charset="utf-8",
            body=envelope.model_dump_json().encode("utf-8"),
        )


def _sections_from_chapters(
    *,
    chapters: tuple[YoutubeChapter, ...],
    transcript: str,
    offsets: tuple[tuple[int, float], ...],
) -> tuple[YoutubeSection, ...]:
    """Map YouTube chapter starts onto timed transcript character offsets."""
    if not chapters or not offsets or not transcript:
        return ()
    body_len = len(transcript)
    boundaries: list[tuple[int, str | None, float]] = []
    for chapter in sorted(chapters, key=lambda item: item.start_time_s):
        char_start = _char_for_time(
            offsets=offsets,
            time_s=float(chapter.start_time_s),
            body_len=body_len,
        )
        if char_start >= body_len:
            continue
        boundary = (char_start, chapter.title, float(chapter.start_time_s))
        if boundaries and boundaries[-1][0] == char_start:
            boundaries[-1] = boundary
        else:
            boundaries.append(boundary)
    if not boundaries:
        return ()
    if boundaries[0][0] > 0:
        boundaries.insert(0, (0, None, 0.0))
    return tuple(
        YoutubeSection(
            title=title,
            char_start=char_start,
            char_end=(
                boundaries[index + 1][0] if index + 1 < len(boundaries) else body_len
            ),
            start_time_s=start_time_s,
        )
        for index, (char_start, title, start_time_s) in enumerate(boundaries)
    )


def _char_for_time(
    *,
    offsets: tuple[tuple[int, float], ...],
    time_s: float,
    body_len: int,
) -> int:
    """Return the first retained caption offset at or after a chapter start."""
    return next(
        (
            char_start
            for char_start, caption_time_s in offsets
            if caption_time_s >= time_s
        ),
        body_len,
    )
