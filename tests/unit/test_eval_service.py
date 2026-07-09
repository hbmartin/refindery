"""EvalService unit tests over hand-built logged runs."""

import math
from datetime import UTC, datetime
from typing import Self

import pytest

from refindery.application.ports.query_log_reader import LoggedRun
from refindery.application.services.eval_service import (
    EvalService,
    build_golden_set,
)
from refindery.domain.ids import PageId, QueryId

TS = datetime(2026, 7, 1, tzinfo=UTC)


def ranking_params(
    *,
    rollup: str = "max",
    exact_match: bool = False,
    recency_half_life_days: float | None = None,
) -> dict[str, object]:
    return {
        "k": 10,
        "rollup": rollup,
        "exact_match": exact_match,
        "recency_half_life_days": recency_half_life_days,
    }


def make_run(
    query_id: str,
    *,
    query_text: str = "hexagonal architecture",
    model: str = "model-a",
    reranker_model: str | None = "fake-reranker",
    final: tuple[str, ...] = ("p1", "p2", "p3"),
    final_ranks: tuple[int, ...] | None = None,
    prererank: tuple[str, ...] = ("p2", "p1", "p3"),
    params: dict[str, object] | None = None,
) -> LoggedRun:
    return LoggedRun(
        query_id=QueryId(query_id),
        ts=TS,
        kind="search",
        query_text=query_text,
        params=ranking_params() if params is None else params,
        active_model=model,
        reranker_model=reranker_model,
        final_page_ids=tuple(PageId(p) for p in final),
        final_page_ranks=(
            tuple(range(1, len(final) + 1)) if final_ranks is None else final_ranks
        ),
        prererank_page_ids=tuple(PageId(p) for p in prererank),
    )


def labels_for(**by_query: dict[str, bool]) -> dict[QueryId, dict[PageId, bool]]:
    return {
        QueryId(query_id): {PageId(p): rel for p, rel in pages.items()}
        for query_id, pages in by_query.items()
    }


class FakeReader:
    def __init__(
        self,
        runs: list[LoggedRun],
        labels: dict[QueryId, dict[PageId, bool]],
    ) -> None:
        self._runs = runs
        self._labels = labels

    def read_runs(self, *, since: datetime | None = None) -> list[LoggedRun]:
        if since is None:
            return list(self._runs)
        return [run for run in self._runs if run.ts >= since]

    def read_labels(self) -> dict[QueryId, dict[PageId, bool]]:
        return self._labels

    @classmethod
    def empty(cls) -> Self:
        return cls([], {})


class TestBuildGoldenSet:
    def test_unlabeled_runs_are_dropped(self):
        golden = build_golden_set([make_run("q1")], {})
        assert golden == []

    def test_all_negative_queries_are_dropped(self):
        golden = build_golden_set(
            [make_run("q1")], labels_for(q1={"p1": False, "p2": False})
        )
        assert golden == []

    def test_dedupes_by_normalized_text_and_unions_positives(self):
        runs = [
            make_run("q1", query_text="Hexagonal  Architecture"),
            make_run("q2", query_text="hexagonal architecture"),
        ]
        golden = build_golden_set(
            runs, labels_for(q1={"p1": True}, q2={"p2": True, "p3": False})
        )
        assert len(golden) == 1
        assert golden[0].relevant == {PageId("p1"), PageId("p2")}
        assert golden[0].source_query_ids == (QueryId("q1"), QueryId("q2"))
        assert golden[0].query_text == "Hexagonal  Architecture"

    def test_distinct_queries_stay_separate(self):
        runs = [
            make_run("q1", query_text="alpha"),
            make_run("q2", query_text="beta"),
        ]
        golden = build_golden_set(runs, labels_for(q1={"p1": True}, q2={"p2": True}))
        assert {g.query_text for g in golden} == {"alpha", "beta"}


