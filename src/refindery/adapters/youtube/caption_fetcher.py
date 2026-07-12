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

from refindery.adapters.youtube.backend import VideoCaptionsResult, YoutubeBackend
from refindery.adapters.youtube.captions import (
    transcript_from_json3,
    transcript_from_vtt,
)
from refindery.adapters.youtube.envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.transcriber import Transcriber
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
            transcript = (
                transcript_from_json3(probe.track.content)
                if probe.track.fmt == "json3"
                else transcript_from_vtt(probe.track.content)
            )
            source = (
                TranscriptSource.AUTO_CAPTIONS
                if probe.track.is_automatic
                else TranscriptSource.MANUAL_CAPTIONS
            )
            language = probe.track.language
        elif self._transcribe_fallback and self._transcriber is not None:
            transcript = await self._transcribe_audio(url)
            source = TranscriptSource.TRANSCRIBED
            language = None
        else:
            raise FetchFailedError(
                url=url, detail="no captions available and transcription unavailable"
            )
        if not transcript.strip():
            raise FetchFailedError(url=url, detail="empty transcript")
        return self._envelope_result(
            url=url,
            probe=probe,
            transcript=transcript,
            source=source,
            language=language,
        )

    async def _transcribe_audio(self, url: str) -> str:
        assert self._transcriber is not None  # noqa: S101 — guarded by caller
        tmp_dir = await asyncio.to_thread(
            lambda: tempfile.mkdtemp(prefix="refindery-yt-")
        )
        try:
            audio_path = await self._backend.download_audio(
                url, dest_dir=Path(tmp_dir), timeout_s=self._timeout_s
            )
            return await self._transcriber.transcribe(audio_path)
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
