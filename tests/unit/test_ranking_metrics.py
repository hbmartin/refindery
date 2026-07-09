"""Golden and property tests for the agreement and relevance metrics."""

import pytest

from refindery.domain.ranking_metrics import (
    jaccard_at_k,
    kendall_tau_intersection,
    ndcg_at_k,
    rbo_ext,
    recall_at_k,
    reciprocal_rank,
)

ABC = ["a", "b", "c", "d", "e"]


class TestJaccard:
    def test_identical(self):
        assert jaccard_at_k(ABC, ABC, 5) == 1.0

    def test_disjoint(self):
        assert jaccard_at_k(["a", "b"], ["x", "y"], 2) == 0.0

    def test_partial(self):
        assert jaccard_at_k(["a", "b", "c"], ["b", "c", "d"], 3) == 0.5

    def test_both_empty(self):
        assert jaccard_at_k([], [], 10) == 1.0


class TestRbo:
    def test_identity_is_one(self):
        assert rbo_ext(ABC, ABC) == pytest.approx(1.0)

    def test_disjoint_is_zero(self):
        assert rbo_ext(["a", "b"], ["x", "y"]) == pytest.approx(0.0)

    def test_symmetry(self):
        x = ["a", "b", "c", "d"]
        y = ["b", "a", "d", "e"]
        assert rbo_ext(x, y) == pytest.approx(rbo_ext(y, x))

    def test_bounds(self):
        x = ["a", "b", "c"]
        y = ["c", "b", "a"]
        assert 0.0 <= rbo_ext(x, y) <= 1.0

    def test_top_heavy_weighting(self):
        # Agreement at the top matters more than at the bottom.
        top_agree = rbo_ext(["a", "b", "x"], ["a", "b", "y"])
        bottom_agree = rbo_ext(["x", "b", "c"], ["y", "b", "c"])
        assert top_agree > bottom_agree

    def test_uneven_lengths(self):
        assert 0.0 < rbo_ext(["a", "b", "c", "d"], ["a", "b"]) <= 1.0


class TestKendallTau:
    def test_same_order(self):
        assert kendall_tau_intersection(ABC, ABC) == 1.0

    def test_reversed(self):
        assert kendall_tau_intersection(ABC, list(reversed(ABC))) == -1.0

    def test_intersection_only(self):
        # intersection is {a, c}; both keep a before c
        assert kendall_tau_intersection(["a", "x", "c"], ["a", "y", "c"]) == 1.0

    def test_too_small_intersection(self):
        assert kendall_tau_intersection(["a", "b"], ["b", "x"]) is None
        assert kendall_tau_intersection([], []) is None


class TestNdcg:
    def test_perfect_ranking_is_one(self):
        assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, 3) == pytest.approx(1.0)

    def test_golden_value(self):
        # DCG = 1/log2(2) + 1/log2(4) = 1.5; IDCG = 1/log2(2) + 1/log2(3)
        expected = 1.5 / (1.0 + 0.6309297535714575)
        assert ndcg_at_k(["a", "b", "c"], {"a", "c"}, 3) == pytest.approx(expected)

    def test_no_relevant_is_none(self):
        assert ndcg_at_k(ABC, set(), 5) is None

    def test_nothing_found_is_zero(self):
        assert ndcg_at_k(["x", "y"], {"a"}, 2) == 0.0

    def test_bounds(self):
        value = ndcg_at_k(ABC, {"c", "e"}, 5)
        assert value is not None
        assert 0.0 <= value <= 1.0

    def test_promoting_a_relevant_item_never_hurts(self):
        worse = ndcg_at_k(["x", "y", "a"], {"a"}, 3)
        better = ndcg_at_k(["x", "a", "y"], {"a"}, 3)
        assert worse is not None
        assert better is not None
        assert better >= worse

    def test_ideal_dcg_capped_at_k(self):
        # 3 relevant items but k=2: a perfect top-2 still scores 1.0
        assert ndcg_at_k(["a", "b"], {"a", "b", "c"}, 2) == pytest.approx(1.0)


class TestReciprocalRank:
    def test_first_hit_rank(self):
        assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)

    def test_top_hit_is_one(self):
        assert reciprocal_rank(["a", "x"], {"a"}) == 1.0

    def test_absent_is_zero(self):
        assert reciprocal_rank(["x", "y"], {"a"}) == 0.0

    def test_empty_relevant_is_zero(self):
        assert reciprocal_rank(ABC, set()) == 0.0


class TestRecall:
    def test_fraction_found(self):
        assert recall_at_k(["a", "b", "c", "d"], {"a", "d", "z"}, 4) == pytest.approx(
            2 / 3
        )

    def test_all_found(self):
        assert recall_at_k(ABC, {"a", "e"}, 5) == 1.0

    def test_cutoff_excludes_deep_hits(self):
        assert recall_at_k(["x", "y", "a"], {"a"}, 2) == 0.0

    def test_no_relevant_is_none(self):
        assert recall_at_k(ABC, set(), 5) is None
