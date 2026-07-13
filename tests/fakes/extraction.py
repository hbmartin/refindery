"""Fetcher/extractor fakes: no network, no torch."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from refindery.adapters.extraction.http_fetcher import FileFetchResult
from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.errors import FetchFailedError
from refindery.domain.models import ExtractedContent


class FakeFetcher:
    """Returns preset responses keyed by URL."""

    def __init__(self, responses: dict[str, FetchResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        """Return the preset response or fail like a network error."""
        self.calls.append(url)
        if (result := self.responses.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake response configured")
        return result


class FakeFileDownloader:
    """Writes preset (body, content_type) pairs keyed by URL to the dest file."""

    def __init__(self, responses: dict[str, tuple[bytes, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    async def fetch_to_file(
        self,
        url: str,
        *,
        dest: Path,
        accept: Callable[[str], bool] | None = None,
    ) -> FileFetchResult:
        """Mimic HttpFetcher.fetch_to_file: content-type vetted before writing."""
        self.calls.append(url)
        if (response := self.responses.get(url)) is None:
            raise FetchFailedError(url=url, detail="no fake response configured")
        body, content_type = response
        if accept is not None and not accept(content_type):
            raise FetchFailedError(
                url=url, detail=f"unexpected content type {content_type!r}"
            )
        await asyncio.to_thread(dest.write_bytes, body)
        return FileFetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type=content_type,
            path=dest,
            size_bytes=len(body),
        )


class FakeHtmlExtractor:
    """Strips angle brackets — close enough to markdown for tests."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({"text/html"})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:
        """Very crude tag removal; deterministic."""
        text = raw.decode(charset or "utf-8", errors="replace")
        out: list[str] = []
        in_tag = False
        for ch in text:
            if ch == "<":
                in_tag = True
            elif ch == ">":
                in_tag = False
            elif not in_tag:
                out.append(ch)
        return ExtractedContent(body_text="".join(out).strip())
