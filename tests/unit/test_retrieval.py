"""Pure tests for RRF fusion, rollup strategies, and recency decay."""

from datetime import UTC, datetime, timedelta

from refindery.domain.ids import ChunkId, PageId
from refindery.domain.retrieval import (
    ChunkHit,
    PageScore,
    RollupStrategy,
    ScoredChunk,
    apply_recency_decay,
    rollup_pages,
    rrf_fuse,
)


def _hit(cid: str, pid: str = "p", ordinal: int = 0, score: float = 1.0) -> ChunkHit:
    return ChunkHit(
        chunk_id=ChunkId(cid), page_id=PageId(pid), ordinal=ordinal, score=score
    )


def _scored(
    cid: str, pid: str, fusion: float, rerank: float | None = None
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=ChunkId(cid),
        page_id=PageId(pid),
        ordinal=0,
        fusion_score=fusion,
        rerank_score=rerank,
    )


class TestRrfFuse:
    def test_chunk_in_both_arms_beats_single_arm(self):
        dense = [_hit("a"), _hit("b")]
        sparse = [_hit("b"), _hit("c")]
        fused = rrf_fuse(dense=dense, sparse=sparse, k=60)
        assert fused[0].chunk_id == "b"
        # b: 1/62 + 1/61 > a: 1/61 > c: 1/62
        assert [h.chunk_id for h in fused] == ["b", "a", "c"]

    def test_scores_are_reciprocal_rank_sums(self):
        fused = rrf_fuse(dense=[_hit("a")], sparse=[_hit("a")], k=60)
        assert fused[0].score == 2 / 61

    def test_deterministic_tiebreak_on_chunk_id(self):
        fused = rrf_fuse(dense=[_hit("z"), _hit("a")], sparse=[_hit("a"), _hit("z")])
        assert [h.chunk_id for h in fused] == ["a", "z"]

    def test_empty_arms(self):
        assert rrf_fuse(dense=[], sparse=[]) == []


class TestRollup:
    def test_max_uses_best_effective_score(self):
        chunks = [
            _scored("c1", "p1", fusion=0.1, rerank=0.9),
            _scored("c2", "p1", fusion=0.2, rerank=0.4),
            _scored("c3", "p2", fusion=0.3, rerank=0.7),
        ]
        pages = rollup_pages(chunks=chunks, strategy=RollupStrategy.MAX)
        assert [(p.page_id, p.score) for p in pages] == [("p1", 0.9), ("p2", 0.7)]
        assert [c.chunk_id for c in pages[0].chunks] == ["c1", "c2"]

    def test_mean_top_m(self):
        chunks = [
            _scored("c1", "p1", fusion=0.0, rerank=1.0),
            _scored("c2", "p1", fusion=0.0, rerank=0.5),
            _scored("c3", "p1", fusion=0.0, rerank=0.1),
        ]
        pages = rollup_pages(chunks=chunks, strategy=RollupStrategy.MEAN_TOP_M, top_m=2)
        assert pages[0].score == 0.75

    def test_sum_rrf_ignores_rerank_for_page_order(self):
        chunks = [
            _scored("c1", "p1", fusion=0.4, rerank=0.1),
            _scored("c2", "p1", fusion=0.4, rerank=0.1),
            _scored("c3", "p2", fusion=0.5, rerank=0.99),
        ]
        pages = rollup_pages(chunks=chunks, strategy=RollupStrategy.SUM_RRF)
        # p1 sums to 0.8 despite terrible rerank scores.
        assert [p.page_id for p in pages] == ["p1", "p2"]

    def test_fusion_score_used_when_no_rerank(self):
        pages = rollup_pages(chunks=[_scored("c1", "p1", fusion=0.3)])
        assert pages[0].score == 0.3


class TestRecencyDecay:
    def test_halves_at_exactly_one_half_life(self):
        now = datetime(2026, 7, 8, tzinfo=UTC)
        pages = [
            PageScore(page_id=PageId("old"), score=1.0, chunks=()),
            PageScore(page_id=PageId("new"), score=0.6, chunks=()),
        ]
        first_seen = {
            PageId("old"): now - timedelta(days=30),
            PageId("new"): now,
        }
        decayed = apply_recency_decay(
            pages, first_seen=first_seen, now=now, half_life_days=30.0
        )
        by_id = {p.page_id: p.score for p in decayed}
        assert by_id[PageId("old")] == 0.5
        assert by_id[PageId("new")] == 0.6
        # reordered: new now beats old
        assert [p.page_id for p in decayed] == ["new", "old"]

    def test_unknown_page_kept_undecayed(self):
        now = datetime(2026, 7, 8, tzinfo=UTC)
        pages = [PageScore(page_id=PageId("x"), score=0.8, chunks=())]
        out = apply_recency_decay(pages, first_seen={}, now=now, half_life_days=7.0)
        assert out[0].score == 0.8
