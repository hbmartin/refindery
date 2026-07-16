"""PodcastTranscriptProducer: pure time->char mapping + faked-library build()."""

import collections
import importlib.machinery
import sys
import types

from refindery.adapters.podcast.envelope import PodcastTranscriptEnvelope
from refindery.adapters.podcast.producer import (
    PodcastTranscriptProducer,
    _char_for_time,
    _concatenate,
    _format_from_mime,
    _sections_from_chapters,
)
from refindery.application.ports.content_extractor import FetchResult

Chapter = collections.namedtuple("Chapter", "start title url image")  # noqa: PYI024


class _FetcherStub:
    def __init__(self, bodies: dict[str, str]) -> None:
        self._bodies = bodies

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/plain",
            charset="utf-8",
            body=self._bodies[url].encode("utf-8"),
        )


def _fake_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    return module


def _install_fake_libs(monkeypatch, *, segments, pci=None, described=None) -> None:
    converters = _fake_module("podcast_transcript_convert.converters")
    for sub_name, attr in (
        ("vtt_to_json", "vtt_to_podcast_dict"),
        ("srt_to_json", "srt_to_podcast_dict"),
        ("html_to_json", "html_to_podcast_dict"),
    ):
        full = f"podcast_transcript_convert.converters.{sub_name}"
        sub = _fake_module(full)
        sub.__dict__[attr] = lambda _text: {"version": "1.0.0", "segments": segments}
        converters.__dict__[sub_name] = sub
        monkeypatch.setitem(sys.modules, full, sub)
    ptc = _fake_module("podcast_transcript_convert")
    ptc.__dict__["converters"] = converters
    monkeypatch.setitem(sys.modules, "podcast_transcript_convert", ptc)
    monkeypatch.setitem(
        sys.modules, "podcast_transcript_convert.converters", converters
    )

    pct = _fake_module("podcast_chapter_tools")
    exports = pct.__dict__
    exports["extract_pci_chapters"] = lambda _data: pci
    exports["extract_description_chapters"] = lambda _desc, strip_html=False: described
    exports["normalize_chapters"] = lambda chs, **_k: sorted(chs, key=lambda c: c.start)
    monkeypatch.setitem(sys.modules, "podcast_chapter_tools", pct)


# -- pure helpers -------------------------------------------------------------


def test_format_from_mime_maps_types_and_defaults_to_vtt():
    assert _format_from_mime("text/vtt") == "vtt"
    assert _format_from_mime("application/x-subrip") == "srt"
    assert _format_from_mime("text/html; charset=utf-8") == "html"
    assert _format_from_mime("application/json") == "json"
    assert _format_from_mime(None) == "vtt"
    assert _format_from_mime("audio/mpeg") == "vtt"


def test_char_for_time_snaps_to_first_segment_at_or_after():
    offsets = [(0, 0.0), (30, 10.0), (60, 25.0)]
    assert _char_for_time(offsets=offsets, time_s=0.0) == 0
    assert _char_for_time(offsets=offsets, time_s=10.0) == 30
    assert _char_for_time(offsets=offsets, time_s=5.0) == 30  # snaps forward
    assert _char_for_time(offsets=offsets, time_s=999.0) == 60  # beyond -> last


def test_concatenate_records_offsets_and_joins_with_newlines():
    body, offsets = _concatenate([("hello", 0.0), ("world", 4.0), ("bye", 9.0)])
    assert body == "hello\nworld\nbye"
    assert offsets == [(0, 0.0), (6, 4.0), (12, 9.0)]
    for char_start, _t in offsets:
        assert body[char_start : char_start + 1] in {"h", "w", "b"}


def test_sections_from_chapters_prepends_leading_untitled_section():
    offsets = [(0, 0.0), (50, 120.0)]
    sections = _sections_from_chapters(
        chapters=[(120.0, "Main")], offsets=offsets, body_len=80
    )
    assert [(s.title, s.char_start, s.char_end) for s in sections] == [
        (None, 0, 50),
        ("Main", 50, 80),
    ]


def test_sections_from_chapters_drops_zero_width_overlaps():
    offsets = [(0, 0.0), (40, 60.0)]
    # Two chapters snap to the same segment -> the first is zero-width, dropped.
    sections = _sections_from_chapters(
        chapters=[(60.0, "A"), (61.0, "B")], offsets=offsets, body_len=70
    )
    assert [(s.title, s.char_start, s.char_end) for s in sections] == [
        (None, 0, 40),
        ("B", 40, 70),
    ]


def test_sections_empty_when_no_chapters():
    assert _sections_from_chapters(chapters=[], offsets=[(0, 0.0)], body_len=10) == ()


# -- build() with faked libraries --------------------------------------------

_SEGMENTS = [
    {"startTime": 0.0, "body": "Welcome to the show."},
    {"startTime": 120.0, "body": "Now the main topic."},
    {"startTime": 300.0, "body": "And we wrap up."},
]


async def test_build_maps_pci_chapters_to_contiguous_sections(monkeypatch):
    _install_fake_libs(
        monkeypatch,
        segments=_SEGMENTS,
        pci=[
            Chapter(0, "Intro", None, None),
            Chapter(120, "Main", None, None),
            Chapter(300, "Outro", None, None),
        ],
    )
    fetcher = _FetcherStub({"http://t/ep.vtt": "ignored", "http://t/ch.json": "{}"})
    result = await PodcastTranscriptProducer(fetcher=fetcher).build(
        episode_url="http://t/ep",
        transcript_url="http://t/ep.vtt",
        transcript_type="text/vtt",
        chapters_url="http://t/ch.json",
        description=None,
    )
    envelope = PodcastTranscriptEnvelope.model_validate_json(result.body)
    titles = [s.title for s in envelope.sections]
    assert titles == ["Intro", "Main", "Outro"]
    assert envelope.sections[0].char_start == 0
    assert envelope.sections[-1].char_end == len(envelope.transcript)
    for earlier, later in zip(envelope.sections, envelope.sections[1:], strict=False):
        assert earlier.char_end == later.char_start  # contiguous, no gaps


async def test_build_falls_back_to_description_chapters(monkeypatch):
    _install_fake_libs(
        monkeypatch,
        segments=_SEGMENTS,
        pci=None,
        described=[Chapter(120, "From notes", None, None)],
    )
    fetcher = _FetcherStub({"http://t/ep.srt": "ignored"})
    result = await PodcastTranscriptProducer(fetcher=fetcher).build(
        episode_url="http://t/ep",
        transcript_url="http://t/ep.srt",
        transcript_type="application/x-subrip",
        chapters_url=None,
        description="00:00 Intro\n02:00 From notes",
    )
    envelope = PodcastTranscriptEnvelope.model_validate_json(result.body)
    assert [s.title for s in envelope.sections] == [None, "From notes"]


async def test_build_without_chapters_yields_no_sections(monkeypatch):
    _install_fake_libs(monkeypatch, segments=_SEGMENTS, pci=None, described=None)
    fetcher = _FetcherStub({"http://t/ep.vtt": "ignored"})
    result = await PodcastTranscriptProducer(fetcher=fetcher).build(
        episode_url="http://t/ep",
        transcript_url="http://t/ep.vtt",
        transcript_type="text/vtt",
        chapters_url=None,
        description=None,
    )
    envelope = PodcastTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.sections == ()
    assert envelope.transcript.startswith("Welcome to the show.")
