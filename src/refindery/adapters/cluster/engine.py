"""ClusterEngine adapter: runs the worker in a spawn ProcessPoolExecutor."""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from multiprocessing import get_context

import numpy as np
import numpy.typing as npt

from refindery.adapters.cluster.worker import reduce_and_cluster
from refindery.application.ports.cluster_engine import ClusterFitResult, ClusterParams


class ProcessPoolClusterEngine:
    """CPU-heavy clustering off the event loop."""

    def __init__(self, *, max_workers: int = 1) -> None:
        self._executor = ProcessPoolExecutor(
            max_workers=max_workers, mp_context=get_context("spawn")
        )

    async def fit(
        self, *, vectors: npt.NDArray[np.float32], params: ClusterParams
    ) -> ClusterFitResult:
        """Fit in the pool; only arrays and primitives cross the boundary."""
        loop = asyncio.get_running_loop()
        labels, probabilities, reduce_ms, cluster_ms = await loop.run_in_executor(
            self._executor,
            partial(
                reduce_and_cluster,
                vectors,
                algorithm=params.algorithm,
                reducer=params.reducer,
                n_components=params.n_components,
                n_neighbors=params.n_neighbors,
                min_dist=params.min_dist,
                min_cluster_size=params.min_cluster_size,
                min_samples=params.min_samples,
                leiden_resolution=params.leiden_resolution,
                random_state=params.random_state,
            ),
        )
        return ClusterFitResult(
            labels=labels,
            probabilities=probabilities,
            reduce_ms=reduce_ms,
            cluster_ms=cluster_ms,
        )

    def close(self) -> None:
        """Shut the pool down."""
        self._executor.shutdown(wait=False, cancel_futures=True)
