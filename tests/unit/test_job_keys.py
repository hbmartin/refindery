"""Golden-format tests: idempotency keys are persisted dedupe keys.

A format change for an existing kind would stop deduplicating against keys
already in the jobs ledger, so every expectation here is a hard-coded
literal — recomputing through the implementation would defeat the point.
"""

from datetime import UTC, datetime
from uuid import UUID

from refindery.domain import job_keys
from refindery.domain.ids import PageId, WatchId
from refindery.domain.models import JobKind

TS = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)


def test_key_formats_are_golden():
    assert job_keys.fetch_and_index_key(PageId("p1")) == "fetch:p1"
    assert (
        job_keys.index_page_key(page_id=PageId("p1"), content_hash="abc")
        == "index:p1:abc"
    )
    assert (
        job_keys.extract_entities_key(page_id=PageId("p1"), content_hash="abc")
        == "entities:p1:abc"
    )
    assert job_keys.cluster_key(TS) == "cluster:2026-07-12T03:04:05+00:00"
    assert job_keys.canonicalize_entities_key("run-7") == "canon:run-7"
    assert (
        job_keys.backfill_model_key(model_id="voyage-3.5", now=TS)
        == "backfill:voyage-3.5:2026-07-12T03:04:05+00:00"
    )
    # sha256("p1,p2")[:16] — ids are sorted before hashing.
    assert (
        job_keys.purge_vectors_key([PageId("p1"), PageId("p2")])
        == "purge:8818f49ddacd766d"
    )
    token = UUID("01890a5d-ac96-774b-bcce-b302099a8057")
    assert (
        job_keys.eval_replay_key(token)
        == "eval-replay:01890a5d-ac96-774b-bcce-b302099a8057"
    )
    assert (
        job_keys.poll_watch_key(watch_id=WatchId("w1"), run_at=TS)
        == "poll_watch:w1:2026-07-12T03:04:05+00:00"
    )
    assert (
        job_keys.poll_watch_key(watch_id=WatchId("w1"), run_at=TS, manual=True)
        == "poll_watch:w1:manual:2026-07-12T03:04:05+00:00"
    )


def test_purge_key_is_order_insensitive():
    forward = job_keys.purge_vectors_key([PageId("p1"), PageId("p2")])
    reverse = job_keys.purge_vectors_key([PageId("p2"), PageId("p1")])
    assert forward == reverse


def test_every_enqueued_kind_has_a_unique_prefix():
    ts = TS
    keys_by_kind: dict[JobKind, str] = {
        JobKind.FETCH_AND_INDEX: job_keys.fetch_and_index_key(PageId("p")),
        JobKind.INDEX_PAGE: job_keys.index_page_key(
            page_id=PageId("p"), content_hash="h"
        ),
        JobKind.EXTRACT_ENTITIES: job_keys.extract_entities_key(
            page_id=PageId("p"), content_hash="h"
        ),
        JobKind.CLUSTER: job_keys.cluster_key(ts),
        JobKind.CANONICALIZE_ENTITIES: job_keys.canonicalize_entities_key("r"),
        JobKind.BACKFILL_MODEL: job_keys.backfill_model_key(model_id="m", now=ts),
        JobKind.PURGE_VECTORS: job_keys.purge_vectors_key([PageId("p")]),
        JobKind.EVAL_REPLAY: job_keys.eval_replay_key(UUID(int=1)),
        JobKind.POLL_WATCH: job_keys.poll_watch_key(watch_id=WatchId("w"), run_at=ts),
    }
    prefixes = [key.split(":", maxsplit=1)[0] for key in keys_by_kind.values()]
    assert len(prefixes) == len(set(prefixes))
    # LABEL_CLUSTERS is defined but never enqueued: labeling runs inline
    # within ClusterRunService, not as a durable job.
    assert set(keys_by_kind) == set(JobKind) - {JobKind.LABEL_CLUSTERS}
