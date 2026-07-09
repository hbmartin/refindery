"""Tests for page-vector pooling."""

import numpy as np
import pytest

from refindery.domain.rollup import PoolingStrategy, l2_normalize, page_vector


def _vec(*values: float) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


def test_l2_normalize_unit_norm():
    out = l2_normalize(_vec(3.0, 4.0))
    assert np.allclose(np.linalg.norm(out), 1.0)
    assert np.allclose(out, [0.6, 0.8])


def test_l2_normalize_zero_vector_unchanged():
    out = l2_normalize(_vec(0.0, 0.0))
    assert np.allclose(out, [0.0, 0.0])


def test_mean_pooling():
    out = page_vector([_vec(1.0, 0.0), _vec(0.0, 1.0)])
    assert np.allclose(out, l2_normalize(_vec(0.5, 0.5)))


def test_max_pooling():
    out = page_vector([_vec(1.0, 0.2), _vec(0.3, 0.9)], strategy=PoolingStrategy.MAX)
    assert np.allclose(out, l2_normalize(_vec(1.0, 0.9)))


def test_single_chunk_is_normalized_identity():
    out = page_vector([_vec(2.0, 0.0)])
    assert np.allclose(out, [1.0, 0.0])


def test_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        page_vector([])


def test_output_dtype_is_float32():
    out = page_vector([_vec(1.0, 2.0)])
    assert out.dtype == np.float32
