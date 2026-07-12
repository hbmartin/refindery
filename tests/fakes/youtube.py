"""YouTube backend/transcriber fakes: preset results keyed by URL."""

from pathlib import Path

from refindery.adapters.youtube.backend import (
    VideoCaptionsResult,
    YoutubeEntry,
)
from refindery.domain.errors import FetchFailedError


class FakeYoutubeBackend:
    """Preset caption probes, playlist entries, and audio bytes, keyed by URL."""

    def __init__(
        self,
        *,
        captions: dict[str, VideoCaptionsResult] | None = None,
        entries: dict[str, list[YoutubeEntry]] | None = None,
        audio: dict[str, bytes] | None = None,
    ) -> None:
        self.captions = captions or {}
        self.entries = entries or {}
        self.audio = audio or {}
        self.calls: list[tuple[str, str]] = []

    async def fetch_captions(
        self,
        url: str,
        *,
        langs: tuple[str, ...],  # noqa: ARG002 — port signature
        allow_auto: bool,  # noqa: ARG002 — port signature
        timeout_s: float,  # noqa: ARG002 — port signature
    ) -> VideoCaptionsResult:
        """Return the preset probe or fail like a network error."""
        self.calls.append(("fetch_captions", url))
        if (result := self.captions.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake captions configured")
        return result

    async def download_audio(
        self,
        url: str,
        *,
        dest_dir: Path,
        timeout_s: float,  # noqa: ARG002 — port signature
    ) -> Path:
        """Write preset audio bytes into dest_dir or fail."""
        self.calls.append(("download_audio", url))
        if (payload := self.audio.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake audio configured")
        path = dest_dir / "audio.m4a"
        path.write_bytes(payload)
        return path

    async def list_entries(
        self,
        url: str,
        *,
        max_entries: int,
        timeout_s: float,  # noqa: ARG002 — port signature
    ) -> list[YoutubeEntry]:
        """Return preset entries (capped) or fail like a network error."""
        self.calls.append(("list_entries", url))
        if (entries := self.entries.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake entries configured")
        return entries[:max_entries]


class FakeTranscriber:
    """Returns fixed text; records the audio paths it was given."""

    def __init__(self, text: str = "fake transcript from audio") -> None:
        self.text = text
        self.calls: list[Path] = []

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,  # noqa: ARG002 — port signature
    ) -> str:
        """Return the fixed transcript."""
        self.calls.append(audio_path)
        return self.text
