"""Adapter-safe vector space names derived from public model ids."""

import hashlib
import re

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_SLUG = 48


def safe_model_name(*, prefix: str, model_id: str) -> str:
    """Readable slug plus stable hash for adapter table/vector identifiers."""
    slug = _SAFE_CHARS.sub("_", model_id).strip("._-").lower()
    if not slug:
        slug = "model"
    digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{slug[:_MAX_SLUG]}_{digest}"
