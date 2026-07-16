"""Podcast watch integration for published transcripts and chapter chunks."""

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
CHAPTERED_URL = "https://cdn.example/chaptered.mp3"
CHAPTERED_VTT = "https://cdn.example/chaptered.vtt"
PLAIN_URL = "https://cdn.example/plain.mp3"
PLAIN_VTT = "https://cdn.example/plain.vtt"

_SEGMENT_1 = "Welcome to the show today."
_SEGMENT_2 = "Now the main topic begins."
_SEGMENT_3 = "And we wrap it up here now."
_TRANSCRIPT = f"{_SEGMENT_1}\n{_SEGMENT_2}\n{_SEGMENT_3}"
_BOUNDARY_2 = len(_SEGMENT_1) + 1
_BOUNDARY_3 = _BOUNDARY_2 + len(_SEGMENT_2) + 1


class _FakePodcastProducer:
    """Return a preset validated transcript envelope by transcript URL."""

    def __init__(self, envelopes: dict[str, PodcastTranscriptEnvelope]) -> None:
        self._envelopes = envelopes

    async def build(
        self,
        *,
        episode_url: str,
        transcript_url: str,
        transcript_type: str | None,  # noqa: ARG002 - protocol parity
        chapters_url: str | None,  # noqa: ARG002 - protocol parity
        description: str | None,  # noqa: ARG002 - protocol parity
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


def _envelope(
    *, url: str, transcript_url: str, chaptered: bool
) -> PodcastTranscriptEnvelope:
    sections: tuple[PodcastSection, ...] = ()
    if chaptered:
        sections = (
            PodcastSection(
                title="Intro", char_start=0, char_end=_BOUNDARY_2, start_time_s=0.0
            ),
            PodcastSection(
                title="Main",
                char_start=_BOUNDARY_2,
                char_end=_BOUNDARY_3,
                start_time_s=60.0,
            ),
            PodcastSection(
                title="Outro",
                char_start=_BOUNDARY_3,
                char_end=len(_TRANSCRIPT),
                start_time_s=120.0,
            ),
        )
    return PodcastTranscriptEnvelope(
        episode_url=url,
        title=None,
        language="en",
        transcript=_TRANSCRIPT,
        sections=sections,
        source_url=transcript_url,
    )


def _item(*, url: str, transcript_url: str, chaptered: bool) -> WatchItem:
    return WatchItem(
        url=url,
        title="Episode",
        enclosure_url=url,
        transcript_url=transcript_url,
        transcript_type="text/vtt",
        chapters_url="https://cdn.example/chapters.json" if chaptered else None,
    )


@pytest.fixture
async def harness(tmp_path):
    producer = _FakePodcastProducer(
        {
            CHAPTERED_VTT: _envelope(
                url=CHAPTERED_URL,
                transcript_url=CHAPTERED_VTT,
                chaptered=True,
            ),
            PLAIN_VTT: _envelope(
                url=PLAIN_URL,
                transcript_url=PLAIN_VTT,
                chaptered=False,
            ),
        }
    )
    source = FakeWatchSource(
        {
            FEED_URL: [
                _item(
                    url=CHAPTERED_URL,
                    transcript_url=CHAPTERED_VTT,
                    chaptered=True,
                ),
                _item(url=PLAIN_URL, transcript_url=PLAIN_VTT, chaptered=False),
            ]
        }
    )
    container = build_test_container(
        tmp_path,
        chunker=FixedChunker(size=16),
        podcast_producer=producer,
        watch_sources={WatchKind.PODCAST: source},
    )
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client, container


async def _run_watch(client, container) -> None:
    created = await client.post(
        "/v1/watches", json={"url": FEED_URL, "kind": "podcast"}, headers=AUTH
    )
    assert created.status_code == 201
    assert await container.watches.tick() == 1
    async with asyncio.timeout(30):
        while True:
            response = await client.get(
                f"/v1/watches/{created.json()['id']}", headers=AUTH
            )
            if response.json()["last_status"] == "ok":
                return
            await asyncio.sleep(0.05)


async def _wait_indexed(client, page_id: str) -> None:
    async with asyncio.timeout(30):
        while True:
            response = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            if response.json()["status"] == "indexed":
                return
            await asyncio.sleep(0.05)


async def test_published_podcast_chapters_become_chunk_boundaries(harness):
    client, container = harness
    await _run_watch(client, container)
    page = await container.store.get_page_by_canonical_url(CHAPTERED_URL)
    assert page is not None
    await _wait_indexed(client, page.id)

    response = await client.get(f"/v1/pages/{page.id}/chunks", headers=AUTH)
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


async def test_podcast_without_chapters_keeps_flat_chunking(harness):
    client, container = harness
    await _run_watch(client, container)
    page = await container.store.get_page_by_canonical_url(PLAIN_URL)
    assert page is not None
    await _wait_indexed(client, page.id)

    response = await client.get(f"/v1/pages/{page.id}/chunks", headers=AUTH)
    chunks = response.json()["chunks"]
    assert chunks
    assert all(chunk["section_title"] is None for chunk in chunks)
    assert all(chunk["section_start_s"] is None for chunk in chunks)
