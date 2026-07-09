"""Fetcher/extractor fakes: no network, no torch."""

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
