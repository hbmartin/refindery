"""Fetcher that resolves an audio URL to its Whisper transcript.

The audio streams to a temp file (the downloader owns SSRF pinning and the
byte cap — far larger than the in-memory fetch cap), is transcribed locally,
and returns as an AudioTranscriptEnvelope routed to its extractor by
synthetic content type. An HTML error page at an audio-looking URL is
rejected by content type before any body byte is read, never transcribed.
"""

import asyncio
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlsplit

from refindery.adapters.extraction.http_fetcher import FileFetchResult
from refindery.adapters.transcription.envelope import (
    AUDIO_TRANSCRIPT_CONTENT_TYPE,
    AudioTranscriptEnvelope,
)
from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.transcriber import Transcriber
from refindery.domain.errors import FetchFailedError

_GENERIC_AUDIO_CONTENT_TYPES = frozenset(
    {"application/octet-stream", "application/ogg"}
)


class FileDownloader(Protocol):
    """Streams a URL's body to a file; HttpFetcher matches structurally."""

    async def fetch_to_file(
        self,
        url: str,
        *,
        dest: Path,
        accept: Callable[[str], bool] | None = None,
    ) -> FileFetchResult:
        """Download ``url`` to ``dest``; raises FetchFailedError on failure."""
        ...


def is_audio_content_type(content_type: str) -> bool:
    """Accept ``audio/*`` plus the generic types podcast CDNs actually send."""
    return (
        content_type.startswith("audio/")
        or content_type in _GENERIC_AUDIO_CONTENT_TYPES
    )


class AudioTranscriptFetcher:
    """Fetcher implementation for direct audio URLs."""

    def __init__(self, *, downloader: FileDownloader, transcriber: Transcriber) -> None:
        self._downloader = downloader
        self._transcriber = transcriber

    async def fetch(self, url: str) -> FetchResult:
        """Download the audio, transcribe it, and wrap it in an envelope."""
        downloaded, transcript = await self._transcribe(url)
        if not transcript.strip():
            raise FetchFailedError(url=url, detail="empty transcript")
        envelope = AudioTranscriptEnvelope(
            title=None,
            language=None,
            transcript=transcript,
            source_url=downloaded.final_url,
        )
        return FetchResult(
            url=url,
            final_url=downloaded.final_url,
            status_code=200,
            content_type=AUDIO_TRANSCRIPT_CONTENT_TYPE,
            charset="utf-8",
            body=envelope.model_dump_json().encode("utf-8"),
        )

    async def _transcribe(self, url: str) -> tuple[FileFetchResult, str]:
        tmp_dir = await asyncio.to_thread(
            lambda: tempfile.mkdtemp(prefix="refindery-audio-")
        )
        try:
            suffix = PurePosixPath(urlsplit(url).path).suffix or ".mp3"
            downloaded = await self._downloader.fetch_to_file(
                url,
                dest=Path(tmp_dir) / f"audio{suffix}",
                accept=is_audio_content_type,
            )
            transcript = await self._transcriber.transcribe(downloaded.path)
            return downloaded, transcript
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp_dir, ignore_errors=True)
