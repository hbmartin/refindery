"""Watch source port: enumerate the current child items of a watched URL.

Sources own their I/O (an RSS source fetches bytes and parses them; a
yt-dlp-backed source drives its own extraction), so the port is a single
``discover`` call rather than a raw-bytes parser.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator


class WatchItem(BaseModel):
    """One discovered child URL; validates parser/backend output.

    Podcast feeds additionally surface the episode's audio enclosure and any
    Podcasting 2.0 ``<podcast:transcript>`` / ``<podcast:chapters>`` links, which
    the watch fan-out threads into ingest so the transcript is chapter-chunked.
    """

    model_config = ConfigDict(frozen=True)

    url: str
    title: str | None = None
    published_at: datetime | None = None
    enclosure_url: str | None = None
    transcript_url: str | None = None
    transcript_type: str | None = None
    chapters_url: str | None = None
    description: str | None = None

    @field_validator("url")
    @classmethod
    def _absolute_http(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            msg = f"not an absolute http(s) URL: {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("enclosure_url", "transcript_url", "chapters_url")
    @classmethod
    def _optional_absolute_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            msg = f"not an absolute http(s) URL: {value!r}"
            raise ValueError(msg)
        return value


class WatchSource(Protocol):
    """Enumerates the current items of one watch kind."""

    async def discover(self, *, url: str, config: Mapping[str, str]) -> list[WatchItem]:
        """Return current child items; raises FetchFailedError on I/O failure."""
        ...
