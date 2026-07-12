"""Watch source fake: preset items keyed by URL."""

from collections.abc import Mapping

from refindery.application.ports.watch_source import WatchItem
from refindery.domain.errors import FetchFailedError


class FakeWatchSource:
    """Returns preset item lists keyed by watch URL."""

    def __init__(self, items: dict[str, list[WatchItem]] | None = None) -> None:
        self.items = items or {}
        self.calls: list[str] = []

    async def discover(
        self,
        *,
        url: str,
        config: Mapping[str, str],  # noqa: ARG002 — port signature
    ) -> list[WatchItem]:
        """Return preset items or fail like a network error."""
        self.calls.append(url)
        if (items := self.items.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake items configured")
        return items
