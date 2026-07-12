"""Streaming ASGI transport for SSE tests.

httpx 0.28's ``ASGITransport`` runs the app to completion and buffers every
body part, so an endless ``text/event-stream`` response deadlocks. This
transport returns the ``httpx.Response`` as soon as the app sends
``http.response.start`` and streams body chunks through a queue; closing the
response delivers ``http.disconnect`` (so Starlette cancels the generator)
and awaits the app task.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, MutableMapping
from typing import Any

import httpx

_Message = MutableMapping[str, Any]


class _RequestChannel:
    """ASGI receive/send pair bridging one request to a streaming response."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._delivered = False
        self.started = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.response_start: dict[str, Any] = {}

    async def receive(self) -> _Message:
        """Deliver the request body once, then block until disconnect."""
        if not self._delivered:
            self._delivered = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: _Message) -> None:
        """Record the response start; stream body chunks (None = end)."""
        if message["type"] == "http.response.start":
            self.response_start["status"] = message["status"]
            self.response_start["headers"] = message.get("headers", [])
            self.started.set()
        elif message["type"] == "http.response.body":
            if chunk := message.get("body", b""):
                await self.chunks.put(chunk)
            if not message.get("more_body", False):
                await self.chunks.put(None)


class _QueueByteStream(httpx.AsyncByteStream):
    """Yields chunks until the None sentinel; aclose stops the app."""

    def __init__(
        self,
        chunks: "asyncio.Queue[bytes | None]",
        close: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self._chunks = chunks
        self._close = close

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while (chunk := await self._chunks.get()) is not None:
            yield chunk

    async def aclose(self) -> None:
        await self._close()


def _scope(request: httpx.Request) -> MutableMapping[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": request.method,
        "headers": [(k.lower(), v) for k, v in request.headers.raw],
        "scheme": request.url.scheme,
        "path": request.url.path,
        "raw_path": request.url.raw_path,
        "query_string": request.url.query,
        "server": (request.url.host, request.url.port or 80),
        "client": ("testclient", 5000),
        "root_path": "",
    }


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """In-process transport that supports endless streaming responses."""

    def __init__(self, app: Callable[..., Coroutine[Any, Any, None]]) -> None:
        self._app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Run the app as a task; return the response at http.response.start."""
        channel = _RequestChannel(await request.aread())

        async def run_app() -> None:
            try:
                await self._app(_scope(request), channel.receive, channel.send)
            finally:
                await channel.chunks.put(None)  # always unblock the reader
                channel.started.set()

        task = asyncio.create_task(run_app(), name="streaming-asgi-app")

        async def close() -> None:
            channel.disconnected.set()
            try:
                async with asyncio.timeout(10):
                    await task
            except TimeoutError:
                task.cancel()

        async with asyncio.timeout(30):
            await channel.started.wait()
        if "status" not in channel.response_start:
            await task  # the app died before responding: surface its exception
            msg = "app completed without http.response.start"
            raise RuntimeError(msg)
        return httpx.Response(
            status_code=channel.response_start["status"],
            headers=channel.response_start["headers"],
            stream=_QueueByteStream(channel.chunks, close),
            request=request,
        )
