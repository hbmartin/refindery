"""ContentExtractor for the synthetic YouTube transcript envelope.

Pure stdlib + pydantic, so this extractor is always registered (unlike the
yt-dlp fetcher, which is gated on the ``youtube`` extra). It validates the
envelope the fetcher produced and yields the transcript text plus the video
title.
"""

from refindery.adapters.extraction.youtube_envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    YoutubeTranscriptEnvelope,
)
from refindery.domain.models import ExtractedContent


class YoutubeTranscriptExtractor:
    """ContentExtractor for ``application/x-youtube-transcript+json``."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({YOUTUBE_TRANSCRIPT_CONTENT_TYPE})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:  # noqa: ARG002 — port signature; the envelope is always UTF-8 JSON
        """Validate the transcript envelope and return body text and title."""
        envelope = YoutubeTranscriptEnvelope.model_validate_json(raw)
        return ExtractedContent(body_text=envelope.transcript, title=envelope.title)
