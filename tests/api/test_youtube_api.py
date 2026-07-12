"""YouTube end-to-end: transcript ingest via /v1/pages and playlist watches."""

import asyncio
import json

import httpx
import pytest

from refindery.adapters.extraction.routing_fetcher import RoutingFetcher
from refindery.adapters.youtube.backend import CaptionTrack, VideoCaptionsResult
from refindery.adapters.youtube.caption_fetcher import YoutubeCaptionFetcher
from refindery.api.app import create_app
from refindery.application.ports.watch_source import WatchItem
from refindery.domain.models import WatchKind
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher
from tests.fakes.watch import FakeWatchSource
from tests.fakes.youtube import FakeYoutubeBackend

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

VIDEO_ID = "dQw4w9WgXcQ"
SHORT_URL = f"https://youtu.be/{VIDEO_ID}"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc"
JSON3 = json.dumps({"events": [{"segs": [{"utf8": "the transcript of the video"}]}]})


def _youtube_fetcher() -> YoutubeCaptionFetcher:
    probe = VideoCaptionsResult(
        video_id=VIDEO_ID,
        title="A Captioned Video",
        track=CaptionTrack(
            language="en", is_automatic=False, fmt="json3", content=JSON3
        ),
    )
    return YoutubeCaptionFetcher(
        backend=FakeYoutubeBackend(captions={SHORT_URL: probe, WATCH_URL: probe}),
        transcriber=None,
        langs=("en",),
        allow_auto=True,
        transcribe_fallback=False,
        timeout_s=5.0,
    )


@pytest.fixture
async def harness(tmp_path):
    fetcher = RoutingFetcher(default=FakeFetcher(), youtube=_youtube_fetcher())
    container = build_test_container(
        tmp_path,
        fetcher=fetcher,
        watch_sources={
            WatchKind.YOUTUBE: FakeWatchSource(
                {PLAYLIST_URL: [WatchItem(url=WATCH_URL, title="A Captioned Video")]}
            )
        },
    )
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, container


async def _wait_indexed(client, page_id: str) -> None:
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            if got.json()["status"] == "indexed":
                return
            await asyncio.sleep(0.05)


async def test_video_url_indexes_transcript_and_title(harness):
    client, _container = harness
    response = await client.post("/v1/pages", json={"url": SHORT_URL}, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    await _wait_indexed(client, page_id)

    page = await client.get(f"/v1/pages/{page_id}", headers=AUTH)
    body = page.json()
    assert body["title"] == "A Captioned Video"
    assert body["body_text"] == "the transcript of the video"
    # youtu.be canonicalizes to the watch form.
    assert body["canonical_url"] == f"https://youtube.com/watch?v={VIDEO_ID}"

    # The watch?v= form of the same video is a revisit, not a new page.
    revisit = await client.post("/v1/pages", json={"url": WATCH_URL}, headers=AUTH)
    assert revisit.status_code == 200
    assert revisit.json()["page_id"] == page_id


async def test_youtube_watch_fans_out_video_transcripts(harness):
    client, container = harness
    created = await client.post(
        "/v1/watches", json={"url": PLAYLIST_URL, "kind": "youtube"}, headers=AUTH
    )
    assert created.status_code == 201
    assert await container.watches.tick() == 1
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/watches/{created.json()['id']}", headers=AUTH)
            if got.json()["last_status"] == "ok":
                break
            await asyncio.sleep(0.05)
    assert got.json()["last_item_count"] == 1
    page = await container.store.get_page_by_canonical_url(
        f"https://youtube.com/watch?v={VIDEO_ID}"
    )
    assert page is not None


async def test_youtube_watch_rejects_single_video_url(harness):
    client, _container = harness
    response = await client.post(
        "/v1/watches", json={"url": WATCH_URL, "kind": "youtube"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "single video" in response.json()["detail"]


async def test_youtube_watch_rejects_non_listing_url(harness):
    client, _container = harness
    response = await client.post(
        "/v1/watches",
        json={"url": "https://www.youtube.com/feed/history", "kind": "youtube"},
        headers=AUTH,
    )
    assert response.status_code == 422


async def test_youtube_watch_unavailable_without_source(tmp_path):
    container = build_test_container(tmp_path)  # RSS-only sources
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            response = await http.post(
                "/v1/watches",
                json={"url": PLAYLIST_URL, "kind": "youtube"},
                headers=AUTH,
            )
    assert response.status_code == 501
    assert "not available" in response.json()["detail"]
