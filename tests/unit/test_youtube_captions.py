"""Tests for json3/vtt caption parsing."""

from refindery.adapters.extraction.youtube_captions import parse_json3, parse_vtt


def test_parse_json3_dedupes_and_joins_segments():
    payload = (
        '{"events":['
        '{"segs":[{"utf8":"Hello there."}]},'
        '{"segs":[{"utf8":"Hello there."}]},'
        '{"segs":[{"utf8":"General "},{"utf8":"Kenobi."}]},'
        '{"segs":[{"utf8":"\\n"}]},'
        '{"tStartMs":0}'
        "]}"
    )
    assert parse_json3(payload) == "Hello there.\nGeneral Kenobi."


def test_parse_json3_empty():
    assert parse_json3('{"events":[]}') == ""


def test_parse_vtt_strips_tags_headers_and_dedupes():
    payload = (
        "WEBVTT\n"
        "Kind: captions\n"
        "Language: en\n"
        "\n"
        "00:00:00.000 --> 00:00:02.000\n"
        "Hello <c>there</c>.\n"
        "\n"
        "00:00:02.000 --> 00:00:04.000\n"
        "Hello there.\n"
        "\n"
        "00:00:04.000 --> 00:00:06.000 align:start\n"
        "General Kenobi.\n"
    )
    assert parse_vtt(payload) == "Hello there.\nGeneral Kenobi."


def test_parse_vtt_unescapes_entities():
    payload = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nTom &amp; Jerry &lt;3\n"
    assert parse_vtt(payload) == "Tom & Jerry <3"
