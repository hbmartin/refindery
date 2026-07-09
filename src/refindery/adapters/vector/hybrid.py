"""Client-side hybrid execution shared by every vector-store adapter.

Both arms run concurrently with per-arm timing, then fuse with RRF so
rankings are identical across stores (the conformance suite asserts this).
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
