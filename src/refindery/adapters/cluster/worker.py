"""Process-pool worker: everything CPU-heavy about clustering.

Module-level pure function so the spawn context can pickle the reference.
Only primitives and numpy arrays cross the boundary; umap/sklearn import
INSIDE the function so the parent never pays the numba JIT cost. Stage
timings are returned (spans cannot cross a process boundary).
"""

import time
from collections.abc import Sequence
from importlib import import_module
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

_LEIDEN_EXTRA_HINT = "Leiden clustering requires `uv sync --extra leiden`"


class _GraphFactory(Protocol):
    def __call__(
        self, *, n: int, edges: list[tuple[int, int]], directed: bool
    ) -> object:
        """Build an igraph graph."""
        ...


class _IgraphModule(Protocol):
    Graph: _GraphFactory


class _FindPartition(Protocol):
    def __call__(
        self,
        graph: object,
        partition_type: object,
        *,
        weights: list[float],
        resolution_parameter: float,
        seed: int,
    ) -> Sequence[Sequence[int]]:
        """Run Leiden partitioning."""
        ...


class _LeidenModule(Protocol):
    RBConfigurationVertexPartition: object
    find_partition: _FindPartition


def _optional_leiden_modules() -> tuple[_IgraphModule, _LeidenModule]:
    try:
        igraph = import_module("igraph")
        leidenalg = import_module("leidenalg")
    except ImportError as exc:
        raise RuntimeError(_LEIDEN_EXTRA_HINT) from exc
    return cast("_IgraphModule", igraph), cast("_LeidenModule", leidenalg)


def _knn_graph_edges(
    matrix: npt.NDArray[np.float32], *, n_neighbors: int
) -> tuple[list[tuple[int, int]], list[float]]:
    from sklearn.neighbors import NearestNeighbors  # noqa: PLC0415 — worker only

    k = min(max(n_neighbors, 1), len(matrix) - 1)
    model = NearestNeighbors(n_neighbors=k + 1, metric="cosine")
    distances, indices = model.fit(matrix).kneighbors(matrix)

    edge_weights: dict[tuple[int, int], float] = {}
    for source, (row_distances, row_indices) in enumerate(
        zip(distances, indices, strict=True)
    ):
        for distance, target in zip(row_distances, row_indices, strict=True):
            target_int = int(target)
            if source == target_int:
                continue
            edge = (min(source, target_int), max(source, target_int))
            distance_value = float(distance)
            if not np.isfinite(distance_value):
                continue
            weight = 1.0 / (1.0 + max(distance_value, 0.0))
            edge_weights[edge] = max(edge_weights.get(edge, 0.0), weight)
    edges = sorted(edge_weights)
    return edges, [edge_weights[edge] for edge in edges]


def _leiden_cluster(
    matrix: npt.NDArray[np.float32],
    *,
    n_neighbors: int,
    resolution: float,
    random_state: int,
) -> npt.NDArray[np.int64]:
    if len(matrix) <= 1:
        return np.zeros(len(matrix), dtype=np.int64)
    igraph, leidenalg = _optional_leiden_modules()
    edges, weights = _knn_graph_edges(matrix, n_neighbors=n_neighbors)
    if not edges:
        return np.arange(len(matrix), dtype=np.int64)

    graph = igraph.Graph(n=len(matrix), edges=edges, directed=False)
    partition = leidenalg.find_partition(
        graph,
        leidenalg.RBConfigurationVertexPartition,
        weights=weights,
        resolution_parameter=resolution,
        seed=random_state,
    )

    labels = np.full(len(matrix), -1, dtype=np.int64)
    for label, members in enumerate(partition):
        for vertex in members:
            labels[int(vertex)] = label
    next_label = len(partition)
    for index, label in enumerate(labels):
        if label < 0:
            labels[index] = next_label
            next_label += 1
    return labels


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
    leiden_resolution: float = 1.0,
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float32], float, float]:
    """Reduce then cluster; returns (labels, probabilities, reduce_ms, cluster_ms)."""
    if len(vectors) == 0:
        return (
            np.array([], dtype=np.int64),
            np.array([], dtype=np.float32),
            0.0,
            0.0,
        )
    started = time.perf_counter()
    reduced = np.ascontiguousarray(vectors, dtype=np.float32)
    match reducer:
        case "umap" if len(vectors) > n_components + 2:
            from umap import UMAP  # noqa: PLC0415 — heavy import, worker only

            reduced = UMAP(
                n_components=n_components,
                n_neighbors=min(n_neighbors, max(len(vectors) - 1, 2)),
                min_dist=min_dist,
                metric="cosine",
                random_state=random_state,
            ).fit_transform(vectors)
        case "umap":
            pass
        case "pca" if len(vectors) > n_components:
            from sklearn.decomposition import PCA  # noqa: PLC0415 — worker only

            reduced = PCA(
                n_components=n_components, random_state=random_state
            ).fit_transform(vectors)
        case "pca" | "none":
            pass
        case _:
            msg = f"unknown reducer {reducer!r}"
            raise ValueError(msg)
    reduce_ms = (time.perf_counter() - started) * 1_000.0

    started = time.perf_counter()
    match algorithm:
        case "kmeans":
            from sklearn.cluster import KMeans  # noqa: PLC0415 — worker only

            k = min(len(vectors), max(1, int(np.sqrt(len(vectors) / 2))))
            model = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
            labels = model.fit_predict(reduced).astype(np.int64)
            probabilities = np.ones(len(vectors), dtype=np.float32)
        case "hdbscan":
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
        case "leiden":
            labels = _leiden_cluster(
                reduced,
                n_neighbors=n_neighbors,
                resolution=leiden_resolution,
                random_state=random_state,
            )
            probabilities = np.ones(len(vectors), dtype=np.float32)
        case _:
            msg = f"unknown clustering algorithm {algorithm!r}"
            raise ValueError(msg)
    cluster_ms = (time.perf_counter() - started) * 1_000.0
    return labels, probabilities, reduce_ms, cluster_ms
