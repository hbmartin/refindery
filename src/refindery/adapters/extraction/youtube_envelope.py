"""The fetch/extract contract for YouTube transcripts.

The YouTube fetcher produces the final transcript text (from captions or
local transcription) and wraps it in a self-describing JSON envelope with a
synthetic content type. ``YoutubeTranscriptExtractor`` validates that envelope
and turns it into ``ExtractedContent`` — this is how the video title reaches
the indexing pipeline without adding a ``title`` field to ``FetchResult``.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

YOUTUBE_TRANSCRIPT_CONTENT_TYPE = "application/x-youtube-transcript+json"


class TranscriptSource(StrEnum):
    """Where a transcript came from, in descending preference."""

    MANUAL_CAPTIONS = "manual_captions"
    AUTO_CAPTIONS = "auto_captions"
    TRANSCRIBED = "transcribed"


class YoutubeTranscriptEnvelope(BaseModel):
    """Self-describing transcript payload carried in a ``FetchResult.body``."""

    model_config = ConfigDict(frozen=True)

    video_id: str | None
    title: str | None
    language: str | None
    source: TranscriptSource
    transcript: str
    source_url: str
