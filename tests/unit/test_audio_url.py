"""is_audio_url: extension-based routing predicate for transcribable audio."""

import pytest

from refindery.domain.audio import AUDIO_EXTENSIONS, is_audio_url


@pytest.mark.parametrize("extension", sorted(AUDIO_EXTENSIONS))
def test_every_known_extension_matches(extension: str) -> None:
    assert is_audio_url(f"https://cdn.example/episodes/42{extension}")


def test_uppercase_extension_matches() -> None:
    assert is_audio_url("https://cdn.example/episodes/42.MP3")


def test_query_string_does_not_hide_the_extension() -> None:
    assert is_audio_url("https://cdn.example/ep.mp3?updated=1700000000&token=abc")


def test_extension_in_query_only_does_not_match() -> None:
    assert not is_audio_url("https://cdn.example/download?file=ep.mp3")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "https://example.com/video.mp4",
        "https://example.com/notes.txt",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "http://[broken",
        "",
    ],
)
def test_non_audio_urls_do_not_match(url: str) -> None:
    assert not is_audio_url(url)
