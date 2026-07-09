"""Content hashing for revisit detection and v2 near-duplicate collapsing."""

import hashlib


def content_hash(body_text: str) -> str:
    """Return the hex sha256 of the page body text."""
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()
