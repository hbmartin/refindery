"""RoutingFetcher: video URLs to the YouTube fetcher, the rest to default."""

from refindery.adapters.extraction.routing_fetcher import RoutingFetcher
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.extraction import FakeFetcher

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PAGE_URL = "https://example.com/article"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc"


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
