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


def test_video_result_accepts_nullable_yt_dlp_inventories() -> None:
    result = _validate_video_info(
        {
            "subtitles": None,
            "automatic_captions": None,
            "requested_downloads": None,
            "chapters": None,
        },
        url="https://youtu.be/example",
    )

    assert result.subtitles is None
    assert result.chapters is None


def test_video_result_rejects_non_finite_chapter_start() -> None:
    with pytest.raises(FetchFailedError, match="invalid yt-dlp video result"):
        _validate_video_info(
            {"chapters": [{"title": "Bad", "start_time": float("nan")}]},
            url="https://youtu.be/example",
        )


def test_listing_result_rejects_malformed_entries() -> None:
    with pytest.raises(FetchFailedError, match="invalid yt-dlp listing result"):
        _validate_listing_info(
            {"entries": [{"id": ["not-a-string"]}]},
            url="https://youtube.com/@example/videos",
        )
