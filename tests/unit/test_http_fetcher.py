"""HttpFetcher tests over a mocked httpx transport (pytest-httpx)."""

from ipaddress import IPv4Address

import httpx
import pytest
from pytest_httpx import HTTPXMock

from refindery.adapters.extraction.http_fetcher import HttpFetcher
from refindery.domain.errors import FetchFailedError

URL = "https://example.test/page"
PUBLIC_IP = IPv4Address("1.1.1.1")
PINNED_URL = "https://1.1.1.1/page"


async def _public_resolver(*, host: str, port: int) -> tuple[IPv4Address, ...]:
    assert host == "example.test"
    assert port == 443
    return (PUBLIC_IP,)


def _fetcher(*, max_bytes: int = 10_000_000) -> HttpFetcher:
    return HttpFetcher(max_bytes=max_bytes, resolver=_public_resolver)


async def test_success_maps_and_normalizes(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=PINNED_URL,
        content=b"<html>hi</html>",
        headers={"content-type": "Text/HTML; charset=UTF-8"},
    )
    result = await _fetcher().fetch(URL)
    assert result.url == URL
    assert result.final_url == URL
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.charset is not None
    assert result.charset.lower() == "utf-8"
    assert result.body == b"<html>hi</html>"
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["user-agent"] == "refindery/0.1"
    assert request.headers["host"] == "example.test"
    assert request.extensions["sni_hostname"] == "example.test"


async def test_follows_redirects_and_reports_final_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=PINNED_URL,
        status_code=301,
        headers={"location": "https://example.test/new"},
    )
    httpx_mock.add_response(
        url="https://1.1.1.1/new",
        content=b"moved",
        headers={"content-type": "text/plain"},
    )
    result = await _fetcher().fetch(URL)
    assert result.final_url == "https://example.test/new"
    assert result.body == b"moved"


async def test_redirect_loop_maps_to_fetch_failed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=PINNED_URL,
        status_code=302,
        headers={"location": URL},
        is_reusable=True,
    )
    with pytest.raises(FetchFailedError, match="TooManyRedirects"):
        await _fetcher().fetch(URL)


async def test_non_2xx_maps_to_fetch_failed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=PINNED_URL, status_code=404)
    with pytest.raises(FetchFailedError, match="404"):
        await _fetcher().fetch(URL)


async def test_network_error_maps_to_fetch_failed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=PINNED_URL)
    with pytest.raises(FetchFailedError, match="connection refused"):
        await _fetcher().fetch(URL)


async def test_timeout_maps_to_fetch_failed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=PINNED_URL)
    with pytest.raises(FetchFailedError, match="ReadTimeout"):
        await _fetcher().fetch(URL)


async def test_streaming_size_cap(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=PINNED_URL, content=b"x" * 11)
    with pytest.raises(FetchFailedError, match="body exceeds 10 bytes"):
        await _fetcher(max_bytes=10).fetch(URL)


async def test_pydantic_cap_maps_to_fetch_failed(httpx_mock: HTTPXMock) -> None:
    # Larger than FetchResult's hard MAX_FETCH_BYTES but under the fetcher's
    # own cap, so the pydantic validator is the one that trips.
    httpx_mock.add_response(url=PINNED_URL, content=b"x" * 10_000_001)
    with pytest.raises(FetchFailedError, match="exceeds 10000000 bytes"):
        await _fetcher(max_bytes=20_000_000).fetch(URL)


async def test_private_destination_is_rejected_before_request(
    httpx_mock: HTTPXMock,
) -> None:
    async def private_resolver(*, host: str, port: int) -> tuple[IPv4Address, ...]:
        assert host == "example.test"
        assert port == 443
        return (IPv4Address("127.0.0.1"),)

    with pytest.raises(FetchFailedError, match=r"non-public.*127\.0\.0\.1"):
        await HttpFetcher(resolver=private_resolver).fetch(URL)
    assert httpx_mock.get_requests() == []


async def test_mixed_public_and_private_dns_is_rejected(
    httpx_mock: HTTPXMock,
) -> None:
    async def mixed_resolver(*, host: str, port: int) -> tuple[IPv4Address, ...]:
        assert host == "example.test"
        assert port == 443
        return (PUBLIC_IP, IPv4Address("10.0.0.1"))

    with pytest.raises(FetchFailedError, match=r"non-public.*10\.0\.0\.1"):
        await HttpFetcher(resolver=mixed_resolver).fetch(URL)
    assert httpx_mock.get_requests() == []


async def test_redirect_target_is_validated_before_request(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=PINNED_URL,
        status_code=302,
        headers={"location": "http://127.0.0.1/admin"},
    )

    async def resolver(*, host: str, port: int) -> tuple[IPv4Address, ...]:
        if host == "example.test":
            assert port == 443
            return (PUBLIC_IP,)
        assert host == "127.0.0.1"
        assert port == 80
        return (IPv4Address(host),)

    with pytest.raises(FetchFailedError, match=r"non-public.*127\.0\.0\.1"):
        await HttpFetcher(resolver=resolver).fetch(URL)
    assert len(httpx_mock.get_requests()) == 1


async def test_same_host_redirect_is_reresolved_to_block_rebinding(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=PINNED_URL,
        status_code=302,
        headers={"location": "/next"},
    )
    calls = 0

    async def rebinding_resolver(*, host: str, port: int) -> tuple[IPv4Address, ...]:
        nonlocal calls
        assert host == "example.test"
        assert port == 443
        calls += 1
        return (PUBLIC_IP,) if calls == 1 else (IPv4Address("169.254.169.254"),)

    with pytest.raises(FetchFailedError, match=r"non-public.*169\.254\.169\.254"):
        await HttpFetcher(resolver=rebinding_resolver).fetch(URL)
    assert calls == 2
    assert len(httpx_mock.get_requests()) == 1
