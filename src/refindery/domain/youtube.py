"""Pure YouTube URL detection.

A submitted URL is routed to the caption/transcript fetcher instead of the
generic HTML fetcher when its host is a YouTube host. Detection is by host
suffix only, so ``www.``/``m.``/``music.`` subdomains and ``/shorts/<id>``
paths are all covered.
"""

from urllib.parse import urlsplit

_YOUTUBE_HOSTS = frozenset({"youtube.com", "youtube-nocookie.com", "youtu.be"})


def is_youtube_url(url: str) -> bool:
    """Whether ``url`` points at a YouTube video host.

    Returns ``False`` for malformed URLs or non-YouTube hosts. A bare
    substring like ``example.com/youtube`` is not matched — only the host is
    considered.
    """
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return False
    if host is None:
        return False
    host = host.lower()
    return any(host == d or host.endswith(f".{d}") for d in _YOUTUBE_HOSTS)
