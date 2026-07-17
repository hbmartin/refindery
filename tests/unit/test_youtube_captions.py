"""json3 and WebVTT caption parsing to clean transcript text."""

import json

from refindery.adapters.youtube.captions import (
    parse_json3,
    parse_vtt,
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


def test_json3_preserves_offsets_for_retained_lines():
    raw = json.dumps(
        {
            "events": [
                {"tStartMs": 0, "segs": [{"utf8": "first"}]},
                {"tStartMs": 10_000, "segs": [{"utf8": "first"}]},
                {"tStartMs": 20_000, "segs": [{"utf8": "second"}]},
            ]
        }
    )

    parsed = parse_json3(raw)

    assert parsed.text == "first\nsecond"
    assert parsed.offsets == ((0, 0.0), (6, 20.0))


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


def test_vtt_preserves_offsets_for_retained_cues():
    parsed = parse_vtt(VTT)

    assert parsed.text == "Hello there\nGeneral & specific"
    assert parsed.offsets == ((0, 0.0), (12, 4.0))
