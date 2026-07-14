"""httpx-based fetcher for the fetch_and_index path.

The raw response is an external input: it is validated into the pydantic
``FetchResult`` model (size cap, content-type normalization) before anything
downstream touches it. DNS results are validated and pinned before each
connection so user-controlled URLs cannot reach non-public services through
literal addresses, redirects, or DNS rebinding.

``fetch_to_file`` shares the same pinned transport but streams the body to
disk instead of buffering it, for payloads (podcast audio) far beyond the
in-memory cap; its byte limit is this instance's ``max_bytes``, so audio
callers construct a dedicated instance with audio-sized limits.
"""

import asyncio
import socket
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Annotated, BinaryIO, Protocol
from urllib.parse import urljoin

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    TypeAdapter,
    ValidationError,
)

from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.errors import FetchFailedError

type IPAddress = IPv4Address | IPv6Address
type SocketAddress = tuple[str, int] | tuple[str, int, int, int]
type AddressInfo = tuple[int, int, int, str, SocketAddress]

_ADDRESS_INFO_ADAPTER = TypeAdapter(list[AddressInfo])
_IP_ADDRESSES_ADAPTER = TypeAdapter(
    Annotated[tuple[IPvAnyAddress, ...], Field(min_length=1)]
)
_MAX_REDIRECTS = 20
_TOO_MANY_REDIRECTS = "Exceeded maximum allowed redirects."


class HostResolver(Protocol):
    """Resolves one host and service port to concrete network addresses."""

    async def __call__(self, *, host: str, port: int) -> tuple[IPAddress, ...]:
        """Return every address currently published for the host."""
        ...


@dataclass(frozen=True, slots=True)
class _PinnedTarget:
    """A logical URL mapped to one already-validated connection address."""

    request_url: httpx.URL
    host_header: str
    sni_hostname: str


class FileFetchResult(BaseModel):
    """Outcome of streaming a response body to a file on disk."""

    model_config = ConfigDict(frozen=True)

    url: str
    final_url: str
    status_code: int
    content_type: str
    path: Path
    size_bytes: int


def _normalized_content_type(response: httpx.Response) -> str:
    raw = response.headers.get("content-type", "application/octet-stream")
    return raw.split(";", 1)[0].strip().lower()


def _open_for_writing(dest: Path) -> BinaryIO:
    return dest.open("wb")


async def _system_resolver(*, host: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve a host off-loop and validate the untyped socket result."""
    raw = await asyncio.to_thread(
        socket.getaddrinfo,
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    rows = _ADDRESS_INFO_ADAPTER.validate_python(raw)
    return tuple(dict.fromkeys(ip_address(row[4][0]) for row in rows))


class HttpFetcher:
    """Fetcher port implementation with connection-time SSRF protection."""

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        max_bytes: int = 10_000_000,
        resolver: HostResolver = _system_resolver,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._resolver = resolver

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url``; raise FetchFailedError on any failure."""
        try:
            return await self._fetch(url)
        except FetchFailedError:
            raise
        except (httpx.HTTPError, OSError, ValidationError, ValueError) as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc

    async def fetch_to_file(
        self,
        url: str,
        *,
        dest: Path,
        accept: Callable[[str], bool] | None = None,
    ) -> FileFetchResult:
        """Stream the response body to ``dest``; raise FetchFailedError on failure.

        ``accept`` vets the normalized content type before any body byte is
        read. A failed or rejected fetch may leave a partial file behind;
        cleanup of ``dest`` is the caller's responsibility.
        """
        try:
            return await self._fetch_to_file(url, dest=dest, accept=accept)
        except FetchFailedError:
            raise
        except (httpx.HTTPError, OSError, ValidationError, ValueError) as exc:
            raise FetchFailedError(url=url, detail=repr(exc)) from exc

    async def _fetch(self, url: str) -> FetchResult:
        async with self._stream(url) as (response, final_url):
            body = await self._read_body(response, source_url=url)
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=response.status_code,
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                charset=response.charset_encoding,
                body=body,
            )

    async def _fetch_to_file(
        self,
        url: str,
        *,
        dest: Path,
        accept: Callable[[str], bool] | None,
    ) -> FileFetchResult:
        async with self._stream(url) as (response, final_url):
            content_type = _normalized_content_type(response)
            if accept is not None and not accept(content_type):
                raise FetchFailedError(
                    url=url, detail=f"unexpected content type {content_type!r}"
                )
            size_bytes = await self._write_body(response, dest=dest, source_url=url)
            return FileFetchResult(
                url=url,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                path=dest,
                size_bytes=size_bytes,
            )

    @asynccontextmanager
    async def _stream(self, url: str) -> AsyncGenerator[tuple[httpx.Response, str]]:
        """Yield the final non-redirect response (SSRF-pinned) and its URL."""
        current_url = httpx.URL(url)
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout_s,
            trust_env=False,
            headers={"User-Agent": "refindery/0.1"},
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                target = await self._pin(current_url)
                async with client.stream(
                    "GET",
                    target.request_url,
                    headers={"Host": target.host_header},
                    extensions={"sni_hostname": target.sni_hostname},
                ) as response:
                    if response.has_redirect_location:
                        if redirect_count == _MAX_REDIRECTS:
                            raise httpx.TooManyRedirects(
                                _TOO_MANY_REDIRECTS,
                                request=response.request,
                            )
                        current_url = httpx.URL(
                            urljoin(
                                base=str(current_url),
                                url=response.headers["location"],
                            )
                        )
                        continue
                    response.raise_for_status()
                    yield response, str(current_url)
                    return
        raise AssertionError("unreachable")

    async def _pin(self, url: httpx.URL) -> _PinnedTarget:
        if url.scheme not in {"http", "https"} or not url.host:
            msg = f"not an absolute http(s) URL: {url!s}"
            raise ValueError(msg)
        port = url.port or (443 if url.scheme == "https" else 80)
        raw_addresses = await self._resolver(host=url.host, port=port)
        addresses = _IP_ADDRESSES_ADAPTER.validate_python(raw_addresses)
        if blocked := tuple(address for address in addresses if not address.is_global):
            detail = ", ".join(str(address) for address in blocked)
            msg = f"destination resolves to non-public address(es): {detail}"
            raise ValueError(msg)
        address = addresses[0]
        return _PinnedTarget(
            request_url=url.copy_with(host=str(address)),
            host_header=url.netloc.decode("ascii"),
            sni_hostname=url.host,
        )

    async def _read_body(self, response: httpx.Response, *, source_url: str) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._max_bytes:
                raise FetchFailedError(
                    url=source_url,
                    detail=f"body exceeds {self._max_bytes} bytes",
                )
        return bytes(body)

    async def _write_body(
        self, response: httpx.Response, *, dest: Path, source_url: str
    ) -> int:
        size_bytes = 0
        handle = await asyncio.to_thread(_open_for_writing, dest)
        try:
            async for chunk in response.aiter_bytes():
                size_bytes += len(chunk)
                if size_bytes > self._max_bytes:
                    raise FetchFailedError(
                        url=source_url,
                        detail=f"body exceeds {self._max_bytes} bytes",
                    )
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
        return size_bytes
