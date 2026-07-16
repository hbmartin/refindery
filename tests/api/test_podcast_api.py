"""Podcast end-to-end: an RSS watch indexes an episode into chapter chunks."""

import asyncio

import httpx
import pytest

from refindery.adapters.podcast.envelope import (
    PODCAST_TRANSCRIPT_CONTENT_TYPE,
    PodcastSection,
    PodcastTranscriptEnvelope,
)
from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.watch_source import WatchItem
from refindery.domain.models import WatchKind
from tests.fakes.chunking import FixedChunker
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.watch import FakeWatchSource

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

FEED_URL = "https://pod.example/feed.xml"
CHAPTERED_URL = "https://pod.example/ep1"
CHAPTERED_VTT = "https://cdn.example/ep1.vtt"
PLAIN_URL = "https://pod.example/ep2"
PLAIN_VTT = "https://cdn.example/ep2.vtt"

_SEG1 = "Welcome to the show today."
_SEG2 = "Now the main topic begins."
_SEG3 = "And we wrap it up here now."
_TRANSCRIPT = f"{_SEG1}\n{_SEG2}\n{_SEG3}"
_B2 = len(_SEG1) + 1
_B3 = _B2 + len(_SEG2) + 1

_CHAPTERED = PodcastTranscriptEnvelope(
    episode_url=CHAPTERED_URL,
    title="Episode One",
    language="en",
    transcript=_TRANSCRIPT,
    sections=(
        PodcastSection(title="Intro", char_start=0, char_end=_B2, start_time_s=0.0),
        PodcastSection(title="Main", char_start=_B2, char_end=_B3, start_time_s=60.0),
        PodcastSection(
            title="Outro", char_start=_B3, char_end=len(_TRANSCRIPT), start_time_s=120.0
        ),
    ),
    source_url=CHAPTERED_VTT,
)

_PLAIN = PodcastTranscriptEnvelope(
    episode_url=PLAIN_URL,
    title="Episode Two",
    language="en",
    transcript=_TRANSCRIPT,
    sections=(),
    source_url=PLAIN_VTT,
)


class _FakePodcastProducer:
    """Returns a preset envelope keyed by transcript URL (no packages needed)."""

    def __init__(self, envelopes: dict[str, PodcastTranscriptEnvelope]) -> None:
        self._envelopes = envelopes

    async def build(
        self,
        *,
        episode_url: str,
        transcript_url: str,
        transcript_type: str | None,  # noqa: ARG002 — port signature
        chapters_url: str | None,  # noqa: ARG002 — port signature
        description: str | None,  # noqa: ARG002 — port signature
    ) -> FetchResult:
        envelope = self._envelopes[transcript_url]
        return FetchResult(
            url=episode_url,
            final_url=episode_url,
            status_code=200,
            content_type=PODCAST_TRANSCRIPT_CONTENT_TYPE,
            charset="utf-8",
            body=envelope.model_dump_json().encode("utf-8"),
        )


def _episode(url: str, transcript_url: str, *, chapters: bool) -> WatchItem:
    return WatchItem(
        url=url,
        title="Episode",
        transcript_url=transcript_url,
        transcript_type="text/vtt",
        chapters_url="https://cdn.example/ch.json" if chapters else None,
    )


@pytest.fixture
async def harness(tmp_path):
    producer = _FakePodcastProducer({CHAPTERED_VTT: _CHAPTERED, PLAIN_VTT: _PLAIN})
    source = FakeWatchSource(
        {
            FEED_URL: [
                _episode(CHAPTERED_URL, CHAPTERED_VTT, chapters=True),
                _episode(PLAIN_URL, PLAIN_VTT, chapters=False),
            ]
        }
    )
    container = build_test_container(
        tmp_path,
        chunker=FixedChunker(size=16),
        podcast_producer=producer,
        watch_sources={WatchKind.RSS: source},
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


async def _run_watch(client, container) -> None:
    created = await client.post(
        "/v1/watches", json={"url": FEED_URL, "kind": "rss"}, headers=AUTH
    )
    assert created.status_code == 201
    assert await container.watches.tick() == 1
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/watches/{created.json()['id']}", headers=AUTH)
            if got.json()["last_status"] == "ok":
                return
            await asyncio.sleep(0.05)


async def test_podcast_watch_indexes_chapter_titled_chunks(harness):
    client, container = harness
    await _run_watch(client, container)

    page = await container.store.get_page_by_canonical_url(CHAPTERED_URL)
    assert page is not None
    await _wait_indexed(client, page.id)

    body = (await client.get(f"/v1/pages/{page.id}/chunks", headers=AUTH)).json()
    chunks = body["chunks"]
    assert chunks
    # Every chunk belongs to a chapter and no chunk spans two chapters.
    assert {c["section_title"] for c in chunks} == {"Intro", "Main", "Outro"}
    for chunk in chunks:
        assert chunk["text"].startswith(f"{chunk['section_title']}\n\n")
    # Chapter start times are preserved for retrieval/faceting.
    assert any(c["section_start_s"] == 60.0 for c in chunks)


async def test_podcast_without_chapters_falls_back_to_flat_chunks(harness):
    client, container = harness
    await _run_watch(client, container)

    page = await container.store.get_page_by_canonical_url(PLAIN_URL)
    assert page is not None
    await _wait_indexed(client, page.id)

    body = (await client.get(f"/v1/pages/{page.id}/chunks", headers=AUTH)).json()
    chunks = body["chunks"]
    assert chunks
    assert all(c["section_title"] is None for c in chunks)
    assert all(c["section_start_s"] is None for c in chunks)
