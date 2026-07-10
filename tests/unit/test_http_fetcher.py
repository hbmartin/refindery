"""HttpFetcher tests over a mocked httpx transport (pytest-httpx)."""

import httpx
import pytest

from refindery.adapters.extraction.http_fetcher import HttpFetcher
from refindery.domain.errors import FetchFailedError

URL = "https://example.test/page"


async def test_success_maps_and_normalizes(httpx_mock):
    httpx_mock.add_response(
        url=URL,
        content=b"<html>hi</html>",
        headers={"content-type": "Text/HTML; charset=UTF-8"},
    )
    result = await HttpFetcher().fetch(URL)
    assert result.url == URL
    assert result.final_url == URL
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.charset is not None
    assert result.charset.lower() == "utf-8"
    assert result.body == b"<html>hi</html>"
    request = httpx_mock.get_request()
    assert request.headers["user-agent"] == "refindery/0.1"


async def test_follows_redirects_and_reports_final_url(httpx_mock):
    httpx_mock.add_response(
        url=URL, status_code=301, headers={"location": "https://example.test/new"}
    )
    httpx_mock.add_response(
        url="https://example.test/new",
        content=b"moved",
        headers={"content-type": "text/plain"},
    )
    result = await HttpFetcher().fetch(URL)
    assert result.final_url == "https://example.test/new"
    assert result.body == b"moved"


async def test_redirect_loop_maps_to_fetch_failed(httpx_mock):
    httpx_mock.add_response(
        url=URL,
        status_code=302,
        headers={"location": URL},
        is_reusable=True,
    )
    with pytest.raises(FetchFailedError, match="TooManyRedirects"):
        await HttpFetcher().fetch(URL)


async def test_non_2xx_maps_to_fetch_failed(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=404)
    with pytest.raises(FetchFailedError, match="404"):
        await HttpFetcher().fetch(URL)


async def test_network_error_maps_to_fetch_failed(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(FetchFailedError, match="connection refused"):
        await HttpFetcher().fetch(URL)


async def test_timeout_maps_to_fetch_failed(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    with pytest.raises(FetchFailedError, match="ReadTimeout"):
        await HttpFetcher().fetch(URL)


async def test_streaming_size_cap(httpx_mock):
    httpx_mock.add_response(url=URL, content=b"x" * 11)
    with pytest.raises(FetchFailedError, match="body exceeds 10 bytes"):
        await HttpFetcher(max_bytes=10).fetch(URL)


async def test_pydantic_cap_maps_to_fetch_failed(httpx_mock):
    # Larger than FetchResult's hard MAX_FETCH_BYTES but under the fetcher's
    # own cap, so the pydantic validator is the one that trips.
    httpx_mock.add_response(url=URL, content=b"x" * 10_000_001)
    with pytest.raises(FetchFailedError, match="exceeds 10000000 bytes"):
        await HttpFetcher(max_bytes=20_000_000).fetch(URL)
