"""Extractor for the YouTube transcript envelope (core, no optional deps)."""

from pydantic import ValidationError

from refindery.adapters.youtube.envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    YoutubeTranscriptEnvelope,
)
from refindery.domain.models import ExtractedContent


class YoutubeTranscriptExtractor:
    """Unwraps the envelope produced by YoutubeCaptionFetcher."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({YOUTUBE_TRANSCRIPT_CONTENT_TYPE})

    async def extract(
        self,
        *,
        raw: bytes,
        charset: str | None,  # noqa: ARG002 — port signature; the envelope is always UTF-8 JSON
    ) -> ExtractedContent:
        """Validate the envelope and surface transcript + video title."""
        try:
            envelope = YoutubeTranscriptEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            msg = f"malformed youtube transcript envelope: {exc}"
            raise ValueError(msg) from exc
        return ExtractedContent(body_text=envelope.transcript, title=envelope.title)
