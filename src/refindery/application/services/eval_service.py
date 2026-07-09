"""Offline retrieval eval: score the query log, replay regression diffs.

Two modes share the golden-set substrate:

- ``score_log`` reads logged runs read-only and computes nDCG@k / MRR /
  recall@k against feedback labels — no retrieval re-run, no container.
- ``replay`` re-runs golden queries live under two arm configurations
  (model and/or rerank toggled) via ``CompareService.replay_arm`` and
  diffs the aggregate metrics. Replays are never logged.

Reranker lift in ``score_log`` is exact only for ``rollup=max`` rows
without exact-match pins or recency decay: first-occurrence page order of
the fused candidate set is then precisely the rerank-off ranking. Rows
with other rollups are excluded from lift.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from refindery.application.ports.query_log_reader import LoggedRun, QueryLogReader
from refindery.application.services.compare_service import CompareService
from refindery.domain.ids import PageId, QueryId
from refindery.domain.ranking_metrics import ndcg_at_k, recall_at_k, reciprocal_rank


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    """One replayable query: normalized text with its union of positives."""

    query_text: str
    relevant: frozenset[PageId]
    source_query_ids: tuple[QueryId, ...]


@dataclass(frozen=True, slots=True)
class QueryScore:
    """Metrics for one logged run."""

    query_id: QueryId
    query_text: str
    model: str
    ndcg: float
    reciprocal_rank: float
    recall: float
    recall_candidates: float
    rerank_lift: float | None


@dataclass(frozen=True, slots=True)
class ModelReport:
    """Mean metrics over one model's scored runs."""

    model: str
    queries: int
    ndcg: float
    reciprocal_rank: float
    recall: float
    recall_candidates: float
    rerank_lift: float | None


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """Everything ``eval score`` emits."""

    k: int
    logged: int
    labeled: int
    scored: int
    skipped_no_positive: int
    models: tuple[ModelReport, ...]
    queries: tuple[QueryScore, ...]


@dataclass(frozen=True, slots=True)
class ArmSpec:
    """One replay configuration; model ``None`` means the active model."""

    model_id: str | None = None
    rerank: bool = True

    def label(self, *, active_model_id: str) -> str:
        """Human-readable arm name for reports."""
        model = self.model_id or active_model_id
        return f"{model} ({'rerank' if self.rerank else 'no rerank'})"


@dataclass(frozen=True, slots=True)
class ArmReport:
    """Mean metrics for one replayed arm."""

    label: str
    ndcg: float
    reciprocal_rank: float
    recall: float


