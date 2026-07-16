"""Tests for YouTube URL detection."""

import pytest

from refindery.domain.youtube import is_youtube_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=abc",
        "https://m.youtube.com/watch?v=abc",
        "https://music.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.youtube.com/shorts/abcDEF123",
        "https://www.youtube-nocookie.com/embed/abc",
        "http://YOUTUBE.com/watch?v=abc",
    ],
)
def test_is_youtube_url_true(url):
    assert is_youtube_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc",
        "https://example.com/youtube",
        "https://vimeo.com/12345",
        "https://notyoutube.com/watch?v=abc",
        "https://evilyoutube.com/watch?v=abc",
        "https://youtube.com.evil.com/watch?v=abc",
        "not a url",
        "",
    ],
)
def test_is_youtube_url_false(url):
    assert is_youtube_url(url) is False
