"""The transcript envelope handed from the podcast producer to its extractor.

A synthetic content type routes the envelope through the normal
ExtractionRouter dispatch (mirroring the YouTube design), carrying the
concatenated transcript plus resolved chapter sections into the indexing path
without changing ``FetchResult`` or the indexing service. Imports only pydantic,
so it is always importable (the extractor is registered unconditionally; only
the producer needs the ``podcast`` extra).
"""

from pydantic import BaseModel, ConfigDict

PODCAST_TRANSCRIPT_CONTENT_TYPE = "application/x-podcast-transcript+json"


class PodcastSection(BaseModel):
    """A resolved chapter span over the transcript text."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    char_start: int
    char_end: int
    start_time_s: float | None = None


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
