"""Golden and property tests for the agreement statistics."""

import pytest

from refindery.domain.ranking_metrics import (
    jaccard_at_k,
    kendall_tau_intersection,
    rbo_ext,
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
