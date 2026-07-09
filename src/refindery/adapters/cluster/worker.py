"""Process-pool worker: everything CPU-heavy about clustering.

Module-level pure function so the spawn context can pickle the reference.
Only primitives and numpy arrays cross the boundary; umap/sklearn import
INSIDE the function so the parent never pays the numba JIT cost. Stage
timings are returned (spans cannot cross a process boundary).
"""

import time

import numpy as np
import numpy.typing as npt


def reduce_and_cluster(
    vectors: npt.NDArray[np.float32],
    *,
    algorithm: str,
    reducer: str,
    n_components: int,
    n_neighbors: int,
    min_dist: float,
    min_cluster_size: int,
    min_samples: int,
    random_state: int,
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float32], float, float]:
    """Reduce then cluster; returns (labels, probabilities, reduce_ms, cluster_ms)."""
    started = time.perf_counter()
    reduced = np.ascontiguousarray(vectors, dtype=np.float32)
    if reducer == "umap" and len(vectors) > n_components + 2:
        from umap import UMAP  # noqa: PLC0415 — heavy import, worker only

        reduced = UMAP(
            n_components=n_components,
            n_neighbors=min(n_neighbors, max(len(vectors) - 1, 2)),
            min_dist=min_dist,
            metric="cosine",
            random_state=random_state,
        ).fit_transform(vectors)
    elif reducer == "pca" and len(vectors) > n_components:
        from sklearn.decomposition import PCA  # noqa: PLC0415 — worker only

        reduced = PCA(
            n_components=n_components, random_state=random_state
        ).fit_transform(vectors)
    reduce_ms = (time.perf_counter() - started) * 1_000.0

    started = time.perf_counter()
    if algorithm == "kmeans":
        from sklearn.cluster import KMeans  # noqa: PLC0415 — worker only

        k = max(2, int(np.sqrt(len(vectors) / 2)))
        model = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
        labels = model.fit_predict(reduced).astype(np.int64)
        probabilities = np.ones(len(vectors), dtype=np.float32)
    else:
        from sklearn.cluster import (  # noqa: PLC0415 — worker only
            HDBSCAN,  # pyrefly: ignore[missing-module-attribute]
        )

        model = HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = model.fit_predict(reduced).astype(np.int64)
        probabilities = np.asarray(
            getattr(model, "probabilities_", np.ones(len(vectors))),
            dtype=np.float32,
        )
    cluster_ms = (time.perf_counter() - started) * 1_000.0
    return labels, probabilities, reduce_ms, cluster_ms
