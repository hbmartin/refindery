"""json3 and WebVTT caption parsing to clean transcript text."""

import json

from refindery.adapters.youtube.captions import (
    transcript_from_json3,
    transcript_from_vtt,
)


def _json3(*lines: str | None) -> str:
    events: list[dict[str, object]] = []
    for line in lines:
        if line is None:
            events.append({"tStartMs": 0})  # segless event (music cue etc.)
        else:
            events.append({"tStartMs": 0, "segs": [{"utf8": line}]})
    return json.dumps({"events": events})


def test_json3_joins_segments_and_strips():
    raw = json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world"}]}]})
    assert transcript_from_json3(raw) == "Hello world"


def test_json3_drops_consecutive_duplicates_and_empty_lines():
    raw = _json3("first line", "first line", "  ", "second line", None)
    assert transcript_from_json3(raw) == "first line\nsecond line"


def test_json3_segless_events_are_skipped():
    assert transcript_from_json3(_json3(None, None)) == ""


VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Hello <c.colorE5E5E5>there</c>

00:00:02.000 --> 00:00:04.000
Hello there

2
00:00:04.000 --> 00:00:06.000
General &amp; specific
"""


def test_vtt_strips_headers_timings_tags_and_rolling_duplicates():
    assert transcript_from_vtt(VTT) == "Hello there\nGeneral & specific"