@dataclass(frozen=True, slots=True)
class PairedScore:
    """Per-query nDCG under both arms."""

    query_text: str
    ndcg_a: float
    ndcg_b: float


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Everything ``eval replay`` emits."""

    k: int
    golden_queries: int
    arm_a: ArmReport
    arm_b: ArmReport
    deltas: dict[str, float]
    queries: tuple[PairedScore, ...]


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def build_golden_set(
    runs: Sequence[LoggedRun],
    labels: Mapping[QueryId, Mapping[PageId, bool]],
) -> list[GoldenQuery]:
    """Assemble golden queries: dedupe by text, union positives.

    Runs without feedback are dropped; so are queries whose labels are all
    negative (no positives means every metric is undefined).
    """
    positives: dict[str, set[PageId]] = {}
    display: dict[str, str] = {}
    sources: dict[str, list[QueryId]] = {}
    for run in runs:
        run_labels = labels.get(run.query_id)
        if not run_labels:
            continue
        key = _normalize(run.query_text)
        display.setdefault(key, run.query_text)
        positives.setdefault(key, set()).update(
            page for page, relevant in run_labels.items() if relevant
        )
        sources.setdefault(key, []).append(run.query_id)
    return [
        GoldenQuery(
            query_text=display[key],
            relevant=frozenset(pages),
            source_query_ids=tuple(sources[key]),
        )
        for key, pages in positives.items()
        if pages
    ]


class EvalService:
    """Scores and replays the query log."""

    def __init__(self, *, reader: QueryLogReader) -> None:
        self._reader = reader

    def score_log(
        self,
        *,
        k: int = 10,
        since: datetime | None = None,
        model: str | None = None,
    ) -> ScoreReport:
        """Score every labeled logged run against its own feedback."""
        runs = self._reader.read_runs(since=since)
        if model is not None:
            runs = [run for run in runs if run.active_model == model]
        labels = self._reader.read_labels()

        labeled = 0
        skipped_no_positive = 0
        scores: list[QueryScore] = []
        for run in runs:
            run_labels = labels.get(run.query_id)
            if not run_labels:
                continue
            labeled += 1
            relevant = frozenset(
                page for page, is_relevant in run_labels.items() if is_relevant
            )
            if (score := self._score_run(run, relevant=relevant, k=k)) is None:
                skipped_no_positive += 1
                continue
            scores.append(score)

        return ScoreReport(
            k=k,
            logged=len(runs),
            labeled=labeled,
            scored=len(scores),
            skipped_no_positive=skipped_no_positive,
            models=tuple(
                _model_report(model_id, rows)
                for model_id, rows in _by_model(scores).items()
            ),
            queries=tuple(scores),
        )

    async def replay(
        self,
        *,
        compare: CompareService,
        active_model_id: str,
        arm_a: ArmSpec,
        arm_b: ArmSpec,
        k: int = 10,
        candidates: int = 100,
        limit: int | None = None,
    ) -> ReplayReport:
        """Re-run golden queries under two arms and diff the aggregates."""
        golden = build_golden_set(self._reader.read_runs(), self._reader.read_labels())
        if limit is not None:
            golden = golden[:limit]

        arm_rows: tuple[list[QueryScore], list[QueryScore]] = ([], [])
        paired: list[PairedScore] = []
        for query in golden:
            ndcgs: list[float] = []
            for rows, arm in zip(arm_rows, (arm_a, arm_b), strict=True):
                pages = await compare.replay_arm(
                    model_id=arm.model_id or active_model_id,
                    query=query.query_text,
                    k=k,
                    candidates=candidates,
                    rerank=arm.rerank,
                )
                score = _score_ranking(
                    ranked=pages, relevant=query.relevant, k=k, query=query
                )
                rows.append(score)
                ndcgs.append(score.ndcg)
            paired.append(
                PairedScore(
                    query_text=query.query_text, ndcg_a=ndcgs[0], ndcg_b=ndcgs[1]
                )
            )

        reports = tuple(
            _arm_report(arm.label(active_model_id=active_model_id), rows)
            for arm, rows in zip((arm_a, arm_b), arm_rows, strict=True)
        )
        deltas = {
            metric: getattr(reports[1], metric) - getattr(reports[0], metric)
            for metric in ("ndcg", "reciprocal_rank", "recall")
        }
        return ReplayReport(
            k=k,
            golden_queries=len(golden),
            arm_a=reports[0],
            arm_b=reports[1],
            deltas=deltas,
            queries=tuple(paired),
        )

    def _score_run(
        self, run: LoggedRun, *, relevant: frozenset[PageId], k: int
    ) -> QueryScore | None:
        ndcg = ndcg_at_k(run.final_page_ids, relevant, k)
        recall = recall_at_k(run.final_page_ids, relevant, k)
        pool = run.prererank_page_ids
        recall_candidates = recall_at_k(pool, relevant, len(pool))
        if ndcg is None or recall is None or recall_candidates is None:
            return None
        lift: float | None = None
        if run.reranker_model is not None and run.params.get("rollup", "max") == "max":
            prererank_ndcg = ndcg_at_k(pool, relevant, k)
            if prererank_ndcg is not None:
                lift = ndcg - prererank_ndcg
        return QueryScore(
            query_id=run.query_id,
            query_text=run.query_text,
            model=run.active_model,
            ndcg=ndcg,
            reciprocal_rank=reciprocal_rank(run.final_page_ids, relevant),
            recall=recall,
            recall_candidates=recall_candidates,
            rerank_lift=lift,
        )


def _score_ranking(
    *, ranked: Sequence[PageId], relevant: frozenset[PageId], k: int, query: GoldenQuery
) -> QueryScore:
    ndcg = ndcg_at_k(ranked, relevant, k)
    recall = recall_at_k(ranked, relevant, k)
    if ndcg is None or recall is None:  # unreachable: golden queries have positives
        msg = f"golden query without positives: {query.query_text!r}"
        raise ValueError(msg)
    return QueryScore(
        query_id=query.source_query_ids[0],
        query_text=query.query_text,
        model="",
        ndcg=ndcg,
        reciprocal_rank=reciprocal_rank(ranked, relevant),
        recall=recall,
        recall_candidates=recall,
        rerank_lift=None,
    )


def _by_model(scores: Sequence[QueryScore]) -> dict[str, list[QueryScore]]:
    grouped: dict[str, list[QueryScore]] = {}
    for score in scores:
        grouped.setdefault(score.model, []).append(score)
    return grouped


def _model_report(model_id: str, rows: Sequence[QueryScore]) -> ModelReport:
    lifts = [row.rerank_lift for row in rows if row.rerank_lift is not None]
    return ModelReport(
        model=model_id,
        queries=len(rows),
        ndcg=fmean(row.ndcg for row in rows),
        reciprocal_rank=fmean(row.reciprocal_rank for row in rows),
        recall=fmean(row.recall for row in rows),
        recall_candidates=fmean(row.recall_candidates for row in rows),
        rerank_lift=fmean(lifts) if lifts else None,
    )


def _arm_report(label: str, rows: Sequence[QueryScore]) -> ArmReport:
    if not rows:
        return ArmReport(label=label, ndcg=0.0, reciprocal_rank=0.0, recall=0.0)
    return ArmReport(
        label=label,
        ndcg=fmean(row.ndcg for row in rows),
        reciprocal_rank=fmean(row.reciprocal_rank for row in rows),
        recall=fmean(row.recall for row in rows),
    )
