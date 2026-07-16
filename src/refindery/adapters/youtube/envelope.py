"""The transcript envelope handed from the YouTube fetcher to its extractor.

A synthetic content type routes the envelope through the normal
ExtractionRouter dispatch, which is how the video title reaches
``handle_fetch_and_index`` without changing ``FetchResult`` or the indexing
service. Imports only pydantic, so it is always importable (the extractor is
registered unconditionally; only the fetcher needs yt-dlp).
"""

from enum import StrEnum
from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

YOUTUBE_TRANSCRIPT_CONTENT_TYPE = "application/x-youtube-transcript+json"


class TranscriptSource(StrEnum):
    """Where the transcript text came from."""

    MANUAL_CAPTIONS = "manual_captions"
    AUTO_CAPTIONS = "auto_captions"
    TRANSCRIBED = "transcribed"


class YoutubeSection(BaseModel):
    """A resolved YouTube chapter span over the transcript text."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    start_time_s: FiniteFloat | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _positive_width(self) -> Self:
        if self.char_end <= self.char_start:
            msg = "section char_end must be greater than char_start"
            raise ValueError(msg)
        return self


class YoutubeTranscriptEnvelope(BaseModel):
    """Fetcher output: the transcript plus video metadata."""

    model_config = ConfigDict(frozen=True)

    video_id: str | None
    title: str | None
    language: str | None
    source: TranscriptSource
    transcript: str
    sections: tuple[YoutubeSection, ...] = ()
    source_url: str

    @model_validator(mode="after")
    def _sections_tile_transcript(self) -> Self:
        if not self.sections:
            return self
        tiled = (
            self.sections[0].char_start == 0
            and self.sections[-1].char_end == len(self.transcript)
            and all(
                left.char_end == right.char_start
                for left, right in pairwise(self.sections)
            )
        )
        if not tiled:
            msg = "youtube sections must tile the complete transcript"
            raise ValueError(msg)
        return self
