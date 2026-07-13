"""Podcast end-to-end: audio transcript ingest via /v1/pages and feed watches."""

import asyncio

import httpx
import pytest

from refindery.adapters.extraction.routing_fetcher import RoutingFetcher
from refindery.adapters.feeds.podcast_feedparser import PodcastWatchSource
from refindery.adapters.transcription.audio_fetcher import AudioTranscriptFetcher
from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.models import WatchKind
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher, FakeFileDownloader
from tests.fakes.youtube import FakeTranscriber

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

FEED_URL = "https://pod.example/feed.xml"
ENCLOSURE_URL = "https://cdn.example/audio/ep1.mp3"
TRANSCRIPT = "spoken words about the topic of the episode"

PODCAST_RSS: bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Podcast</title>
  <item>
    <title>Episode One</title>
    <link>https://pod.example/episodes/1</link>
    <enclosure url="https://cdn.example/audio/ep1.mp3" length="123" type="audio/mpeg"/>
    <pubDate>Mon, 06 Sep 2021 16:45:00 GMT</pubDate>
  </item>
</channel></rss>
"""


def _feed_fetcher() -> FakeFetcher:
    return FakeFetcher(
        {
            FEED_URL: FetchResult(
                url=FEED_URL,
                final_url=FEED_URL,
                status_code=200,
                content_type="application/rss+xml",
                charset="utf-8",
                body=PODCAST_RSS,
            )
        }
    )


@pytest.fixture
async def harness(tmp_path):
    audio_fetcher = AudioTranscriptFetcher(
        downloader=FakeFileDownloader({ENCLOSURE_URL: (b"ID3fake", "audio/mpeg")}),
        transcriber=FakeTranscriber(TRANSCRIPT),
    )
    fetcher = RoutingFetcher(default=_feed_fetcher(), audio=audio_fetcher)
    container = build_test_container(
        tmp_path,
        fetcher=fetcher,
        watch_sources={WatchKind.PODCAST: PodcastWatchSource(fetcher=fetcher)},
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


async def test_audio_url_indexes_transcript(harness):
    client, _container = harness
    response = await client.post("/v1/pages", json={"url": ENCLOSURE_URL}, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    await _wait_indexed(client, page_id)

    page = await client.get(f"/v1/pages/{page_id}", headers=AUTH)
    body = page.json()
    assert body["body_text"] == TRANSCRIPT
    assert body["canonical_url"] == ENCLOSURE_URL


async def test_podcast_watch_fans_out_episode_transcripts(harness):
    client, container = harness
    created = await client.post(
        "/v1/watches", json={"url": FEED_URL, "kind": "podcast"}, headers=AUTH
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

    page = await container.store.get_page_by_canonical_url(ENCLOSURE_URL)
    assert page is not None
    await _wait_indexed(client, page.id)
    body = (await client.get(f"/v1/pages/{page.id}", headers=AUTH)).json()
    assert body["body_text"] == TRANSCRIPT
    # The episode title comes from the feed entry, not the envelope.
    assert body["title"] == "Episode One"


async def test_podcast_watch_rejects_direct_audio_url(harness):
    client, _container = harness
    response = await client.post(
        "/v1/watches", json={"url": ENCLOSURE_URL, "kind": "podcast"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "audio file" in response.json()["detail"]


async def test_podcast_watch_unavailable_without_source(tmp_path):
    container = build_test_container(tmp_path)  # RSS-only sources
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            response = await http.post(
                "/v1/watches",
                json={"url": FEED_URL, "kind": "podcast"},
                headers=AUTH,
            )
    assert response.status_code == 501
    assert "not available" in response.json()["detail"]
