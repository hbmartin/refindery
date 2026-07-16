"""Fetcher that turns a YouTube URL into a transcript via yt-dlp.

Requires the ``youtube`` extra. It prefers manual captions, falls back to
auto-generated captions, and — when a ``Transcriber`` is provided and the video
has no captions at all — downloads the audio and transcribes it locally. The
result is wrapped in a ``YoutubeTranscriptEnvelope`` and returned as a
``FetchResult`` with a synthetic content type, so the existing
``ExtractionRouter`` carries the title through to indexing.

The untyped info dict yt-dlp returns is validated with pydantic before use.
"""

import asyncio
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from refindery.adapters.extraction.youtube_captions import parse_json3, parse_vtt
from refindery.adapters.extraction.youtube_envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.transcriber import Transcriber
from refindery.domain.errors import ExtractionUnavailableError, FetchFailedError

_YOUTUBE_EXTRA = "youtube"
_CAPTION_FORMATS = ("json3", "vtt")


@dataclass(frozen=True, slots=True)
class _CaptionData:
    """Raw captions selected from a video, before parsing to text."""

    video_id: str | None
    title: str | None
    language: str
    source: TranscriptSource
    payload_format: str
    payload: str


@dataclass(frozen=True, slots=True)
class _AudioResult:
    """A downloaded audio file plus the metadata needed for the envelope."""

    path: Path
    video_id: str | None
    title: str | None
    language: str | None


class _CaptionTrack(BaseModel):
    """One caption track entry (a format/URL pair) from the info dict."""

    model_config = ConfigDict(extra="ignore")

    ext: str | None = None
    url: str | None = None


class _RequestedDownload(BaseModel):
    """One completed download descriptor from the info dict."""

    model_config = ConfigDict(extra="ignore")

    filepath: str | None = None


