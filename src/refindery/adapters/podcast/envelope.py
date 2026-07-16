"""The transcript envelope handed from the podcast producer to its extractor.

A synthetic content type routes the envelope through the normal
ExtractionRouter dispatch (mirroring the YouTube design), carrying the
concatenated transcript plus resolved chapter sections into the indexing path
without changing ``FetchResult`` or the indexing service. Imports only pydantic,
so it is always importable (the extractor is registered unconditionally; only
the producer needs the ``podcast`` extra).
"""

from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PODCAST_TRANSCRIPT_CONTENT_TYPE = "application/x-podcast-transcript+json"


class PodcastSection(BaseModel):
    """A resolved chapter span over the transcript text."""

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


class PodcastTranscriptEnvelope(BaseModel):
    """Producer output: the transcript plus resolved chapter sections.

    ``sections`` is empty when the episode has no chapters, in which case the
    extractor surfaces ``sections=None`` and chunking stays flat.
    """

    model_config = ConfigDict(frozen=True)

    episode_url: str
    title: str | None
    language: str | None
    transcript: str
    sections: tuple[PodcastSection, ...] = ()
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
            msg = "podcast sections must tile the complete transcript"
            raise ValueError(msg)
        return self
