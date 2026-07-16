"""RSS/Atom parsing: link/title/date extraction, resilience to bad input."""

from datetime import UTC, datetime

from refindery.adapters.feeds.rss_feedparser import RssFeedParser

BASE = "https://example.com/feed.xml"

_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example</title>
<item><title>Alpha</title><link>https://example.com/a</link>
<pubDate>Tue, 10 Jun 2025 04:00:00 GMT</pubDate></item>
<item><title>Beta</title><link>/b</link></item>
</channel></rss>
"""

_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Example</title>
<entry><title>Gamma</title><link href="https://example.com/c"/>
<updated>2025-06-10T04:00:00Z</updated></entry>
</feed>
"""


async def test_rss_extracts_link_title_and_date():
    items = await RssFeedParser().parse(raw=_RSS, base_url=BASE)
    assert [i.url for i in items] == ["https://example.com/a", "https://example.com/b"]
    assert items[0].title == "Alpha"
    assert items[0].published_at == datetime(2025, 6, 10, 4, 0, tzinfo=UTC)
    # Relative link resolved against the feed URL.
    assert items[1].url == "https://example.com/b"


async def test_atom_link_href_is_used():
    items = await RssFeedParser().parse(raw=_ATOM, base_url=BASE)
    assert [i.url for i in items] == ["https://example.com/c"]
    assert items[0].title == "Gamma"
    assert items[0].published_at == datetime(2025, 6, 10, 4, 0, tzinfo=UTC)


async def test_malformed_feed_yields_no_items():
    items = await RssFeedParser().parse(raw=b"<not a feed <<<", base_url=BASE)
    assert items == []


async def test_entry_without_link_is_skipped():
    raw = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <item><title>NoLink</title></item>
    <item><title>HasLink</title><link>https://example.com/x</link></item>
    </channel></rss>"""
    items = await RssFeedParser().parse(raw=raw, base_url=BASE)
    assert [i.url for i in items] == ["https://example.com/x"]


async def test_non_http_link_is_dropped_without_aborting_feed():
    raw = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <item><title>Bad</title><link>javascript:void(0)</link></item>
    <item><title>Good</title><link>https://example.com/ok</link></item>
    </channel></rss>"""
    items = await RssFeedParser().parse(raw=raw, base_url=BASE)
    assert [i.url for i in items] == ["https://example.com/ok"]
