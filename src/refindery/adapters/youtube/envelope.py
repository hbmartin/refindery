"""The transcript envelope handed from the YouTube fetcher to its extractor.

A synthetic content type routes the envelope through the normal
ExtractionRouter dispatch, which is how the video title reaches
``handle_fetch_and_index`` without changing ``FetchResult`` or the indexing
service. Imports only pydantic, so it is always importable (the extractor is
registered unconditionally; only the fetcher needs yt-dlp).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

YOUTUBE_TRANSCRIPT_CONTENT_TYPE = "application/x-youtube-transcript+json"


class TranscriptSource(StrEnum):
    """Where the transcript text came from."""

    MANUAL_CAPTIONS = "manual_captions"
    AUTO_CAPTIONS = "auto_captions"
    TRANSCRIBED = "transcribed"


class YoutubeTranscriptEnvelope(BaseModel):
    """Fetcher output: the transcript plus video metadata."""

    model_config = ConfigDict(frozen=True)

    video_id: str | None
    title: str | None
    language: str | None
    source: TranscriptSource
    transcript: str
    source_url: str
