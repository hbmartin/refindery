"""Feed-parser fake: returns preset items without parsing any bytes."""

from refindery.application.ports.feed_parser import FeedItem


class FakeFeedParser:
    """Returns a preset item list and records the base URLs it was called with."""

    def __init__(self, items: list[FeedItem] | None = None) -> None:
        self.items = items or []
        self.calls: list[str] = []

    async def parse(self, *, raw: bytes, base_url: str) -> list[FeedItem]:  # noqa: ARG002 — fake ignores raw bytes
        """Return the preset items regardless of ``raw``."""
        self.calls.append(base_url)
        return list(self.items)
