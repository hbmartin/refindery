"""Fetcher router: YouTube videos get captions, audio gets Whisper, rest HTTP.

Playlist/channel URLs deliberately fall through to plain HTTP — feeding a
listing page to caption fetch is nonsense; those belong to /v1/watches. The
automatic audio route matches by file extension; podcast watches persist an
explicit audio route so MIME-identified enclosures without extensions still
resolve to transcripts.
"""

from refindery.application.ports.content_extractor import (
    Fetcher,
    FetchResult,
    FetchRoute,
)
from refindery.domain.audio import is_audio_url
from refindery.domain.errors import FetchFailedError
from refindery.domain.youtube import is_youtube_video_url


class RoutingFetcher:
    """Dispatches fetches by URL shape."""

    def __init__(
        self,
        *,
        default: Fetcher,
        youtube: Fetcher | None = None,
        audio: Fetcher | None = None,
    ) -> None:
        self._default = default
        self._youtube = youtube
        self._audio = audio

    async def fetch(self, url: str) -> FetchResult:
        """Route a fetch to the YouTube, audio, or default fetcher."""
        if self._youtube is not None and is_youtube_video_url(url):
            return await self._youtube.fetch(url)
        if self._audio is not None and is_audio_url(url):
            return await self._audio.fetch(url)
        return await self._default.fetch(url)

    async def fetch_routed(self, url: str, *, route: FetchRoute) -> FetchResult:
        """Route a fetch explicitly when source metadata is authoritative."""
        match route:
            case FetchRoute.AUTO:
                return await self.fetch(url)
            case FetchRoute.AUDIO if self._audio is not None:
                return await self._audio.fetch(url)
            case FetchRoute.AUDIO:
                raise FetchFailedError(
                    url=url, detail="audio fetch route is unavailable"
                )
