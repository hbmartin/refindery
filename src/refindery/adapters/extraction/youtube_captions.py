"""Turn raw YouTube caption payloads into clean plain text.

``json3`` is YouTube's timed-text JSON and is preferred: it has no rolling
duplicate lines and is trivially validated. ``parse_vtt`` is a defensive
fallback for the rare video that only offers WebVTT captions.
"""

import html
import re

from pydantic import BaseModel, ConfigDict, Field


class _Json3Seg(BaseModel):
    """One text segment within a json3 caption event."""

    model_config = ConfigDict(extra="ignore")

    utf8: str | None = None


class _Json3Event(BaseModel):
    """One json3 caption event (a cue); formatting-only events have no segs."""

    model_config = ConfigDict(extra="ignore")

    segs: list[_Json3Seg] | None = None


class _Json3Transcript(BaseModel):
    """The top-level json3 timed-text document."""

    model_config = ConfigDict(extra="ignore")

    events: list[_Json3Event] = Field(default_factory=list)


def parse_json3(payload: str) -> str:
    """Extract plain transcript text from a json3 caption payload.

    Consecutive duplicate cue lines are dropped; empty/formatting-only events
    are skipped.
    """
    doc = _Json3Transcript.model_validate_json(payload)
    lines: list[str] = []
    for event in doc.events:
        if not event.segs:
            continue
        text = "".join(seg.utf8 or "" for seg in event.segs).strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


_VTT_TAG_RE = re.compile(r"<[^>]*>")
_VTT_HEADER_PREFIXES = ("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE", "REGION")


def _is_vtt_noise(line: str) -> bool:
    """Whether a stripped VTT line is a header, timing, or cue-index line."""
    if not line or "-->" in line or line.isdigit():
        return True
    return any(line.startswith(prefix) for prefix in _VTT_HEADER_PREFIXES)


def _clean_vtt_line(line: str) -> str:
    """Strip inline VTT tags and unescape HTML entities."""
    return html.unescape(_VTT_TAG_RE.sub("", line)).strip()


def parse_vtt(payload: str) -> str:
    """Extract plain transcript text from a WebVTT caption payload.

    Headers, timing lines, and inline tags are removed; the rolling-overlap
    duplication that YouTube auto-VTT produces is collapsed.
    """
    lines: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if _is_vtt_noise(line):
            continue
        cleaned = _clean_vtt_line(line)
        if cleaned and (not lines or lines[-1] != cleaned):
            lines.append(cleaned)
    return "\n".join(lines)
