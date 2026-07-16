"""Fetcher/extractor fakes: no network, no torch."""

from collections.abc import Sequence
from pathlib import Path

from refindery.adapters.extraction.youtube_envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    TranscriptSource,
    YoutubeTranscriptEnvelope,
)
from refindery.adapters.extraction.youtube_fetcher import _AudioResult, _CaptionData
from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.errors import FetchFailedError
from refindery.domain.models import ExtractedContent


class FakeFetcher:
    """Returns preset responses keyed by URL."""

    def __init__(self, responses: dict[str, FetchResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        """Return the preset response or fail like a network error."""
        self.calls.append(url)
        if (result := self.responses.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake response configured")
        return result


class FakeHtmlExtractor:
    """Strips angle brackets — close enough to markdown for tests."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({"text/html"})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:
        """Very crude tag removal; deterministic."""
        text = raw.decode(charset or "utf-8", errors="replace")
        out: list[str] = []
        in_tag = False
        for ch in text:
            if ch == "<":
                in_tag = True
            elif ch == ">":
                in_tag = False
            elif not in_tag:
                out.append(ch)
        return ExtractedContent(body_text="".join(out).strip())


def youtube_fetch_result(
    url: str,
    *,
    title: str | None,
    transcript: str,
    source: TranscriptSource = TranscriptSource.MANUAL_CAPTIONS,
    video_id: str | None = None,
    language: str | None = "en",
) -> FetchResult:
    """Build a YouTube transcript envelope FetchResult for indexing tests."""
    envelope = YoutubeTranscriptEnvelope(
        video_id=video_id,
        title=title,
        language=language,
        source=source,
        transcript=transcript,
        source_url=url,
    )
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
        charset="utf-8",
        body=envelope.model_dump_json().encode("utf-8"),
    )


class FakeYtDlpBackend:
    """In-memory yt-dlp backend for YoutubeCaptionFetcher unit tests."""

    def __init__(
        self,
        *,
        captions: _CaptionData | None = None,
        audio: _AudioResult | None = None,
    ) -> None:
        self._captions = captions
        self._audio = audio
        self.caption_calls: list[tuple[str, tuple[str, ...], bool]] = []
        self.audio_calls: list[str] = []

    def fetch_captions(
        self, url: str, *, langs: Sequence[str], allow_auto: bool, timeout_s: float
    ) -> _CaptionData | None:
        """Record the call and return the preset captions."""
        _ = timeout_s
        self.caption_calls.append((url, tuple(langs), allow_auto))
        return self._captions

    def download_audio(
        self, url: str, *, dest_dir: Path, timeout_s: float
    ) -> _AudioResult:
        """Record the call and return the preset audio result."""
        _ = (dest_dir, timeout_s)
        self.audio_calls.append(url)
        if self._audio is None:
            raise FetchFailedError(url=url, detail="fake: no audio configured")
        return self._audio


class FakeTranscriber:
    """Returns a preset transcript regardless of the audio contents."""

    def __init__(self, transcript: str = "") -> None:
        self._transcript = transcript
        self.calls: list[Path] = []

    async def transcribe(self, audio: Path, *, language: str | None = None) -> str:
        """Record the call and return the preset transcript."""
        _ = language
        self.calls.append(audio)
        return self._transcript
