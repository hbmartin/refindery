"""Watch source that enumerates a YouTube playlist or channel via yt-dlp."""

from collections.abc import Mapping

from refindery.adapters.youtube.backend import YoutubeBackend
from refindery.application.ports.watch_source import WatchItem
from refindery.domain.youtube import normalize_listing_url


class YoutubeWatchSource:
    """Flat-extracts a playlist/channel into its current video URLs."""

    def __init__(
        self, *, backend: YoutubeBackend, max_entries: int, timeout_s: float
    ) -> None:
        self._backend = backend
        self._max_entries = max_entries
        self._timeout_s = timeout_s

    async def discover(self, *, url: str, config: Mapping[str, str]) -> list[WatchItem]:
        """List current videos; ``config["max_entries"]`` overrides the cap."""
        target = normalize_listing_url(url)
        max_entries = int(config.get("max_entries", self._max_entries))
        entries = await self._backend.list_entries(
            target, max_entries=max_entries, timeout_s=self._timeout_s
        )
        return [
            WatchItem(url=entry.url, title=entry.title, published_at=entry.published_at)
            for entry in entries
        ]
