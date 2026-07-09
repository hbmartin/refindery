"""Clustering domain: dynamic parameters and the stable-ID matching layer.

HDBSCAN gives no stable ids across runs; ``match_clusters`` does, via
Jaccard cost + Hungarian assignment. Noise (label -1) is a legitimate
outcome and never becomes a cluster.
"""

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from scipy.optimize import linear_sum_assignment

from refindery.domain.ids import ClusterId, PageId, new_cluster_id

INHERIT_JACCARD = 0.5
SPLIT_OVERLAP = 0.3
MERGE_ABSORPTION = 0.5


class LineageEvent(StrEnum):
    """What happened to a cluster between runs."""

    CREATED = "created"
    PERSISTED = "persisted"
    SPLIT = "split"
    MERGED = "merged"
    DISSOLVED = "dissolved"


@dataclass(frozen=True, slots=True)
class LineageRecord:
    """One lineage event emitted by a run."""

    event: LineageEvent
    cluster_id: ClusterId
    parent_ids: tuple[ClusterId, ...] = ()
    jaccard: float | None = None


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """Stable ids per new label plus the lineage of the transition."""

    ids_by_label: dict[int, ClusterId]
    lineage: tuple[LineageRecord, ...]
    tombstoned: tuple[ClusterId, ...] = field(default=())


def dynamic_hdbscan_params(n_pages: int) -> tuple[int, int]:
    """Corpus-size-scaled (min_cluster_size, min_samples).

    Heuristic (config-overridable): ``sqrt(n)/2`` clamped to [5, 25];
    min_samples is half that, clamped to [3, 15]. Reproduces the spec's
    static defaults (5, 3) around n=100. TODO: tune against a real corpus.
    """
    min_cluster_size = int(np.clip(round(np.sqrt(n_pages) / 2), 5, 25))
    min_samples = int(np.clip(min_cluster_size // 2, 3, 15))
    return min_cluster_size, min_samples


def _jaccard(a: frozenset[PageId], b: frozenset[PageId]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_clusters(
    *,
    old: dict[ClusterId, frozenset[PageId]],
    new: dict[int, frozenset[PageId]],
) -> MatchOutcome:
    """Assign stable ids to new labels and emit lineage.

    Hungarian assignment over the Jaccard cost matrix; a match holds only at
    Jaccard >= 0.5. Unmatched new clusters are created (or splits when they
    overlap an old cluster > 0.3); unmatched old clusters are tombstoned as
    dissolved, or merged when most of their members landed in one new cluster.
    """
    old_ids = list(old)
    new_labels = list(new)
    matched: dict[int, tuple[ClusterId, float]] = {}

    if old_ids and new_labels:
        cost = np.ones((len(new_labels), len(old_ids)), dtype=np.float64)
        for i, label in enumerate(new_labels):
            for j, old_id in enumerate(old_ids):
                cost[i, j] = 1.0 - _jaccard(new[label], old[old_id])
        rows, cols = linear_sum_assignment(cost)
        for i, j in zip(rows.tolist(), cols.tolist(), strict=True):
            jaccard = 1.0 - float(cost[i, j])
            if jaccard >= INHERIT_JACCARD:
                matched[new_labels[i]] = (old_ids[j], jaccard)

    ids_by_label: dict[int, ClusterId] = {}
    lineage: list[LineageRecord] = []
    inherited = {cid for cid, _ in matched.values()}

    for label in new_labels:
        if label in matched:
            cluster_id, jaccard = matched[label]
            ids_by_label[label] = cluster_id
            lineage.append(
                LineageRecord(
                    event=LineageEvent.PERSISTED,
                    cluster_id=cluster_id,
                    jaccard=jaccard,
                )
            )
            continue
        fresh = new_cluster_id()
        ids_by_label[label] = fresh
        parents = tuple(
            old_id
            for old_id in old_ids
            if _jaccard(new[label], old[old_id]) > SPLIT_OVERLAP
        )
        lineage.append(
            LineageRecord(
                event=LineageEvent.SPLIT if parents else LineageEvent.CREATED,
                cluster_id=fresh,
                parent_ids=parents,
            )
        )

    tombstoned: list[ClusterId] = []
    for old_id, members in old.items():
        if old_id in inherited or not members:
            continue
        tombstoned.append(old_id)
        absorbing = [
            ids_by_label[label]
            for label, new_members in new.items()
            if len(members & new_members) / len(members) > MERGE_ABSORPTION
        ]
        lineage.append(
            LineageRecord(
                event=LineageEvent.MERGED if absorbing else LineageEvent.DISSOLVED,
                cluster_id=old_id,
                parent_ids=tuple(absorbing),
            )
        )
    return MatchOutcome(
        ids_by_label=ids_by_label,
        lineage=tuple(lineage),
        tombstoned=tuple(tombstoned),
    )
