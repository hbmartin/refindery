"""Podcast producer port: resolve a podcast episode into a transcript envelope.

Given the transcript/chapters URLs discovered from a podcast feed, the producer
fetches and normalizes them into a ``FetchResult`` carrying the synthetic
podcast-transcript content type, so the result rides the normal extraction
rails (router -> extractor -> chunker) without special-casing indexing.
"""

from typing import Protocol

from refindery.application.ports.content_extractor import FetchResult


class PodcastProducer(Protocol):
    """Builds a podcast transcript ``FetchResult`` from feed-discovered URLs."""

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
        ...
