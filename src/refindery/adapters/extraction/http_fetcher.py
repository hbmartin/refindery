"""httpx-based fetcher for the fetch_and_index path.

The raw response is an external input: it is validated into the pydantic
``FetchResult`` model (size cap, content-type normalization) before anything
downstream touches it.
"""

import httpx
from pydantic import ValidationError

from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.errors import FetchFailedError


class HttpFetcher:
    """Fetcher port implementation."""

    def __init__(self, *, timeout_s: float = 10.0, max_bytes: int = 10_000_000) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url``; raise FetchFailedError on any failure."""
        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=self._timeout_s,
                    headers={"User-Agent": "refindery/0.1"},
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise FetchFailedError(
                            url=url,
                            detail=f"body exceeds {self._max_bytes} bytes",
                        )
                result = FetchResult(
                    url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    content_type=response.headers.get(
                        "content-type", "application/octet-stream"
                    ),
                    charset=response.charset_encoding,
                    body=bytes(body),
                )
        except httpx.HTTPError as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc
        except ValidationError as exc:
            raise FetchFailedError(url=url, detail=str(exc)) from exc
        return result
