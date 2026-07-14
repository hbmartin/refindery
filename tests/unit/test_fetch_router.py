"""RoutingFetcher: videos to YouTube, audio to Whisper, the rest to default."""

import pytest

from refindery.adapters.extraction.routing_fetcher import RoutingFetcher
from refindery.application.ports.content_extractor import FetchResult, FetchRoute
from refindery.domain.errors import FetchFailedError
from tests.fakes.extraction import FakeFetcher

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PAGE_URL = "https://example.com/article"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc"
AUDIO_URL = "https://cdn.example/episodes/42.mp3"
TYPED_AUDIO_URL = "https://cdn.example/download?id=42"


def _result(url: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        charset="utf-8",
        body=b"x",
    )


async def test_routes_video_urls_to_youtube_and_rest_to_default():
    default = FakeFetcher(
        {PAGE_URL: _result(PAGE_URL), PLAYLIST_URL: _result(PLAYLIST_URL)}
    )
    youtube = FakeFetcher({VIDEO_URL: _result(VIDEO_URL)})
    router = RoutingFetcher(default=default, youtube=youtube)

    await router.fetch(VIDEO_URL)
    await router.fetch(PAGE_URL)
    await router.fetch(PLAYLIST_URL)  # listing pages are NOT videos

    assert youtube.calls == [VIDEO_URL]
    assert default.calls == [PAGE_URL, PLAYLIST_URL]


async def test_no_youtube_fetcher_sends_everything_to_default():
    default = FakeFetcher({VIDEO_URL: _result(VIDEO_URL)})
    router = RoutingFetcher(default=default, youtube=None)
    await router.fetch(VIDEO_URL)
    assert default.calls == [VIDEO_URL]


async def test_routes_audio_urls_to_audio_and_rest_to_default():
    default = FakeFetcher({PAGE_URL: _result(PAGE_URL)})
    audio = FakeFetcher({AUDIO_URL: _result(AUDIO_URL)})
    router = RoutingFetcher(default=default, audio=audio)

    await router.fetch(AUDIO_URL)
    await router.fetch(PAGE_URL)

    assert audio.calls == [AUDIO_URL]
    assert default.calls == [PAGE_URL]


async def test_explicit_audio_route_handles_url_without_extension() -> None:
    audio = FakeFetcher({TYPED_AUDIO_URL: _result(TYPED_AUDIO_URL)})
    router = RoutingFetcher(default=FakeFetcher(), audio=audio)

    await router.fetch_routed(TYPED_AUDIO_URL, route=FetchRoute.AUDIO)

    assert audio.calls == [TYPED_AUDIO_URL]


async def test_explicit_audio_route_fails_when_audio_fetcher_is_unavailable() -> None:
    router = RoutingFetcher(default=FakeFetcher())

    with pytest.raises(FetchFailedError, match="audio fetch route is unavailable"):
        await router.fetch_routed(TYPED_AUDIO_URL, route=FetchRoute.AUDIO)


async def test_no_audio_fetcher_sends_audio_to_default():
    default = FakeFetcher({AUDIO_URL: _result(AUDIO_URL)})
    router = RoutingFetcher(default=default)
    await router.fetch(AUDIO_URL)
    assert default.calls == [AUDIO_URL]


async def test_video_urls_never_hit_the_audio_route():
    youtube = FakeFetcher({VIDEO_URL: _result(VIDEO_URL)})
    audio = FakeFetcher()
    router = RoutingFetcher(default=FakeFetcher(), youtube=youtube, audio=audio)
    await router.fetch(VIDEO_URL)
    assert youtube.calls == [VIDEO_URL]
    assert audio.calls == []
