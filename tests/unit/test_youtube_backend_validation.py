"""Validation boundaries for untyped yt-dlp results."""

import pytest

from refindery.adapters.youtube.backend import (
    _validate_listing_info,
    _validate_video_info,
)
from refindery.domain.errors import FetchFailedError


def test_video_result_rejects_malformed_caption_inventory() -> None:
    with pytest.raises(FetchFailedError, match="invalid yt-dlp video result"):
        _validate_video_info({"subtitles": []}, url="https://youtu.be/example")


def test_listing_result_rejects_malformed_entries() -> None:
    with pytest.raises(FetchFailedError, match="invalid yt-dlp listing result"):
        _validate_listing_info(
            {"entries": [{"id": ["not-a-string"]}]},
            url="https://youtube.com/@example/videos",
        )
