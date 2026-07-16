"""Fetcher that routes YouTube URLs to a caption/transcript fetcher.

YouTube URLs go to the yt-dlp-backed fetcher; everything else keeps using the
default HTTP fetcher. Implements the ``Fetcher`` port, so ``IndexingService``
is unaware of the routing.
"""

from refindery.application.ports.content_extractor import Fetcher, FetchResult
from refindery.domain.youtube import is_youtube_url


class RoutingFetcher:
    """Dispatches by URL: YouTube -> caption fetcher, else the default."""

    def __init__(self, *, default: Fetcher, youtube: Fetcher) -> None:
        self._default = default
        self._youtube = youtube

    async def fetch(self, url: str) -> FetchResult:
        """Route ``url`` to the YouTube fetcher when it is a YouTube URL."""
        if is_youtube_url(url):
            return await self._youtube.fetch(url)
        return await self._default.fetch(url)
