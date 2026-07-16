"""Podcast transcript producer: published transcript + chapters -> envelope.

Fetches a Podcasting 2.0 ``<podcast:transcript>`` (and, when present, a
``<podcast:chapters>`` JSON file or show-notes timestamps), normalizes the
transcript to timed segments via ``podcast-transcript-convert``, resolves
chapters via ``podcast-chapter-tools``, snaps each chapter start time onto a
transcript char offset, and emits a ``FetchResult`` carrying the podcast
transcript envelope.

Requires the ``podcast`` extra (podcast-transcript-convert, podcast-chapter-tools);
the third-party imports are deferred so the module stays importable without it.
"""

import json
import logging
from importlib.util import find_spec

from refindery.adapters.podcast.envelope import (
    PODCAST_TRANSCRIPT_CONTENT_TYPE,
    PodcastSection,
    PodcastTranscriptEnvelope,
)
from refindery.application.ports.content_extractor import Fetcher, FetchResult
from refindery.domain.errors import ExtractionUnavailableError

logger = logging.getLogger(__name__)

_SEGMENT_SEPARATOR = "\n"

# Transcript MIME type (from <podcast:transcript type=...>) -> converter format.
_MIME_FORMATS = {
    "text/vtt": "vtt",
    "application/vtt": "vtt",
    "text/srt": "srt",
    "application/srt": "srt",
    "application/x-subrip": "srt",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/json": "json",
    "application/x-json": "json",
}

# A timed transcript segment: (body_text, start_time_seconds).
type _Segment = tuple[str, float]
# A chapter marker: (start_time_seconds, title).
type _Chapter = tuple[float, str | None]


class PodcastTranscriptProducer:
    """Builds a podcast transcript envelope from feed-discovered URLs."""

    def __init__(self, *, fetcher: Fetcher) -> None:
        for module in ("podcast_transcript_convert", "podcast_chapter_tools"):
            if find_spec(module) is None:
                raise ExtractionUnavailableError(
                    content_type=PODCAST_TRANSCRIPT_CONTENT_TYPE, extra="podcast"
                )
        self._fetcher = fetcher

    async def build(
        self,
        *,
        episode_url: str,
        transcript_url: str,
        transcript_type: str | None,
        chapters_url: str | None,
        description: str | None,
    ) -> FetchResult:
        """Fetch and normalize the transcript + chapters into an envelope."""
        transcript_text = await self._fetch_text(transcript_url)
        segments = _to_segments(text=transcript_text, mime=transcript_type)
        body_text, offsets = _concatenate(segments)
        chapters = await self._resolve_chapters(
            chapters_url=chapters_url, description=description
        )
        sections = _sections_from_chapters(
            chapters=chapters, offsets=offsets, body_len=len(body_text)
        )
        envelope = PodcastTranscriptEnvelope(
            episode_url=episode_url,
            title=None,
            language=None,
            transcript=body_text,
            sections=sections,
            source_url=transcript_url,
        )
        return FetchResult(
            url=episode_url,
            final_url=episode_url,
            status_code=200,
            content_type=PODCAST_TRANSCRIPT_CONTENT_TYPE,
            charset="utf-8",
            body=envelope.model_dump_json().encode("utf-8"),
        )

    async def _fetch_text(self, url: str) -> str:
        result = await self._fetcher.fetch(url)
        return result.body.decode(result.charset or "utf-8", errors="replace")

    async def _resolve_chapters(
        self, *, chapters_url: str | None, description: str | None
    ) -> list[_Chapter]:
        import podcast_chapter_tools as pct  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]

        if chapters_url and (chapters := await self._pci_chapters(chapters_url, pct)):
            return chapters
        if description:
            derived = pct.extract_description_chapters(description, strip_html=True)
            if derived:
                return _normalize(pct, derived)
        return []

    async def _pci_chapters(self, chapters_url: str, pct: object) -> list[_Chapter]:
        raw = await self._fetch_text(chapters_url)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("podcast chapters at %s is not valid JSON", chapters_url)
            return []
        if not isinstance(data, dict):
            return []
        chapters = pct.extract_pci_chapters(data)  # ty: ignore[unresolved-attribute]  # pyrefly: ignore[missing-attribute]
        return _normalize(pct, chapters) if chapters else []


