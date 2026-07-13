"""Extractor for the audio transcript envelope (core, no optional deps)."""

from pydantic import ValidationError

from refindery.adapters.transcription.envelope import (
    AUDIO_TRANSCRIPT_CONTENT_TYPE,
    AudioTranscriptEnvelope,
)
from refindery.domain.models import ExtractedContent


class AudioTranscriptExtractor:
    """Unwraps the envelope produced by AudioTranscriptFetcher."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({AUDIO_TRANSCRIPT_CONTENT_TYPE})

    async def extract(
        self,
        *,
        raw: bytes,
        charset: str | None,  # noqa: ARG002 — port signature; the envelope is always UTF-8 JSON
    ) -> ExtractedContent:
        """Validate the envelope and surface the transcript."""
        try:
            envelope = AudioTranscriptEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            msg = f"malformed audio transcript envelope: {exc}"
            raise ValueError(msg) from exc
        return ExtractedContent(body_text=envelope.transcript, title=envelope.title)
