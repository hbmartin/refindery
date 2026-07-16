"""RSS/Atom feed parser built on feedparser.

feedparser handles RSS 0.9x/1.0/2.0 and Atom uniformly and degrades to an
empty entry list on malformed input instead of raising, so a broken feed
yields no items rather than failing the poll. It also disables external
entity expansion, unlike a naive ``xml.etree`` parse of an untrusted feed.
"""

import asyncio
from datetime import UTC, datetime
from time import struct_time
from urllib.parse import urljoin

import feedparser  # pyrefly: ignore[missing-import]
from pydantic import ValidationError

from refindery.application.ports.feed_parser import FeedItem


def _published_at(entry: object) -> datetime | None:
    """Map feedparser's parsed time struct (UTC) to an aware datetime."""
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if not isinstance(parsed, struct_time):
        return None
    return datetime(*parsed[:6], tzinfo=UTC)


class RssFeedParser:
    """FeedParser for RSS and Atom feeds."""

    async def parse(self, *, raw: bytes, base_url: str) -> list[FeedItem]:
        """Parse feed bytes into items; one bad entry never aborts the feed."""
        parsed = await asyncio.to_thread(feedparser.parse, raw)
        items: list[FeedItem] = []
        for entry in parsed.entries:
            link = entry.get("link")
            if not isinstance(link, str) or not link:
                continue
            title = entry.get("title")
            try:
                items.append(
                    FeedItem(
                        url=urljoin(base_url, link),
                        title=title if isinstance(title, str) else None,
                        published_at=_published_at(entry),
                    )
                )
            except ValidationError:
                continue
        return items