def _normalize(pct: object, chapters: object) -> list[_Chapter]:
    """Sort/dedupe via podcast-chapter-tools, then flatten to (start_s, title)."""
    normalized = pct.normalize_chapters(chapters)  # ty: ignore[unresolved-attribute]  # pyrefly: ignore[missing-attribute]
    return [(float(chapter.start), chapter.title) for chapter in normalized]


def _to_segments(*, text: str, mime: str | None) -> list[_Segment]:
    """Convert transcript text to (body, start_time_s) pairs via the converters."""
    podcast_dict = _transcript_to_dict(text=text, mime=mime)
    raw_segments = podcast_dict.get("segments")
    if not isinstance(raw_segments, list):
        return []
    segments: list[_Segment] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        body = seg.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        start = seg.get("startTime")
        start_s = float(start) if isinstance(start, int | float) else 0.0
        segments.append((body.strip(), start_s))
    return segments


def _transcript_to_dict(*, text: str, mime: str | None) -> dict[str, object]:
    fmt = _format_from_mime(mime)
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    from podcast_transcript_convert.converters import (  # noqa: PLC0415 — optional extra  # ty: ignore[unresolved-import]  # pyrefly: ignore[missing-import]
        html_to_json,
        srt_to_json,
        vtt_to_json,
    )

    converters = {
        "vtt": vtt_to_json.vtt_to_podcast_dict,
        "srt": srt_to_json.srt_to_podcast_dict,
        "html": html_to_json.html_to_podcast_dict,
    }
    return converters[fmt](text)


def _format_from_mime(mime: str | None) -> str:
    if mime:
        base = mime.split(";", maxsplit=1)[0].strip().lower()
        if base in _MIME_FORMATS:
            return _MIME_FORMATS[base]
    logger.info("unknown transcript type %r; defaulting to vtt", mime)
    return "vtt"


def _concatenate(segments: list[_Segment]) -> tuple[str, list[tuple[int, float]]]:
    """Join segment bodies, recording each segment's (char_start, start_time_s)."""
    parts: list[str] = []
    offsets: list[tuple[int, float]] = []
    cursor = 0
    for body, start_s in segments:
        offsets.append((cursor, start_s))
        parts.append(body)
        cursor += len(body) + len(_SEGMENT_SEPARATOR)
    return _SEGMENT_SEPARATOR.join(parts), offsets


def _sections_from_chapters(
    *,
    chapters: list[_Chapter],
    offsets: list[tuple[int, float]],
    body_len: int,
) -> tuple[PodcastSection, ...]:
    """Map chapter start times onto transcript char offsets as section spans."""
    if not chapters or not offsets:
        return ()
    boundaries = sorted(
        (_char_for_time(offsets=offsets, time_s=start_s), title, start_s)
        for start_s, title in chapters
    )
    # Capture any pre-first-chapter transcript as a leading untitled section so
    # no text is dropped (the chunker only chunks within section spans).
    if boundaries[0][0] > 0:
        boundaries.insert(0, (0, None, 0.0))
    sections: list[PodcastSection] = []
    for index, (char_start, title, start_s) in enumerate(boundaries):
        char_end = boundaries[index + 1][0] if index + 1 < len(boundaries) else body_len
        if char_end <= char_start:
            continue  # zero-width (overlapping/beyond-transcript chapters) -> drop
        sections.append(
            PodcastSection(
                title=title,
                char_start=char_start,
                char_end=char_end,
                start_time_s=start_s,
            )
        )
    return tuple(sections)


def _char_for_time(*, offsets: list[tuple[int, float]], time_s: float) -> int:
    """Char offset of the first segment starting at or after ``time_s``."""
    for char_start, seg_start in offsets:
        if seg_start >= time_s:
            return char_start
    return offsets[-1][0]
