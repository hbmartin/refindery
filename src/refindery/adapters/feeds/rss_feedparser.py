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
from urllib.parse import urljoin, urlsplit

import feedparser
from pydantic import ValidationError

from refindery.application.ports.content_extractor import Fetcher
from refindery.application.ports.watch_source import WatchItem

logger = logging.getLogger(__name__)


def _entry_published(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not isinstance(parsed, struct_time):
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (OverflowError, ValueError):
        return None


def _http_url(value: object, *, base_url: str) -> str | None:
    """Resolve ``value`` against ``base_url``; return None unless it is http(s)."""
    if not isinstance(value, str) or not value:
        return None
    resolved = urljoin(base_url, value)
    parts = urlsplit(resolved)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return resolved


def _enclosure_url(entry: feedparser.FeedParserDict, *, base_url: str) -> str | None:
    """Return the episode's audio enclosure href, if any (audio types first)."""
    enclosures = [
        enc for enc in (entry.get("enclosures") or []) if isinstance(enc, Mapping)
    ]
    audio = [enc for enc in enclosures if str(enc.get("type", "")).startswith("audio/")]
    for enc in [*audio, *enclosures]:
        if href := _http_url(enc.get("href"), base_url=base_url):
            return href
    return None


def _namespaced(
    entry: feedparser.FeedParserDict, key: str
) -> tuple[object, str | None]:
    """Return (url, type) from a namespaced ``{url,type}`` element, or (None, None)."""
    data = entry.get(key)
    if not isinstance(data, Mapping):
        return None, None
    mime = data.get("type")
    return data.get("url"), mime if isinstance(mime, str) else None


def parse_feed(*, raw: bytes, base_url: str) -> list[WatchItem]:
    """Parse feed bytes into items; invalid entries are dropped, never fatal.

    Podcasting 2.0 ``<podcast:transcript>``/``<podcast:chapters>`` links and the
    audio enclosure are surfaced when present. feedparser keeps only the last
    ``<podcast:transcript>`` per item, so feeds offering several formats expose
    just one here (its ``type`` still drives conversion downstream).
    """
    parsed = feedparser.parse(raw)
    items: list[WatchItem] = []
    for entry in parsed.entries:
        link = entry.get("link")
        if not isinstance(link, str) or not link:
            continue
        title = entry.get("title")
        transcript_raw, transcript_type = _namespaced(entry, "podcast_transcript")
        transcript_url = _http_url(transcript_raw, base_url=base_url)
        chapters_raw, _chapters_type = _namespaced(entry, "podcast_chapters")
        summary = entry.get("summary")
        try:
            item = WatchItem(
                url=urljoin(base_url, link),
                title=title if isinstance(title, str) and title else None,
                published_at=_entry_published(entry),
                enclosure_url=_enclosure_url(entry, base_url=base_url),
                transcript_url=transcript_url,
                transcript_type=transcript_type if transcript_url else None,
                chapters_url=_http_url(chapters_raw, base_url=base_url),
                description=(
                    summary if transcript_url and isinstance(summary, str) else None
                ),
            )
        except (ValidationError, ValueError):
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