class _VideoInfo(BaseModel):
    """The subset of yt-dlp's info dict this adapter consumes."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    title: str | None = None
    language: str | None = None
    subtitles: dict[str, list[_CaptionTrack]] = Field(default_factory=dict)
    automatic_captions: dict[str, list[_CaptionTrack]] = Field(default_factory=dict)
    requested_downloads: list[_RequestedDownload] = Field(default_factory=list)


def _pick_format(entries: list[_CaptionTrack]) -> tuple[str, str] | None:
    """Pick the (ext, url) of the most parseable caption format available."""
    for preferred in _CAPTION_FORMATS:
        for entry in entries:
            if entry.ext == preferred and entry.url:
                return preferred, entry.url
    return None


def _match_exact(
    tracks: dict[str, list[_CaptionTrack]], langs: Sequence[str]
) -> tuple[str, str, str] | None:
    """Match a caption track whose language key equals a configured lang."""
    for lang in langs:
        entries = tracks.get(lang)
        if entries and (picked := _pick_format(entries)) is not None:
            return lang, picked[0], picked[1]
    return None


def _match_prefix(
    tracks: dict[str, list[_CaptionTrack]], langs: Sequence[str]
) -> tuple[str, str, str] | None:
    """Match a track whose base language (before ``-``) equals a configured one."""
    bases = [lang.split("-", 1)[0] for lang in langs]
    for base in bases:
        for key, entries in tracks.items():
            if key.split("-", 1)[0] == base and (picked := _pick_format(entries)):
                return key, picked[0], picked[1]
    return None


def _match_track(
    tracks: dict[str, list[_CaptionTrack]], langs: Sequence[str]
) -> tuple[str, str, str] | None:
    """Select the best (lang, ext, url) caption track for the preferred langs."""
    return _match_exact(tracks, langs) or _match_prefix(tracks, langs)


def _resolve_audio_path(model: _VideoInfo, dest_dir: Path) -> Path | None:
    """Locate the downloaded audio file from the info dict or the temp dir."""
    for download in model.requested_downloads:
        if download.filepath:
            return Path(download.filepath)
    files = sorted(p for p in dest_dir.iterdir() if p.is_file())
    return files[0] if files else None


def _parse_captions(captions: _CaptionData) -> str:
    """Parse a caption payload to plain text by its format."""
    if captions.payload_format == "vtt":
        return parse_vtt(captions.payload)
    return parse_json3(captions.payload)


def _to_fetch_result(url: str, envelope: YoutubeTranscriptEnvelope) -> FetchResult:
    """Wrap a transcript envelope in a synthetic-content-type FetchResult."""
    return FetchResult(
        url=url,
        final_url=envelope.source_url,
        status_code=200,
        content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
        charset="utf-8",
        body=envelope.model_dump_json().encode("utf-8"),
    )


class _YtDlpBackend(Protocol):
    """Seam over yt-dlp so the fetcher can be unit-tested without network."""

    def fetch_captions(
        self, url: str, *, langs: Sequence[str], allow_auto: bool, timeout_s: float
    ) -> _CaptionData | None:
        """Return the best matching captions, or ``None`` if the video has none."""
        ...

    def download_audio(
        self, url: str, *, dest_dir: Path, timeout_s: float
    ) -> _AudioResult:
        """Download the best audio stream into ``dest_dir``."""
        ...


class YtDlpBackend:
    """Real yt-dlp backend (lazy import; requires the ``youtube`` extra)."""

    def __init__(self) -> None:
        if importlib_util.find_spec("yt_dlp") is None:
            raise ExtractionUnavailableError(
                content_type="video/youtube", extra=_YOUTUBE_EXTRA
            )

    def fetch_captions(
        self, url: str, *, langs: Sequence[str], allow_auto: bool, timeout_s: float
    ) -> _CaptionData | None:
        """Extract info, select a caption track, and download its payload."""
        from yt_dlp import (  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]
            YoutubeDL,
        )

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": timeout_s,
        }
        try:
            with YoutubeDL(opts) as ydl:
                model = _VideoInfo.model_validate(ydl.extract_info(url, download=False))
                match = _match_track(model.subtitles, langs)
                source = TranscriptSource.MANUAL_CAPTIONS
                if match is None and allow_auto:
                    match = _match_track(model.automatic_captions, langs)
                    source = TranscriptSource.AUTO_CAPTIONS
                if match is None:
                    return None
                lang, ext, sub_url = match
                payload = ydl.urlopen(sub_url).read().decode("utf-8", errors="replace")
        except ValidationError as exc:
            raise FetchFailedError(url=url, detail=str(exc)) from exc
        except Exception as exc:  # any yt-dlp failure = fetch failed
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        return _CaptionData(
            video_id=model.id,
            title=model.title,
            language=lang,
            source=source,
            payload_format=ext,
            payload=payload,
        )

    def download_audio(
        self, url: str, *, dest_dir: Path, timeout_s: float
    ) -> _AudioResult:
        """Download the best audio stream and return its path plus metadata."""
        from yt_dlp import (  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]
            YoutubeDL,
        )

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
            "socket_timeout": timeout_s,
        }
        try:
            with YoutubeDL(opts) as ydl:
                model = _VideoInfo.model_validate(ydl.extract_info(url, download=True))
                path = _resolve_audio_path(model, dest_dir)
        except ValidationError as exc:
            raise FetchFailedError(url=url, detail=str(exc)) from exc
        except Exception as exc:  # any yt-dlp failure = fetch failed
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        if path is None:
            raise FetchFailedError(url=url, detail="audio download produced no file")
        return _AudioResult(
            path=path, video_id=model.id, title=model.title, language=model.language
        )


class YoutubeCaptionFetcher:
    """Fetcher: YouTube URL -> transcript (captions, else local transcription)."""

    def __init__(
        self,
        *,
        langs: Sequence[str],
        allow_auto: bool,
        transcribe_fallback: bool,
        timeout_s: float,
        backend: _YtDlpBackend | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self._langs = tuple(langs)
        self._allow_auto = allow_auto
        self._transcribe_fallback = transcribe_fallback
        self._timeout_s = timeout_s
        self._backend = backend or YtDlpBackend()
        self._transcriber = transcriber

    async def fetch(self, url: str) -> FetchResult:
        """Fetch a transcript for ``url``; raise FetchFailedError on failure."""
        captions = await asyncio.to_thread(
            self._backend.fetch_captions,
            url,
            langs=self._langs,
            allow_auto=self._allow_auto,
            timeout_s=self._timeout_s,
        )
        if captions is not None:
            transcript = _parse_captions(captions)
            if transcript.strip():
                envelope = YoutubeTranscriptEnvelope(
                    video_id=captions.video_id,
                    title=captions.title,
                    language=captions.language,
                    source=captions.source,
                    transcript=transcript,
                    source_url=url,
                )
                return _to_fetch_result(url, envelope)
        transcriber = self._transcriber
        if self._transcribe_fallback and transcriber is not None:
            return await self._transcribe(url, transcriber)
        raise FetchFailedError(
            url=url, detail="no captions available and transcription unavailable"
        )

    async def _transcribe(self, url: str, transcriber: Transcriber) -> FetchResult:
        with tempfile.TemporaryDirectory() as tmp:
            audio = await asyncio.to_thread(
                self._backend.download_audio,
                url,
                dest_dir=Path(tmp),
                timeout_s=self._timeout_s,
            )
            transcript = await transcriber.transcribe(
                audio.path, language=audio.language
            )
        if not transcript.strip():
            raise FetchFailedError(url=url, detail="transcription produced no text")
        envelope = YoutubeTranscriptEnvelope(
            video_id=audio.video_id,
            title=audio.title,
            language=audio.language,
            source=TranscriptSource.TRANSCRIBED,
            transcript=transcript,
            source_url=url,
        )
        return _to_fetch_result(url, envelope)
