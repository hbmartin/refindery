"""Podcast feed parsing: enclosure audio in, WatchItems out, the rest dropped."""

from datetime import UTC, datetime

from refindery.adapters.feeds.podcast_feedparser import (
    PodcastWatchSource,
    parse_podcast_feed,
)
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.extraction import FakeFetcher

PODCAST_RSS: bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Podcast</title>
  <link>https://pod.example/</link>
  <item>
    <title>Episode One</title>
    <link>https://pod.example/episodes/1</link>
    <enclosure url="https://cdn.example/audio/ep1.mp3" length="123" type="audio/mpeg"/>
    <pubDate>Mon, 06 Sep 2021 16:45:00 GMT</pubDate>
  </item>
  <item>
    <title>Text Only</title>
    <link>https://pod.example/posts/text-only</link>
  </item>
  <item>
    <title>Cover Art Only</title>
    <link>https://pod.example/episodes/art</link>
    <enclosure url="https://cdn.example/img/cover.jpg" length="9" type="image/jpeg"/>
  </item>
  <item>
    <title>Untyped Relative Audio</title>
    <enclosure url="/audio/ep2.m4a" length="5"/>
  </item>
</channel></rss>
"""

PODCAST_ATOM: bytes = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Pod</title>
  <entry>
    <title>Atom Episode</title>
    <link rel="alternate" href="https://pod.example/entries/1"/>
    <link rel="enclosure" href="https://cdn.example/audio/atom1.mp3"
          type="audio/mpeg"/>
    <updated>2026-01-02T03:04:05Z</updated>
  </entry>
</feed>
"""

FEED_URL = "https://pod.example/feed.xml"


def test_audio_enclosures_become_items_and_the_rest_are_skipped() -> None:
    items = parse_podcast_feed(raw=PODCAST_RSS, base_url=FEED_URL)
    assert [item.url for item in items] == [
        "https://cdn.example/audio/ep1.mp3",
        "https://pod.example/audio/ep2.m4a",
    ]


def test_item_url_is_the_enclosure_not_the_episode_link() -> None:
    items = parse_podcast_feed(raw=PODCAST_RSS, base_url=FEED_URL)
    assert items[0].url == "https://cdn.example/audio/ep1.mp3"
    assert items[0].title == "Episode One"
    assert items[0].published_at == datetime(2021, 9, 6, 16, 45, tzinfo=UTC)


def test_untyped_enclosure_accepted_by_audio_extension_and_resolved() -> None:
    items = parse_podcast_feed(raw=PODCAST_RSS, base_url=FEED_URL)
    assert items[1].url == "https://pod.example/audio/ep2.m4a"
    assert items[1].title == "Untyped Relative Audio"
    assert items[1].published_at is None


def test_atom_enclosure_link_is_discovered() -> None:
    items = parse_podcast_feed(raw=PODCAST_ATOM, base_url="https://pod.example/atom")
    assert [item.url for item in items] == ["https://cdn.example/audio/atom1.mp3"]
    assert items[0].title == "Atom Episode"
    assert items[0].published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_malformed_xml_yields_no_items() -> None:
    assert parse_podcast_feed(raw=b"not xml <<<", base_url=FEED_URL) == []


async def test_source_fetches_and_parses_via_fetcher() -> None:
    fetcher = FakeFetcher(
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
    source = PodcastWatchSource(fetcher=fetcher)
    items = await source.discover(url=FEED_URL, config={})
    assert fetcher.calls == [FEED_URL]
    assert len(items) == 2
