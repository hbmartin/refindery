"""Real yt-dlp caption fetch against a stable public video (scheduled CI only).

Run with ``uv run pytest -m "slow and external"
tests/integration/test_youtube_captions_real.py`` (needs the youtube extra).
"""

import pytest

from refindery.adapters.youtube.backend import YtDlpBackend
from refindery.adapters.youtube.caption_fetcher import YoutubeCaptionFetcher
from refindery.adapters.youtube.envelope import (
    YOUTUBE_TRANSCRIPT_CONTENT_TYPE,
    YoutubeTranscriptEnvelope,
)

pytestmark = [pytest.mark.slow, pytest.mark.external]

# "Me at the zoo" — the first YouTube video; stable, short, captioned.
VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


async def test_real_captioned_video_yields_transcript_and_title():
    pytest.importorskip("yt_dlp")
    fetcher = YoutubeCaptionFetcher(
        backend=YtDlpBackend(),
        transcriber=None,
        langs=("en", "en-US"),
        allow_auto=True,
        transcribe_fallback=False,
        timeout_s=60.0,
    )
    result = await fetcher.fetch(VIDEO_URL)
    assert result.content_type == YOUTUBE_TRANSCRIPT_CONTENT_TYPE
    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.title
    assert len(envelope.transcript) > 20
    assert envelope.video_id == "jNQXAC9IVRw"


async def test_real_channel_flat_extraction_lists_videos():
    pytest.importorskip("yt_dlp")
    backend = YtDlpBackend()
    entries = await backend.list_entries(
        "https://www.youtube.com/@YouTube/videos", max_entries=5, timeout_s=60.0
    )
    assert 1 <= len(entries) <= 5
    assert all(
        entry.url.startswith("https://www.youtube.com/watch?v=") for entry in entries
    )
