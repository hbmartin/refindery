"""Podcast watch source: RSS/Atom entries' audio enclosures, via feedparser.

A podcast feed is ordinary RSS whose items carry ``<enclosure>`` audio URLs.
Each discovered item points at the episode's audio file (not the episode
webpage), so the fetch router hands it to the audio transcript fetcher.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import cast
from urllib.parse import urljoin

import feedparser
from pydantic import ValidationError

from refindery.adapters.feeds.rss_feedparser import entry_published
from refindery.application.ports.content_extractor import Fetcher
from refindery.application.ports.watch_source import WatchItem
from refindery.domain.audio import is_audio_url

logger = logging.getLogger(__name__)


def _enclosure_candidates(entry: feedparser.FeedParserDict) -> list[dict[str, object]]:
    enclosures = entry.get("enclosures")
    links = entry.get("links")
    candidates: list[object] = list(enclosures) if isinstance(enclosures, list) else []
    if isinstance(links, list):
        candidates.extend(
            link
            for link in links
            if isinstance(link, dict) and link.get("rel") == "enclosure"
        )
    return [
        cast("dict[str, object]", item) for item in candidates if isinstance(item, dict)
    ]


def _audio_enclosure(entry: feedparser.FeedParserDict) -> str | None:
    """First enclosure href typed ``audio/*``, or untyped with an audio path."""
    for candidate in _enclosure_candidates(entry):
        href = candidate.get("href") or candidate.get("url")
        if not isinstance(href, str) or not href:
            continue
        content_type = candidate.get("type")
        if isinstance(content_type, str) and content_type.strip():
            if content_type.strip().lower().startswith("audio/"):
                return href
        elif is_audio_url(href):
            return href
    return None


def parse_podcast_feed(*, raw: bytes, base_url: str) -> list[WatchItem]:
    """Parse feed bytes into audio items; invalid entries are dropped, never fatal."""
    parsed = feedparser.parse(raw)
    items: list[WatchItem] = []
    for entry in parsed.entries:
        href = _audio_enclosure(entry)
        if href is None:
            continue
        title = entry.get("title")
        try:
            item = WatchItem(
                url=urljoin(base_url, href),
                title=title if isinstance(title, str) and title else None,
                published_at=entry_published(entry),
            )
        except (ValidationError, ValueError):
            logger.warning("dropping invalid feed enclosure %r from %s", href, base_url)
            continue
        items.append(item)
    return items


class PodcastWatchSource:
    """Fetches a feed via the Fetcher port and parses out its audio enclosures."""

    def __init__(self, *, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    async def discover(
        self,
        *,
        url: str,
        config: Mapping[str, str],  # noqa: ARG002 — port signature; podcasts have no per-watch options
    ) -> list[WatchItem]:
        """Fetch and parse the feed; raises FetchFailedError on fetch failure."""
        result = await self._fetcher.fetch(url)
        return await asyncio.to_thread(
            lambda: parse_podcast_feed(raw=result.body, base_url=result.final_url)
        )
