"""Feed-parser port: fetched bytes of a list resource -> child item URLs.

A ``FeedItem`` is a pydantic model because it wraps parser output derived
from an external, frequently-malformed source (a third-party feed) that must
be validated at the trust boundary. One port covers every fan-out watch kind
(RSS now; sitemap / index-page diff later reuse the same contract).
"""

from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator


class FeedItem(BaseModel):
    """One discovered item: a URL plus optional title and publish time."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str | None = None
    published_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            msg = "item url must be an absolute http(s) URL"
            raise ValueError(msg)
        return value


class FeedParser(Protocol):
    """Parses a fetched list resource into the item URLs it references."""

    async def parse(self, *, raw: bytes, base_url: str) -> list[FeedItem]:
        """Return the items in ``raw``; ``base_url`` resolves relative links."""
        ...
