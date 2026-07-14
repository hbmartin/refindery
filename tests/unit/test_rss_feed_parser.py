"""RSS/Atom parsing: real feed bytes in, WatchItems out, bad entries dropped."""

from datetime import UTC, datetime
from time import struct_time

import feedparser

from refindery.adapters.feeds.rss_feedparser import (
    RssWatchSource,
    entry_published,
    parse_feed,
)
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.extraction import FakeFetcher

RSS2: bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Blog</title>
  <link>https://blog.example/</link>
  <item>
    <title>First Post</title>
    <link>https://blog.example/posts/first</link>
    <pubDate>Mon, 06 Sep 2021 16:45:00 GMT</pubDate>
  </item>
  <item>
    <title>Relative Post</title>
    <link>/posts/relative</link>
  </item>
  <item>
    <description>No link here at all.</description>
  </item>
</channel></rss>
"""

ATOM: bytes = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Atom Entry</title>
    <link rel="alternate" href="https://atom.example/entries/1"/>
    <updated>2026-01-02T03:04:05Z</updated>
  </entry>
  <entry>
    <title>Bad Scheme</title>
    <link rel="alternate" href="ftp://files.example/entries/2"/>
  </entry>
</feed>
"""

MALFORMED_LINK: bytes = b"""<rss><channel>
  <item><link>http://[broken</link></item>
  <item><link>https://example.com/good</link></item>
</channel></rss>
"""


def test_rss2_items_parsed_with_titles_and_dates() -> None:
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert [item.url for item in items] == [
        "https://blog.example/posts/first",
        "https://blog.example/posts/relative",
    ]
    assert items[0].title == "First Post"
    assert items[0].published_at == datetime(2021, 9, 6, 16, 45, tzinfo=UTC)
    assert items[1].published_at is None


def test_relative_links_resolve_against_base_url() -> None:
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert items[1].url == "https://blog.example/posts/relative"


def test_entry_without_link_is_skipped() -> None:
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert len(items) == 2


def test_atom_entries_parse_and_invalid_scheme_dropped() -> None:
    items = parse_feed(raw=ATOM, base_url="https://atom.example/feed.atom")
    assert [item.url for item in items] == ["https://atom.example/entries/1"]
    assert items[0].title == "Atom Entry"
    assert items[0].published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_malformed_xml_yields_no_items() -> None:
    items = parse_feed(raw=b"this is not xml <<<", base_url="https://x.example/feed")
    assert items == []


def test_malformed_link_is_dropped_without_hiding_valid_entries() -> None:
    items = parse_feed(
        raw=MALFORMED_LINK,
        base_url="https://example.com/feed.xml",
    )
    assert [item.url for item in items] == ["https://example.com/good"]


def test_extreme_date_is_dropped_without_crashing() -> None:
    entry = feedparser.FeedParserDict(
        published_parsed=struct_time((10**100, 1, 1, 0, 0, 0, 0, 1, 0))
    )
    assert entry_published(entry) is None


async def test_source_fetches_and_parses_via_fetcher() -> None:
    feed_url = "https://blog.example/feed.xml"
    fetcher = FakeFetcher(
        {
            feed_url: FetchResult(
                url=feed_url,
                final_url=feed_url,
                status_code=200,
                content_type="application/rss+xml",
                charset="utf-8",
                body=RSS2,
            )
        }
    )
    source = RssWatchSource(fetcher=fetcher)
    items = await source.discover(url=feed_url, config={})
    assert fetcher.calls == [feed_url]
    assert len(items) == 2
