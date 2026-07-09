"""Tests for surface-form normalization, blocking, and cluster matching."""

from refindery.domain.clustering import (
    LineageEvent,
    dynamic_hdbscan_params,
    match_clusters,
)
from refindery.domain.ctfidf import compute_ctfidf
from refindery.domain.entities import (
    block_key,
    normalize_surface_form,
    normalized_edit_distance,
)
from refindery.domain.ids import ClusterId, PageId


class TestNormalize:
    def test_casefold_and_punctuation(self):
        assert normalize_surface_form("OpenAI, Inc.") == "openai inc"

    def test_diacritics_stripped(self):
        assert normalize_surface_form("Café Zoë") == "cafe zoe"

    def test_plural_singularized(self):
        assert normalize_surface_form("Transformers") == "transformer"
        assert normalize_surface_form("neural networks") == "neural network"

    def test_whitespace_collapsed(self):
        assert normalize_surface_form("  Large   Language  Models ") == (
            "large language model"
        )

    def test_block_key_is_first_token(self):
        assert block_key("large language model") == "large"
        assert block_key("") == ""

    def test_edit_distance(self):
        assert normalized_edit_distance("kubernetes", "kubernetes") == 0.0
        assert normalized_edit_distance("kubernetes", "kuberneties") < 0.15
        assert normalized_edit_distance("python", "rust") > 0.5


def _members(*pages: str) -> frozenset[PageId]:
    return frozenset(PageId(p) for p in pages)


class TestMatchClusters:
    def test_first_run_creates_everything(self):
        outcome = match_clusters(
            old={}, new={0: _members("a", "b"), 1: _members("c", "d")}
        )
        assert len(outcome.ids_by_label) == 2
        assert all(r.event is LineageEvent.CREATED for r in outcome.lineage)
        assert outcome.tombstoned == ()

    def test_persisted_inherits_id(self):
        old_id = ClusterId("stable-1")
        outcome = match_clusters(
            old={old_id: _members("a", "b", "c")},
            new={0: _members("a", "b", "c", "d")},  # jaccard 0.75
        )
        assert outcome.ids_by_label[0] == old_id
        assert outcome.lineage[0].event is LineageEvent.PERSISTED
        assert outcome.lineage[0].jaccard is not None

    def test_split_records_parents(self):
        old_id = ClusterId("stable-1")
        outcome = match_clusters(
            old={old_id: _members("a", "b", "c", "d", "e", "f")},
            new={0: _members("a", "b", "c"), 1: _members("d", "e", "f")},
        )
        # jaccard 0.5 each; hungarian assigns one as persisted, the other splits
        events = {r.event for r in outcome.lineage}
        assert LineageEvent.PERSISTED in events
        split = next(r for r in outcome.lineage if r.event is LineageEvent.SPLIT)
        assert split.parent_ids == (old_id,)

    def test_dissolved_and_merged(self):
        gone = ClusterId("gone")
        absorbed = ClusterId("absorbed")
        survivor = ClusterId("survivor")
        outcome = match_clusters(
            old={
                gone: _members("x", "y"),
                absorbed: _members("a", "b"),
                survivor: _members("c", "d", "e"),
            },
            new={
                0: _members("c", "d", "e"),  # survivor persists
                1: _members("a", "b", "c", "q", "r", "s"),  # absorbs `absorbed`
            },
        )
        by_id = {r.cluster_id: r for r in outcome.lineage}
        assert by_id[survivor].event is LineageEvent.PERSISTED
        assert by_id[gone].event is LineageEvent.DISSOLVED
        assert by_id[absorbed].event is LineageEvent.MERGED
        assert by_id[absorbed].parent_ids == (outcome.ids_by_label[1],)
        assert set(outcome.tombstoned) == {gone, absorbed}

    def test_rectangular_more_new_than_old(self):
        outcome = match_clusters(
            old={ClusterId("one"): _members("a", "b")},
            new={0: _members("a", "b"), 1: _members("x", "y"), 2: _members("z")},
        )
        assert outcome.ids_by_label[0] == ClusterId("one")
        assert len({*outcome.ids_by_label.values()}) == 3


def test_dynamic_params_scale_with_corpus():
    assert dynamic_hdbscan_params(100) == (5, 3)
    assert dynamic_hdbscan_params(2500) == (25, 12)
    mcs, ms = dynamic_hdbscan_params(400)
    assert 5 <= mcs <= 25
    assert 3 <= ms <= 15


def test_ctfidf_finds_distinctive_terms():
    keywords = compute_ctfidf(
        {
            "c1": "kubernetes pods containers orchestration kubernetes cluster",
            "c2": "sourdough bread fermentation flour starter bread baking",
            "c3": "kubernetes bread shared words appear everywhere",
        },
        top_n=3,
    )
    assert "kubernetes" in keywords["c1"]
    assert "bread" in keywords["c2"]
    assert keywords["c1"] != keywords["c2"]


def test_ctfidf_empty():
    assert compute_ctfidf({"c1": ""}) == {"c1": []}
