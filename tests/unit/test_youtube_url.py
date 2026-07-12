"""Table-driven tests for YouTube URL detection, classification, and rewrites."""

import pytest

from refindery.domain.youtube import (
    YoutubeUrlKind,
    classify_youtube_url,
    is_youtube_video_url,
    normalize_listing_url,
    rewrite_to_watch,
    video_id_from_url,
)

VIDEO_ID = "dQw4w9WgXcQ"

VIDEO_URLS = [
    f"https://www.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com/watch?v={VIDEO_ID}&t=42s",
    f"https://m.youtube.com/watch?v={VIDEO_ID}",
    f"https://music.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?t=30",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://www.youtube.com/live/{VIDEO_ID}",
]

NON_VIDEO_URLS = [
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    "https://example.com/youtube/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch",
    "https://www.youtube.com/watch?v=tooshort",
    "https://www.youtube.com/playlist?list=PLabc",
    "https://www.youtube.com/@somecreator",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://youtu.be/",
    "youtube.com/watch?v=dQw4w9WgXcQ",  # schemeless
    "",
]


@pytest.mark.parametrize("url", VIDEO_URLS)
def test_video_urls_detected(url):
    assert is_youtube_video_url(url)
    assert video_id_from_url(url) == VIDEO_ID


@pytest.mark.parametrize("url", NON_VIDEO_URLS)
def test_non_video_urls_rejected(url):
    assert not is_youtube_video_url(url)


CLASSIFY_CASES = [
    (f"https://youtu.be/{VIDEO_ID}", YoutubeUrlKind.VIDEO),
    ("https://www.youtube.com/playlist?list=PLabc", YoutubeUrlKind.PLAYLIST),
    ("https://www.youtube.com/watch?list=PLabc", YoutubeUrlKind.PLAYLIST),
    (f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLabc", YoutubeUrlKind.VIDEO),
    ("https://www.youtube.com/@somecreator", YoutubeUrlKind.CHANNEL),
    ("https://www.youtube.com/@somecreator/videos", YoutubeUrlKind.CHANNEL),
    ("https://www.youtube.com/channel/UCabcdef", YoutubeUrlKind.CHANNEL),
    ("https://www.youtube.com/c/SomeName", YoutubeUrlKind.CHANNEL),
    ("https://www.youtube.com/user/SomeName", YoutubeUrlKind.CHANNEL),
    ("https://www.youtube.com/feed/history", None),
    ("https://example.com/@somecreator", None),
]


@pytest.mark.parametrize(("url", "expected"), CLASSIFY_CASES)
def test_classify(url, expected):
    assert classify_youtube_url(url) == expected


NORMALIZE_CASES = [
    # watch?list= becomes the explicit playlist page
    (
        "https://www.youtube.com/watch?list=PLabc",
        "https://www.youtube.com/playlist?list=PLabc",
    ),
    # playlist page passes through
    (
        "https://www.youtube.com/playlist?list=PLabc",
        "https://www.youtube.com/playlist?list=PLabc",
    ),
    # bare channel forms get /videos appended
    (
        "https://www.youtube.com/@somecreator",
        "https://www.youtube.com/@somecreator/videos",
    ),
    (
        "https://www.youtube.com/channel/UCabcdef/",
        "https://www.youtube.com/channel/UCabcdef/videos",
    ),
    # a channel tab already present is preserved
    (
        "https://www.youtube.com/@somecreator/streams",
        "https://www.youtube.com/@somecreator/streams",
    ),
]


@pytest.mark.parametrize(("url", "expected"), NORMALIZE_CASES)
def test_normalize_listing_url(url, expected):
    assert normalize_listing_url(url) == expected


def test_rewrite_folds_video_forms_to_watch():
    assert rewrite_to_watch(host="youtu.be", path=f"/{VIDEO_ID}", query="t=30") == (
        "youtube.com",
        "/watch",
        f"v={VIDEO_ID}",
    )
    assert rewrite_to_watch(
        host="m.youtube.com", path=f"/shorts/{VIDEO_ID}", query=""
    ) == ("youtube.com", "/watch", f"v={VIDEO_ID}")


def test_rewrite_leaves_watch_and_foreign_urls_alone():
    watch = rewrite_to_watch(host="youtube.com", path="/watch", query=f"v={VIDEO_ID}")
    assert watch == ("youtube.com", "/watch", f"v={VIDEO_ID}")
    foreign = rewrite_to_watch(host="example.com", path="/shorts/x", query="a=1")
    assert foreign == ("example.com", "/shorts/x", "a=1")
