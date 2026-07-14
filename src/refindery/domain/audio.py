"""Pure audio URL and MIME predicates for transcribable audio.

Used by the fetch router (audio URLs get the transcript fetcher instead of
the plain HTTP fetcher), podcast discovery, download validation, and watch
creation (a podcast watch takes a feed URL, not a direct audio file).
"""

from pathlib import PurePosixPath
from urllib.parse import urlsplit

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"}
)
GENERIC_AUDIO_CONTENT_TYPES = frozenset({"application/octet-stream", "application/ogg"})


def is_audio_content_type(content_type: str) -> bool:
    """Accept normalized audio MIME types and generic podcast CDN types."""
    normalized = content_type.split(";", maxsplit=1)[0].strip().lower()
    return normalized.startswith("audio/") or normalized in GENERIC_AUDIO_CONTENT_TYPES


def is_audio_url(url: str) -> bool:
    """Report whether the URL path ends in a known audio file extension."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return PurePosixPath(parts.path).suffix.lower() in AUDIO_EXTENSIONS
