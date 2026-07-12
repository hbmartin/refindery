"""Caption payload parsing: json3 (preferred) and WebVTT (fallback) to text.

json3 gives clean segment text; auto-generated VTT carries rolling duplicate
lines, so both parsers drop consecutive repeats.
"""

import html
import re

from pydantic import BaseModel, ConfigDict

_TAG = re.compile(r"<[^>]*>")
_VTT_HEADER_PREFIXES = ("WEBVTT", "NOTE", "STYLE", "REGION", "Kind:", "Language:")


class _Json3Seg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    utf8: str = ""


class _Json3Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segs: list[_Json3Seg] | None = None


class _Json3Transcript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[_Json3Event] = []


def _joined_lines(lines: list[str]) -> str:
    kept: list[str] = []
    for line in lines:
        if (cleaned := line.strip()) and (not kept or kept[-1] != cleaned):
            kept.append(cleaned)
    return "\n".join(kept)


def transcript_from_json3(raw: str) -> str:
    """Join json3 caption events into de-duplicated plain text lines."""
    transcript = _Json3Transcript.model_validate_json(raw)
    lines = [
        "".join(seg.utf8 for seg in event.segs)
        for event in transcript.events
        if event.segs
    ]
    return _joined_lines(lines)


def transcript_from_vtt(raw: str) -> str:
    """Strip WebVTT headers, timings, and inline tags down to text lines."""
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith(_VTT_HEADER_PREFIXES)
            or "-->" in stripped
            or stripped.isdigit()
        ):
            continue
        lines.append(html.unescape(_TAG.sub("", stripped)))
    return _joined_lines(lines)
