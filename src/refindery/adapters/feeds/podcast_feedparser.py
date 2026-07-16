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
from refindery.domain.audio import is_audio_content_type, is_audio_url

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
    """First audio/generic typed href, or untyped href with an audio path."""
    for candidate in _enclosure_candidates(entry):
        href = candidate.get("href") or candidate.get("url")
        if not isinstance(href, str) or not href:
            continue
        content_type = candidate.get("type")
        if isinstance(content_type, str) and content_type.strip():
            if is_audio_content_type(content_type):
                return href
        elif is_audio_url(href):
            return href
    return None


def _namespaced_value(
    entry: feedparser.FeedParserDict, key: str
) -> tuple[object, str | None]:
    """Return a Podcasting 2.0 element's URL-like value and MIME type."""
    value = entry.get(key)
    if not isinstance(value, Mapping):
        return None, None
    raw_url = value.get("url") or value.get("href")
    raw_type = value.get("type")
    return raw_url, raw_type if isinstance(raw_type, str) else None


def parse_podcast_feed(*, raw: bytes, base_url: str) -> list[WatchItem]:
    """Parse feed bytes into audio items; invalid entries are dropped, never fatal."""
    parsed = feedparser.parse(raw)
    items: list[WatchItem] = []
    for entry in parsed.entries:
        enclosure = _audio_enclosure(entry)
        transcript_raw, transcript_type = _namespaced_value(entry, "podcast_transcript")
        transcript_url = (
            urljoin(base_url, transcript_raw)
            if isinstance(transcript_raw, str) and transcript_raw
            else None
        )
        chapters_raw, _chapters_type = _namespaced_value(entry, "podcast_chapters")
        chapters_url = (
            urljoin(base_url, chapters_raw)
            if isinstance(chapters_raw, str) and chapters_raw
            else None
        )
        episode_link = entry.get("link")
        target = enclosure or (
            episode_link
            if transcript_url is not None and isinstance(episode_link, str)
            else None
        )
        if target is None:
            continue
        title = entry.get("title")
        summary = entry.get("summary")
        try:
            item = WatchItem(
                url=urljoin(base_url, target),
                title=title if isinstance(title, str) and title else None,
                published_at=entry_published(entry),
                enclosure_url=(
                    urljoin(base_url, enclosure) if enclosure is not None else None
                ),
                transcript_url=transcript_url,
                transcript_type=transcript_type if transcript_url is not None else None,
                chapters_url=chapters_url,
                description=(
                    summary
                    if transcript_url is not None and isinstance(summary, str)
                    else None
                ),
            )
        except (ValidationError, ValueError):
            logger.warning(
                "dropping invalid podcast entry %r from %s", target, base_url
            )
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
