"""Cluster config and worker guardrails."""

import importlib.util

import numpy as np
import pytest
from pydantic import ValidationError

from refindery.adapters.cluster.worker import reduce_and_cluster
from refindery.config import ClusterSettings


def test_cluster_settings_reject_unknown_algorithm_and_reducer():
    with pytest.raises(ValidationError):
        ClusterSettings.model_validate({"algorithm": "agglomerative"})
    with pytest.raises(ValidationError):
        ClusterSettings.model_validate({"reducer": "tsne"})
    with pytest.raises(ValidationError):
        ClusterSettings.model_validate({"cron": "* * * * * *"})


def test_cluster_settings_accept_leiden_and_validate_resolution():
    settings = ClusterSettings.model_validate(
        {"algorithm": "leiden", "leiden_resolution": 1.25}
    )
    assert settings.algorithm == "leiden"
    assert settings.leiden_resolution == 1.25
    with pytest.raises(ValidationError):
        ClusterSettings.model_validate({"leiden_resolution": 0})


def test_cluster_worker_rejects_unknown_dispatch_values():
    vectors = np.ones((3, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="unknown reducer"):
        reduce_and_cluster(
            vectors,
            algorithm="hdbscan",
            reducer="bad",
            n_components=2,
            n_neighbors=2,
            min_dist=0.0,
            min_cluster_size=2,
            min_samples=1,
            random_state=42,
        )
    with pytest.raises(ValueError, match="unknown clustering algorithm"):
        reduce_and_cluster(
            vectors,
            algorithm="bad",
            reducer="none",
            n_components=2,
            n_neighbors=2,
            min_dist=0.0,
            min_cluster_size=2,
            min_samples=1,
            random_state=42,
        )


def test_kmeans_caps_clusters_for_small_corpus():
    vectors = np.ones((1, 4), dtype=np.float32)
    labels, probabilities, _reduce_ms, _cluster_ms = reduce_and_cluster(
        vectors,
        algorithm="kmeans",
        reducer="none",
        n_components=2,
        n_neighbors=2,
        min_dist=0.0,
        min_cluster_size=2,
        min_samples=1,
        random_state=42,
    )
    assert labels.tolist() == [0]
    assert probabilities.tolist() == [1.0]


def test_leiden_clusters_or_reports_missing_extra():
    vectors = np.asarray(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.9, 0.1, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.9, 0.1],
        ],
        dtype=np.float32,
    )
    kwargs = {
        "algorithm": "leiden",
        "reducer": "none",
        "n_components": 2,
        "n_neighbors": 2,
        "min_dist": 0.0,
        "min_cluster_size": 2,
        "min_samples": 1,
        "leiden_resolution": 1.0,
        "random_state": 42,
    }
    if (
        importlib.util.find_spec("igraph") is None
        or importlib.util.find_spec("leidenalg") is None
    ):
        with pytest.raises(RuntimeError, match="uv sync --extra leiden"):
            reduce_and_cluster(vectors, **kwargs)
        return

    labels, probabilities, _reduce_ms, _cluster_ms = reduce_and_cluster(
        vectors, **kwargs
    )
    assert len(labels) == len(vectors)
    assert all(label >= 0 for label in labels)
    assert probabilities.tolist() == [1.0] * len(vectors)
