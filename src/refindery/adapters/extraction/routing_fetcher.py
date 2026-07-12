"""Fetcher router: YouTube video URLs get the caption fetcher, rest HTTP.

Playlist/channel URLs deliberately fall through to plain HTTP — feeding a
listing page to caption fetch is nonsense; those belong to /v1/watches.
"""

from refindery.application.ports.content_extractor import Fetcher, FetchResult
from refindery.domain.youtube import is_youtube_video_url


class RoutingFetcher:
    """Dispatches fetches by URL shape."""

    def __init__(self, *, default: Fetcher, youtube: Fetcher | None) -> None:
        self._default = default
        self._youtube = youtube

    async def fetch(self, url: str) -> FetchResult:
        """Route a fetch to the YouTube caption fetcher or the default."""
        if self._youtube is not None and is_youtube_video_url(url):
            return await self._youtube.fetch(url)
        return await self._default.fetch(url)
