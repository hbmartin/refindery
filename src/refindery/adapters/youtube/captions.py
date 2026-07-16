"""Caption payload parsing: json3 (preferred) and WebVTT (fallback) to text.

json3 gives clean segment text; auto-generated VTT carries rolling duplicate
lines, so both parsers drop consecutive repeats.
"""

import html
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

_TAG = re.compile(r"<[^>]*>")
_VTT_HEADER_PREFIXES = ("WEBVTT", "NOTE", "STYLE", "REGION", "Kind:", "Language:")
_VTT_TIMESTAMP = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d(?:\.\d+)?)$"
)


@dataclass(frozen=True, slots=True)
class ParsedCaptionTranscript:
    """Transcript text plus timed character offsets for retained caption lines."""

    text: str
    offsets: tuple[tuple[int, float], ...]


class _Json3Seg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    utf8: str = ""


class _Json3Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segs: list[_Json3Seg] | None = None
    start_ms: FiniteFloat | None = Field(default=None, alias="tStartMs", ge=0)


class _Json3Transcript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[_Json3Event] = Field(default_factory=list)


def _joined_lines(
    lines: list[tuple[str, float | None]],
) -> ParsedCaptionTranscript:
    kept: list[str] = []
    offsets: list[tuple[int, float]] = []
    cursor = 0
    for line, start_time_s in lines:
        if (cleaned := line.strip()) and (not kept or kept[-1] != cleaned):
            kept.append(cleaned)
            if start_time_s is not None:
                offsets.append((cursor, start_time_s))
            cursor += len(cleaned) + 1
    return ParsedCaptionTranscript(text="\n".join(kept), offsets=tuple(offsets))


def parse_json3(raw: str) -> ParsedCaptionTranscript:
    """Parse json3 captions while preserving each retained event's start time."""
    transcript = _Json3Transcript.model_validate_json(raw)
    lines = [
        (
            "".join(seg.utf8 for seg in event.segs),
            float(event.start_ms) / 1_000 if event.start_ms is not None else None,
        )
        for event in transcript.events
        if event.segs
    ]
    return _joined_lines(lines)


def transcript_from_json3(raw: str) -> str:
    """Join json3 caption events into de-duplicated plain text lines."""
    return parse_json3(raw).text


def parse_vtt(raw: str) -> ParsedCaptionTranscript:
    """Parse WebVTT text while preserving the start time of each cue line."""
    lines: list[tuple[str, float | None]] = []
    cue_start_s: float | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            cue_start_s = None
            continue
        if stripped.startswith(_VTT_HEADER_PREFIXES):
            continue
        if "-->" in stripped:
            cue_start_s = _vtt_seconds(stripped.split("-->", maxsplit=1)[0].strip())
            continue
        if stripped.isdigit():
            continue
        lines.append((html.unescape(_TAG.sub("", stripped)), cue_start_s))
    return _joined_lines(lines)


def transcript_from_vtt(raw: str) -> str:
    """Strip WebVTT headers, timings, and inline tags down to text lines."""
    return parse_vtt(raw).text


def _vtt_seconds(value: str) -> float | None:
    """Convert a WebVTT timestamp to seconds, or return None when malformed."""
    if (match := _VTT_TIMESTAMP.fullmatch(value)) is None:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = float(match.group("seconds"))
    return hours * 3_600 + minutes * 60 + seconds
