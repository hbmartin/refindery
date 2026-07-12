"""Client-side hybrid execution shared by every vector-store adapter.

Both arms run concurrently with per-arm timing, then fuse with RRF.

Only the fusion step is cross-store identical: each backend's sparse arm has
its own analyzer (Qdrant: fastembed ``Qdrant/bm25`` term frequencies with
server-side IDF; LanceDB: Lance-native FTS with stemming, stop words
retained, and positional phrase support), so per-arm hits and scores
legitimately differ for the same query — switching backends can change what
surfaces. The conformance suite therefore asserts rank-level behavior plus
the per-store fusion identity ``fused == rrf_fuse(dense, sparse, k)``, never
cross-store equality.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from refindery.application.ports.vector_store import ArmTiming, HybridHits, HybridQuery
from refindery.domain.retrieval import ChunkHit, rrf_fuse

type Arm = Callable[[], Awaitable[list[ChunkHit]]]


async def _timed(arm: Arm) -> tuple[list[ChunkHit], float]:
    started = time.perf_counter()
    hits = await arm()
    return hits, (time.perf_counter() - started) * 1_000.0


async def run_hybrid_query(
    *, query: HybridQuery, dense_arm: Arm, sparse_arm: Arm
) -> HybridHits:
    """Run both arms concurrently and fuse client-side.

    A failing arm cancels its sibling, and failures propagate wrapped in an
    ExceptionGroup — catch with ``except*`` rather than a plain ``except``.
    """
    async with asyncio.TaskGroup() as tg:
        dense_task = tg.create_task(_timed(dense_arm))
        sparse_task = tg.create_task(_timed(sparse_arm))
    dense, dense_ms = dense_task.result()
    sparse, sparse_ms = sparse_task.result()
    fuse_started = time.perf_counter()
    fused = rrf_fuse(dense=dense, sparse=sparse, k=query.rrf_k)[: query.fused_limit]
    fuse_ms = (time.perf_counter() - fuse_started) * 1_000.0
    return HybridHits(
        dense=dense,
        sparse=sparse,
        fused=fused,
        timing=ArmTiming(dense_ms=dense_ms, sparse_ms=sparse_ms, fuse_ms=fuse_ms),
    )
