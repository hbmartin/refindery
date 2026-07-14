"""The transcript envelope handed from the audio fetcher to its extractor.

A synthetic content type routes the envelope through the normal
ExtractionRouter dispatch, which is how transcript metadata reaches
``handle_fetch_and_index`` without changing ``FetchResult`` or the indexing
service. Imports only pydantic, so it is always importable (the extractor is
registered unconditionally; only the fetcher needs a Whisper extra).
"""

from pydantic import BaseModel, ConfigDict

AUDIO_TRANSCRIPT_CONTENT_TYPE = "application/x-audio-transcript+json"


class AudioTranscriptEnvelope(BaseModel):
    """Fetcher output: the transcript plus audio source metadata."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    language: str | None
    transcript: str
    source_url: str
