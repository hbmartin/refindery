"""Page-vector rollup: pool chunk vectors into a single page vector."""

from enum import StrEnum

import numpy as np
import numpy.typing as npt

type Vector = npt.NDArray[np.float32]


class PoolingStrategy(StrEnum):
    """How chunk vectors are pooled into a page vector."""

    MEAN = "mean"
    MAX = "max"


def l2_normalize(vector: Vector) -> Vector:
    """L2-normalize a vector; zero vectors are returned unchanged."""
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def page_vector(
    chunk_vectors: list[Vector],
    strategy: PoolingStrategy = PoolingStrategy.MEAN,
) -> Vector:
    """Pool chunk vectors into one L2-normalized page vector.

    Raises ``ValueError`` when no chunk vectors are given.
    """
    if not chunk_vectors:
        msg = "cannot pool an empty list of chunk vectors"
        raise ValueError(msg)
    stacked = np.stack(chunk_vectors)
    match strategy:
        case PoolingStrategy.MEAN:
            pooled = stacked.mean(axis=0)
        case PoolingStrategy.MAX:
            pooled = stacked.max(axis=0)
    return l2_normalize(pooled.astype(np.float32))
