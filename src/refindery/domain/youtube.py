"""Pure YouTube URL logic: detection, classification, and canonical rewrites.

Used by canonicalization (folding youtu.be/shorts/live forms into the
``watch?v=`` page), by the fetch router (only *video* URLs get the caption
fetcher), and by watch creation (only playlists/channels are watchable).
"""

import re
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_PATH_PREFIXES = ("shorts", "live")
_CHANNEL_SEGMENTS = frozenset({"channel", "c", "user"})


class YoutubeUrlKind(StrEnum):
    """What a YouTube URL points at."""

    VIDEO = "video"
    PLAYLIST = "playlist"
    CHANNEL = "channel"


def is_youtube_host(host: str) -> bool:
    """Match youtu.be, youtube.com, youtube-nocookie.com, and subdomains."""
    bare = host.removeprefix("www.")
    if bare == "youtu.be":
        return True
    return any(
        bare == domain or bare.endswith(f".{domain}")
        for domain in ("youtube.com", "youtube-nocookie.com")
    )


def _path_segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _video_id_from_parts(host: str, path: str, query: str) -> str | None:
    segments = _path_segments(path)
    candidate: str | None = None
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if segments[:1] == ["watch"]:
            candidate = next(iter(parse_qs(query).get("v", [])), None)
        elif len(segments) >= 2 and segments[0] in _VIDEO_PATH_PREFIXES:
            candidate = segments[1]
    if candidate is not None and _VIDEO_ID.match(candidate):
        return candidate
    return None


def video_id_from_url(url: str) -> str | None:
    """Extract the 11-char video id from watch/youtu.be/shorts/live URLs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.hostname is None:
        return None
    host = parts.hostname.lower().removeprefix("www.")
    return _video_id_from_parts(host, parts.path, parts.query)


def is_youtube_video_url(url: str) -> bool:
    """Report whether the URL identifies a single video (routing predicate)."""
    return video_id_from_url(url) is not None


def classify_youtube_url(url: str) -> YoutubeUrlKind | None:
    """Classify a YouTube URL as video, playlist, or channel; None otherwise."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.hostname is None or not is_youtube_host(parts.hostname.lower()):
        return None
    host = parts.hostname.lower().removeprefix("www.")
    if _video_id_from_parts(host, parts.path, parts.query) is not None:
        return YoutubeUrlKind.VIDEO
    segments = _path_segments(parts.path)
    has_list = bool(parse_qs(parts.query).get("list"))
    if segments[:1] in (["playlist"], ["watch"]) and has_list:
        return YoutubeUrlKind.PLAYLIST
    if segments and (segments[0].startswith("@") or segments[0] in _CHANNEL_SEGMENTS):
        return YoutubeUrlKind.CHANNEL
    return None


def normalize_listing_url(url: str) -> str:
    """Fetch-time form for yt-dlp flat extraction; never persisted.

    ``watch?list=`` becomes the explicit playlist page; a bare channel URL
    gets its ``/videos`` tab appended so extraction deterministically yields
    the uploads playlist.
    """
    kind = classify_youtube_url(url)
    parts = urlsplit(url)
    if kind is YoutubeUrlKind.PLAYLIST:
        list_ids = parse_qs(parts.query).get("list", [])
        if list_ids:
            return f"https://www.youtube.com/playlist?list={list_ids[0]}"
        return url
    if kind is YoutubeUrlKind.CHANNEL:
        segments = _path_segments(parts.path)
        expected = 1 if segments[0].startswith("@") else 2
        if len(segments) == expected:
            return url.rstrip("/") + "/videos"
    return url


def rewrite_to_watch(*, host: str, path: str, query: str) -> tuple[str, str, str]:
    """Fold youtu.be/<id>, /shorts/<id>, and /live/<id> into watch?v=<id>.

    Called by ``canonicalize()`` with the www-stripped lowercase host. The
    original query is discarded on rewrite (``t=``/``si=`` would be dropped
    by the youtube.com keep-params rule anyway); non-matching URLs pass
    through unchanged — ``/watch`` paths in particular, so existing stored
    canonical URLs are unaffected.
    """
    if not is_youtube_host(host):
        return host, path, query
    video_id = _video_id_from_parts(host, path, query)
    if video_id is None:
        return host, path, query
    segments = _path_segments(path)
    if host == "youtu.be" or segments[:1] != ["watch"]:
        return "youtube.com", "/watch", f"v={video_id}"
    return host, path, query
