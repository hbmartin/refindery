"""RSS/Atom watch source backed by feedparser.

feedparser tolerates the malformed XML that real feeds ship (degrading to an
empty entry list instead of raising), which is why it is used over stdlib
``xml.etree`` + defusedxml.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from time import struct_time
from urllib.parse import urljoin

import feedparser
from pydantic import ValidationError

from refindery.application.ports.content_extractor import Fetcher
from refindery.application.ports.watch_source import WatchItem

logger = logging.getLogger(__name__)


def _entry_published(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not isinstance(parsed, struct_time):
        return None
    return datetime(*parsed[:6], tzinfo=UTC)


def parse_feed(*, raw: bytes, base_url: str) -> list[WatchItem]:
    """Parse feed bytes into items; invalid entries are dropped, never fatal."""
    parsed = feedparser.parse(raw)
    items: list[WatchItem] = []
    for entry in parsed.entries:
        link = entry.get("link")
        if not isinstance(link, str) or not link:
            continue
        title = entry.get("title")
        try:
            item = WatchItem(
                url=urljoin(base_url, link),
                title=title if isinstance(title, str) and title else None,
                published_at=_entry_published(entry),
            )
        except ValidationError:
            logger.warning("dropping invalid feed entry %r from %s", link, base_url)
            continue
        items.append(item)
    return items


class RssWatchSource:
    """Fetches a feed via the Fetcher port and parses out its entry URLs."""

    def __init__(self, *, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    async def discover(
        self,
        *,
        url: str,
        config: Mapping[str, str],  # noqa: ARG002 — port signature; RSS has no per-watch options
    ) -> list[WatchItem]:
        """Fetch and parse the feed; raises FetchFailedError on fetch failure."""
        result = await self._fetcher.fetch(url)
        return await asyncio.to_thread(
            lambda: parse_feed(raw=result.body, base_url=result.final_url)
        )
