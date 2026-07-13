"""Pure audio URL logic: the routing predicate for transcribable audio.

Used by the fetch router (audio URLs get the transcript fetcher instead of
the plain HTTP fetcher) and by watch creation (a podcast watch takes a feed
URL, not a direct audio file).
"""

from pathlib import PurePosixPath
from urllib.parse import urlsplit

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"}
)


def is_audio_url(url: str) -> bool:
    """Report whether the URL path ends in a known audio file extension."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return PurePosixPath(parts.path).suffix.lower() in AUDIO_EXTENSIONS
