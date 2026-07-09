"""Content extraction ports: fetching a URL and extracting body text.

``FetchResult`` is a pydantic model because it wraps a raw HTTP response —
an external input that must be validated (size caps, content type parsing).
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from refindery.domain.models import ExtractedContent

MAX_FETCH_BYTES = 10_000_000


class FetchResult(BaseModel):
    """Validated result of fetching a URL."""

    model_config = ConfigDict(frozen=True)

    url: str
    final_url: str
    status_code: int
    content_type: str
    charset: str | None
    body: bytes

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return value.split(";", maxsplit=1)[0].strip().lower()

    @field_validator("body")
    @classmethod
    def _cap_size(cls, value: bytes) -> bytes:
        if len(value) > MAX_FETCH_BYTES:
            msg = f"fetched body exceeds {MAX_FETCH_BYTES} bytes"
            raise ValueError(msg)
        return value


class Fetcher(Protocol):
    """Fetches a URL for the fetch_and_index path."""

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url``; raises on network errors and non-2xx statuses."""
        ...


class ContentExtractor(Protocol):
    """Extracts markdown body text from one family of content types."""

    @property
    def content_types(self) -> frozenset[str]:
        """Lowercase content types this extractor handles (e.g. text/html)."""
        ...

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:
        """Extract body text (and title when derivable) from raw bytes."""
        ...
