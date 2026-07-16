"""Tests for the URL-routing fetcher."""

from refindery.application.ports.content_extractor import FetchResult
from refindery.application.services.fetch_router import RoutingFetcher
from tests.fakes.extraction import FakeFetcher, youtube_fetch_result


def _html_result(url: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        charset="utf-8",
        body=b"<p>hi</p>",
    )


async def test_routes_youtube_to_youtube_fetcher():
    yt_url = "https://youtu.be/abc"
    other_url = "https://example.com/page"
    default = FakeFetcher({other_url: _html_result(other_url)})
    youtube = FakeFetcher(
        {yt_url: youtube_fetch_result(yt_url, title="T", transcript="hi")}
    )
    router = RoutingFetcher(default=default, youtube=youtube)

    await router.fetch(yt_url)
    await router.fetch(other_url)

    assert youtube.calls == [yt_url]
    assert default.calls == [other_url]
