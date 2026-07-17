"""YouTube end-to-end: transcript ingest via /v1/pages and playlist watches."""

import asyncio
import json

import httpx
import pytest

from refindery.adapters.extraction.routing_fetcher import RoutingFetcher
from refindery.adapters.youtube.backend import (
    CaptionTrack,
    VideoCaptionsResult,
    YoutubeChapter,
)
from refindery.adapters.youtube.caption_fetcher import YoutubeCaptionFetcher
from refindery.api.app import create_app
from refindery.application.ports.transcriber import TranscriptionSegment
from refindery.application.ports.watch_source import WatchItem
from refindery.domain.models import WatchKind
from tests.fakes.chunking import FixedChunker
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher
from tests.fakes.watch import FakeWatchSource
from tests.fakes.youtube import FakeTranscriber, FakeYoutubeBackend

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

VIDEO_ID = "dQw4w9WgXcQ"
SHORT_URL = f"https://youtu.be/{VIDEO_ID}"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc"
_INTRO = "the introduction of the video"
_MAIN = "the main topic of the video"
_OUTRO = "the conclusion of the video"
TRANSCRIPT = f"{_INTRO}\n{_MAIN}\n{_OUTRO}"
JSON3 = json.dumps(
    {
        "events": [
            {"tStartMs": 0, "segs": [{"utf8": _INTRO}]},
            {"tStartMs": 60_000, "segs": [{"utf8": _MAIN}]},
            {"tStartMs": 120_000, "segs": [{"utf8": _OUTRO}]},
        ]
    }
)


def _youtube_fetcher(*, transcribed: bool) -> YoutubeCaptionFetcher:
    chapters = (
        YoutubeChapter(title="Intro", start_time_s=0.0),
        YoutubeChapter(title="Main", start_time_s=60.0),
        YoutubeChapter(title="Outro", start_time_s=120.0),
    )
    probe = VideoCaptionsResult(
        video_id=VIDEO_ID,
        title="A Captioned Video",
        track=(
            None
            if transcribed
            else CaptionTrack(
                language="en", is_automatic=False, fmt="json3", content=JSON3
            )
        ),
        chapters=chapters,
    )
    backend = FakeYoutubeBackend(
        captions={SHORT_URL: probe, WATCH_URL: probe},
        audio=(
            {SHORT_URL: b"fake-audio", WATCH_URL: b"fake-audio"}
            if transcribed
            else None
        ),
    )
    transcriber = (
        FakeTranscriber(
            TRANSCRIPT,
            segments=(
                TranscriptionSegment(text=_INTRO, start_time_s=0.0, end_time_s=30.0),
                TranscriptionSegment(text=_MAIN, start_time_s=60.0, end_time_s=90.0),
                TranscriptionSegment(text=_OUTRO, start_time_s=120.0, end_time_s=150.0),
            ),
        )
        if transcribed
        else None
    )
    return YoutubeCaptionFetcher(
        backend=backend,
        transcriber=transcriber,
        langs=("en",),
        allow_auto=True,
        transcribe_fallback=transcribed,
        timeout_s=5.0,
    )


@pytest.fixture(params=(False, True), ids=("captions", "transcribed"))
async def harness(tmp_path, request: pytest.FixtureRequest):
    fetcher = RoutingFetcher(
        default=FakeFetcher(),
        youtube=_youtube_fetcher(transcribed=bool(request.param)),
    )
    container = build_test_container(
        tmp_path,
        fetcher=fetcher,
        chunker=FixedChunker(size=16),
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


async def _assert_chapter_chunks(client, page_id: str) -> None:
    response = await client.get(f"/v1/pages/{page_id}/chunks", headers=AUTH)
    chunks = response.json()["chunks"]
    assert {chunk["section_title"] for chunk in chunks} == {
        "Intro",
        "Main",
        "Outro",
    }
    assert all(
        chunk["text"].startswith(f"{chunk['section_title']}\n\n") for chunk in chunks
    )
    assert any(chunk["section_start_s"] == 60.0 for chunk in chunks)


async def test_video_url_indexes_transcript_and_title(harness):
    client, _container = harness
    response = await client.post("/v1/pages", json={"url": SHORT_URL}, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    await _wait_indexed(client, page_id)

    page = await client.get(f"/v1/pages/{page_id}", headers=AUTH)
    body = page.json()
    assert body["title"] == "A Captioned Video"
    assert body["body_text"] == TRANSCRIPT
    # youtu.be canonicalizes to the watch form.
    assert body["canonical_url"] == f"https://youtube.com/watch?v={VIDEO_ID}"

    # The watch?v= form of the same video is a revisit, not a new page.
    revisit = await client.post("/v1/pages", json={"url": WATCH_URL}, headers=AUTH)
    assert revisit.status_code == 200
    assert revisit.json()["page_id"] == page_id
    await _assert_chapter_chunks(client, page_id)


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
    await _wait_indexed(client, page.id)
    await _assert_chapter_chunks(client, page.id)


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
