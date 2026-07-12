"""yt-dlp backend shared by the caption fetcher and the YouTube watch source.

All yt-dlp calls are blocking; the real backend runs them in a worker thread.
yt-dlp errors are mapped to FetchFailedError so callers see one failure type.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from refindery.adapters.youtube.envelope import YOUTUBE_TRANSCRIPT_CONTENT_TYPE
from refindery.domain.errors import ExtractionUnavailableError, FetchFailedError

logger = logging.getLogger(__name__)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YoutubeEntry(BaseModel):
    """One video discovered in a playlist/channel flat extraction."""

    model_config = ConfigDict(frozen=True)

    video_id: str
    url: str
    title: str | None = None
    published_at: datetime | None = None


class CaptionTrack(BaseModel):
    """One downloaded caption track."""

    model_config = ConfigDict(frozen=True)

    language: str
    is_automatic: bool
    fmt: Literal["json3", "vtt"]
    content: str


class VideoCaptionsResult(BaseModel):
    """Caption probe result; ``track`` is None when no acceptable track exists."""

    model_config = ConfigDict(frozen=True)

    video_id: str | None
    title: str | None
    track: CaptionTrack | None


class YoutubeBackend(Protocol):
    """The yt-dlp surface both YouTube features consume."""

    async def fetch_captions(
        self, url: str, *, langs: tuple[str, ...], allow_auto: bool, timeout_s: float
    ) -> VideoCaptionsResult:
        """Probe a video and download its best caption track, if any."""
        ...

    async def download_audio(
        self, url: str, *, dest_dir: Path, timeout_s: float
    ) -> Path:
        """Download the video's audio into dest_dir; returns the file path."""
        ...

    async def list_entries(
        self, url: str, *, max_entries: int, timeout_s: float
    ) -> list[YoutubeEntry]:
        """Flat-extract a playlist/channel into its video entries."""
        ...


def _preferred_langs(available: list[str], langs: tuple[str, ...]) -> list[str]:
    """Order available caption languages by the configured preference.

    Exact matches first (in configured order), then base-language prefix
    matches (``en`` matches ``en-US``), then any English variant.
    """
    ordered: list[str] = []
    for wanted in langs:
        ordered += [lang for lang in available if lang == wanted]
    for wanted in langs:
        base = wanted.split("-")[0]
        ordered += [lang for lang in available if lang.split("-")[0] == base]
    ordered += [lang for lang in available if lang.split("-")[0] == "en"]
    seen: set[str] = set()
    return [lang for lang in ordered if not (lang in seen or seen.add(lang))]


class YtDlpBackend:
    """Real yt-dlp backend; requires the ``youtube`` extra."""

    def __init__(self) -> None:
        if find_spec("yt_dlp") is None:
            raise ExtractionUnavailableError(
                content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE, extra="youtube"
            )

    async def fetch_captions(
        self, url: str, *, langs: tuple[str, ...], allow_auto: bool, timeout_s: float
    ) -> VideoCaptionsResult:
        """Probe a video and download its best caption track, if any."""
        return await asyncio.to_thread(
            lambda: self._fetch_captions_sync(
                url, langs=langs, allow_auto=allow_auto, timeout_s=timeout_s
            )
        )

    async def download_audio(
        self, url: str, *, dest_dir: Path, timeout_s: float
    ) -> Path:
        """Download the video's audio into dest_dir; returns the file path."""
        return await asyncio.to_thread(
            self._download_audio_sync, url, dest_dir, timeout_s
        )

    async def list_entries(
        self, url: str, *, max_entries: int, timeout_s: float
    ) -> list[YoutubeEntry]:
        """Flat-extract a playlist/channel into its video entries."""
        return await asyncio.to_thread(
            self._list_entries_sync, url, max_entries, timeout_s
        )

    # -- sync internals (worker thread) -----------------------------------

    def _fetch_captions_sync(
        self,
        url: str,
        *,
        langs: tuple[str, ...],
        allow_auto: bool,
        timeout_s: float,
    ) -> VideoCaptionsResult:
        import yt_dlp  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": timeout_s,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                track = self._select_and_download_track(
                    ydl, info, langs=langs, allow_auto=allow_auto
                )
        except Exception as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        return VideoCaptionsResult(
            video_id=info.get("id"), title=info.get("title"), track=track
        )

    def _select_and_download_track(
        self, ydl: object, info: dict, *, langs: tuple[str, ...], allow_auto: bool
    ) -> CaptionTrack | None:
        pools: list[tuple[dict, bool]] = [(info.get("subtitles") or {}, False)]
        if allow_auto:
            pools.append((info.get("automatic_captions") or {}, True))
        for pool, is_automatic in pools:
            for lang in _preferred_langs(list(pool), langs):
                if (track := self._download_track(ydl, pool[lang])) is not None:
                    return CaptionTrack(
                        language=lang,
                        is_automatic=is_automatic,
                        fmt=track[0],
                        content=track[1],
                    )
        return None

    def _download_track(
        self, ydl: object, formats: list[dict]
    ) -> tuple[Literal["json3", "vtt"], str] | None:
        by_ext = {entry.get("ext"): entry for entry in formats if entry.get("url")}
        fmts: tuple[Literal["json3", "vtt"], ...] = ("json3", "vtt")
        for fmt in fmts:
            if (entry := by_ext.get(fmt)) is None:
                continue
            try:
                content = ydl.urlopen(entry["url"]).read().decode("utf-8")  # ty: ignore[unresolved-attribute]  # pyrefly: ignore[missing-attribute]
            except Exception:  # noqa: BLE001 — try the next caption format
                logger.warning("caption track download failed", exc_info=True)
                continue
            return fmt, content
        return None

    def _download_audio_sync(self, url: str, dest_dir: Path, timeout_s: float) -> Path:
        import yt_dlp  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
            "socket_timeout": timeout_s,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = Path(ydl.prepare_filename(info))
        except Exception as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        if not path.is_file():
            raise FetchFailedError(url=url, detail="audio download produced no file")
        return path

    def _list_entries_sync(
        self, url: str, max_entries: int, timeout_s: float
    ) -> list[YoutubeEntry]:
        import yt_dlp  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "playlistend": max_entries,
            "socket_timeout": timeout_s,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        raw_entries = info.get("entries") or ([info] if info.get("id") else [])
        entries: list[YoutubeEntry] = []
        for raw in raw_entries:
            video_id = raw.get("id")
            if not isinstance(video_id, str) or not _VIDEO_ID.match(video_id):
                continue  # channel-tab sub-playlists and other non-video rows
            entries.append(
                YoutubeEntry(
                    video_id=video_id,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    title=raw.get("title") or None,
                    published_at=_entry_timestamp(raw),
                )
            )
        return entries


def _entry_timestamp(raw: dict) -> datetime | None:
    timestamp = raw.get("timestamp") or raw.get("release_timestamp")
    if not isinstance(timestamp, (int, float)):
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC)
