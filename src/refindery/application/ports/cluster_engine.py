"""Clustering engine port (implemented in M4)."""

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True, slots=True)
class ClusterParams:
    """Parameters crossing the process-pool boundary (primitives only)."""

    algorithm: Literal["hdbscan", "kmeans", "leiden"] = "hdbscan"
    reducer: Literal["umap", "pca", "none"] = "umap"
    n_components: int = 10
    n_neighbors: int = 15
    min_dist: float = 0.0
    min_cluster_size: int = 5
    min_samples: int = 3
    leiden_resolution: float = 1.0
    random_state: int = 42


@dataclass(frozen=True, slots=True)
class ClusterFitResult:
    """Labels (-1 = noise), soft membership, and worker-side stage timings."""

    labels: npt.NDArray[np.int64]
    probabilities: npt.NDArray[np.float32]
    reduce_ms: float
    cluster_ms: float


class ClusterEngine(Protocol):
    """Fits cluster labels over page vectors."""

    async def fit(
        self, *, vectors: npt.NDArray[np.float32], params: ClusterParams
    ) -> ClusterFitResult:
        """Cluster the vectors; CPU-heavy work runs in a process pool."""
        ...