class TestScoreLog:
    def test_counts_and_skips(self):
        runs = [
            make_run("q1"),  # labeled with a positive -> scored
            make_run("q2"),  # unlabeled -> not labeled
            make_run("q3"),  # all-negative -> skipped
        ]
        labels = labels_for(q1={"p1": True, "p3": False}, q3={"p2": False})
        report = EvalService(reader=FakeReader(runs, labels)).score_log(k=10)
        assert report.logged == 3
        assert report.labeled == 2
        assert report.scored == 1
        assert report.skipped_no_positive == 1

    def test_perfect_top_hit_metrics(self):
        runs = [make_run("q1", final=("p1", "p2"))]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        (score,) = report.queries
        assert score.ndcg == pytest.approx(1.0)
        assert score.reciprocal_rank == pytest.approx(1.0)
        assert score.recall == pytest.approx(1.0)
        assert score.recall_candidates == pytest.approx(1.0)

    def test_paginated_results_keep_absolute_ranks(self):
        runs = [
            make_run(
                "q1",
                final=("p1",),
                final_ranks=(11,),
                prererank=("p1",),
            )
        ]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=20)
        (score,) = report.queries
        assert score.ndcg == pytest.approx(1.0 / math.log2(12))
        assert score.reciprocal_rank == pytest.approx(1.0 / 11)
        assert score.recall == pytest.approx(1.0)

    def test_rerank_lift_from_candidate_order(self):
        # rerank moved the relevant page from candidate rank 2 to final rank 1
        runs = [make_run("q1", final=("p1", "p2"), prererank=("p2", "p1"))]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        (score,) = report.queries
        assert score.rerank_lift is not None
        assert score.rerank_lift > 0.0

    def test_no_lift_without_reranker(self):
        runs = [make_run("q1", reranker_model=None)]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        assert report.queries[0].rerank_lift is None

    def test_no_lift_for_non_max_rollup(self):
        runs = [make_run("q1", params=ranking_params(rollup="mean"))]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        assert report.queries[0].rerank_lift is None

    def test_no_lift_for_exact_match_pin(self):
        runs = [make_run("q1", params=ranking_params(exact_match=True))]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        assert report.queries[0].rerank_lift is None

    def test_no_lift_for_recency_decay(self):
        runs = [
            make_run(
                "q1",
                params=ranking_params(recency_half_life_days=30.0),
            )
        ]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        assert report.queries[0].rerank_lift is None

    def test_no_lift_when_search_did_not_log_recency_setting(self):
        runs = [
            make_run(
                "q1",
                params={"k": 10, "rollup": "max", "exact_match": False},
            )
        ]
        report = EvalService(
            reader=FakeReader(runs, labels_for(q1={"p1": True}))
        ).score_log(k=10)
        assert report.queries[0].rerank_lift is None

    def test_model_filter(self):
        runs = [make_run("q1", model="model-a"), make_run("q2", model="model-b")]
        labels = labels_for(q1={"p1": True}, q2={"p1": True})
        report = EvalService(reader=FakeReader(runs, labels)).score_log(
            k=10, model="model-b"
        )
        assert report.logged == 1
        assert report.models[0].model == "model-b"

    def test_aggregates_per_model(self):
        runs = [
            make_run("q1", final=("p1", "p2")),  # ndcg 1.0
            make_run("q2", final=("p2", "p1")),  # relevant at rank 2
        ]
        labels = labels_for(q1={"p1": True}, q2={"p1": True})
        report = EvalService(reader=FakeReader(runs, labels)).score_log(k=10)
        (model_report,) = report.models
        assert model_report.queries == 2
        assert 0.0 < model_report.ndcg < 1.0
        assert model_report.reciprocal_rank == pytest.approx((1.0 + 0.5) / 2)

    def test_empty_log(self):
        report = EvalService(reader=FakeReader.empty()).score_log(k=10)
        assert report.logged == 0
        assert report.models == ()
