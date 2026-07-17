"""Idempotency-key builders, one per durable job kind.

These strings are persisted dedupe keys: ``jobs.idempotency_key`` is UNIQUE
and enqueueing a duplicate is a silent no-op, so the format for an existing
kind must never change — a new format would stop deduplicating against keys
already in the ledger. Golden tests in ``tests/unit/test_job_keys.py`` lock
every format and the one-prefix-per-kind invariant.

Builders are pure; impure inputs (clock readings, fresh uuids) stay at the
call sites and are passed in.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from refindery.domain.ids import PageId, WatchId


def _key(prefix: str, *parts: str) -> str:
    """Join the kind prefix and its identifying parts with ``:``."""
    return ":".join((prefix, *parts))


def fetch_and_index_key(page_id: PageId) -> str:
    """Deferred-fetch key; keyed by page only — content is unknown until fetched."""
    return _key("fetch", page_id)


def index_page_key(*, page_id: PageId, content_hash: str) -> str:
    """Index key; a new content hash is new work, same hash dedupes."""
    return _key("index", page_id, content_hash)


def extract_entities_key(*, page_id: PageId, content_hash: str) -> str:
    """Entity-extraction key over one resolved body."""
    return _key("entities", page_id, content_hash)


def cluster_key(now: datetime) -> str:
    """Clustering-run key; every request timestamp is distinct work."""
    return _key("cluster", now.isoformat())


def canonicalize_entities_key(run_id: str) -> str:
    """Canonicalization key; one pass per cluster run."""
    return _key("canon", run_id)


def backfill_model_key(*, model_id: str, now: datetime) -> str:
    """Backfill key; re-requesting the same model later is distinct work."""
    return _key("backfill", model_id, now.isoformat())


def purge_vectors_key(page_ids: Sequence[PageId]) -> str:
    """Purge key; order-insensitive digest of the page-id set."""
    digest = hashlib.sha256(",".join(sorted(page_ids)).encode()).hexdigest()[:16]
    return _key("purge", digest)


def eval_replay_key(token: UUID) -> str:
    """Eval-replay key; callers pass a fresh uuid so replays never dedupe."""
    return _key("eval-replay", str(token))


def poll_watch_key(*, watch_id: WatchId, run_at: datetime, manual: bool = False) -> str:
    """Poll key; one scheduled run dedupes, while manual runs are namespaced."""
    parts = ("manual", run_at.isoformat()) if manual else (run_at.isoformat(),)
    return _key("poll_watch", watch_id, *parts)
