"""RSS/Atom parsing: real feed bytes in, WatchItems out, bad entries dropped."""

from datetime import UTC, datetime

from refindery.adapters.feeds.rss_feedparser import RssWatchSource, parse_feed
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.extraction import FakeFetcher

RSS2 = b"""<?xml version="1.0" encoding="UTF-8"?>
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

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
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


def test_rss2_items_parsed_with_titles_and_dates():
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert [item.url for item in items] == [
        "https://blog.example/posts/first",
        "https://blog.example/posts/relative",
    ]
    assert items[0].title == "First Post"
    assert items[0].published_at == datetime(2021, 9, 6, 16, 45, tzinfo=UTC)
    assert items[1].published_at is None


def test_relative_links_resolve_against_base_url():
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert items[1].url == "https://blog.example/posts/relative"


def test_entry_without_link_is_skipped():
    items = parse_feed(raw=RSS2, base_url="https://blog.example/feed.xml")
    assert len(items) == 2


def test_atom_entries_parse_and_invalid_scheme_dropped():
    items = parse_feed(raw=ATOM, base_url="https://atom.example/feed.atom")
    assert [item.url for item in items] == ["https://atom.example/entries/1"]
    assert items[0].title == "Atom Entry"
    assert items[0].published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_malformed_xml_yields_no_items():
    items = parse_feed(raw=b"this is not xml <<<", base_url="https://x.example/feed")
    assert items == []


async def test_source_fetches_and_parses_via_fetcher():
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
