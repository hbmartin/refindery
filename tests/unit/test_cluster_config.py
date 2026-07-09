"""Cluster config and worker guardrails."""

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
