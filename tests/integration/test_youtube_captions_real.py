"""Real yt-dlp caption fetch against a stable public video.

Marked ``slow`` and ``external``: skipped by the default suite
(``-m 'not external'``) and the merge gate; run by the scheduled
youtube-captions workflow with the ``youtube`` extra installed.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.external]

# "Me at the zoo" — the first YouTube video; permanent, English speech.
_STABLE_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


async def test_real_youtube_captions():
    pytest.importorskip("yt_dlp")
    from refindery.adapters.extraction.youtube_envelope import (
        YoutubeTranscriptEnvelope,
    )
    from refindery.adapters.extraction.youtube_fetcher import YoutubeCaptionFetcher

    fetcher = YoutubeCaptionFetcher(
        langs=("en", "en-US", "en-GB"),
        allow_auto=True,
        transcribe_fallback=False,
        timeout_s=30.0,
    )
    result = await fetcher.fetch(_STABLE_VIDEO)
    envelope = YoutubeTranscriptEnvelope.model_validate_json(result.body)
    assert envelope.transcript.strip()
    assert envelope.title
