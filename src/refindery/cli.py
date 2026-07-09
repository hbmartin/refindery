"""CLI: ``refindery [serve|eval score|eval replay]``.

No subcommand serves the API, so ``python -m refindery`` keeps its
historical behavior. ``eval score`` never loads settings or a container —
it reads the observability DuckDB file directly, read-only. ``eval replay``
boots a trimmed container (no queue, no sink) and re-runs golden queries.
"""

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from inspect import isawaitable
from pathlib import Path
from typing import NoReturn

from refindery.application.services.eval_service import (
    ArmSpec,
    EvalService,
    ReplayReport,
    ScoreReport,
)

_DEFAULT_DB = Path("data/observability.duckdb")


def main(argv: Sequence[str] | None = None) -> None:
    """Parse argv and dispatch; no subcommand means serve."""
    args = _build_parser().parse_args(argv)
    match args.command:
        case "eval" if args.eval_command == "score":
            _cmd_eval_score(args)
        case "eval":
            _cmd_eval_replay(args)
        case _:
            _cmd_serve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refindery", description="Refindery: refind anything you have seen."
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="serve the API (the default)")

    eval_parser = sub.add_parser("eval", help="offline retrieval eval")
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)

    score = eval_sub.add_parser(
        "score", help="score logged queries against feedback labels"
    )
    score.add_argument(
        "--db", type=Path, default=_DEFAULT_DB, help="observability DuckDB file"
    )
    score.add_argument("--k", type=int, default=10, help="ranking depth to score at")
    score.add_argument(
        "--since",
        type=datetime.fromisoformat,
        default=None,
        help="only score runs at or after this ISO timestamp",
    )
    score.add_argument("--model", default=None, help="only score this model's runs")
    _add_json_flag(score)

    replay = eval_sub.add_parser(
        "replay", help="re-run golden queries under two configurations and diff"
    )
    replay.add_argument(
        "--db", type=Path, default=_DEFAULT_DB, help="observability DuckDB file"
    )
    replay.add_argument("--model-a", default=None, help="arm A model (default active)")
    replay.add_argument("--model-b", default=None, help="arm B model (default active)")
    replay.add_argument(
        "--no-rerank-a", action="store_true", help="disable reranking in arm A"
    )
    replay.add_argument(
        "--no-rerank-b", action="store_true", help="disable reranking in arm B"
    )
    replay.add_argument("--k", type=int, default=10, help="ranking depth to score at")
    replay.add_argument(
        "--candidates", type=int, default=100, help="candidate pool per arm"
    )
    replay.add_argument(
        "--limit", type=int, default=None, help="replay at most this many queries"
    )
    _add_json_flag(replay)
    return parser


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        dest="json_path",
        help="also write the full report as JSON to this path",
    )


def _cmd_serve() -> None:
    import uvicorn  # noqa: PLC0415 — lazy: eval subcommands never need the server

    from refindery.api.app import create_app  # noqa: PLC0415
    from refindery.config import load_settings  # noqa: PLC0415

    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )


def _cmd_eval_score(args: argparse.Namespace) -> None:
    from refindery.adapters.observability.query_log_reader import (  # noqa: PLC0415
        DuckDbQueryLogReader,
    )

    try:
        reader = DuckDbQueryLogReader(args.db)
    except FileNotFoundError as exc:
        _fail(str(exc))
    report = EvalService(reader=reader).score_log(
        k=args.k, since=args.since, model=args.model
    )
    _print_score_report(report)
    _write_json(args.json_path, report)


def _cmd_eval_replay(args: argparse.Namespace) -> None:
    report = asyncio.run(_replay(args))
    _print_replay_report(report)
    _write_json(args.json_path, report)


async def _replay(args: argparse.Namespace) -> ReplayReport:
    from refindery.adapters.observability.query_log_reader import (  # noqa: PLC0415
        DuckDbQueryLogReader,
    )
    from refindery.application.container import build_container  # noqa: PLC0415
    from refindery.config import load_settings  # noqa: PLC0415

    try:
        reader = DuckDbQueryLogReader(args.db)
    except FileNotFoundError as exc:
        _fail(str(exc))
    container = build_container(load_settings())
    await container.startup_for_eval()
    try:
        active = await container.store.get_active_model()
        if active is None:
            _fail("no active embedding model; run the server once to register one")
        return await EvalService(reader=reader).replay(
            compare=container.compare,
            active_model_id=active.id,
            arm_a=ArmSpec(model_id=args.model_a, rerank=not args.no_rerank_a),
            arm_b=ArmSpec(model_id=args.model_b, rerank=not args.no_rerank_b),
            k=args.k,
            candidates=args.candidates,
            limit=args.limit,
        )
    finally:
        for close in (
            container.vector_store.close,
            container.router.close,
            container.store.close,
        ):
            result = close()
            if isawaitable(result):
                await result


def _fail(message: str) -> NoReturn:
    sys.exit(f"refindery eval: {message}")


def _write_json(path: Path | None, report: ScoreReport | ReplayReport) -> None:
    if path is None:
        return
    path.write_text(
        json.dumps(dataclasses.asdict(report), indent=2, default=str) + "\n"
    )
    print(f"wrote {path}")


def _print_score_report(report: ScoreReport) -> None:
    print(
        f"runs: {report.logged} logged, {report.labeled} labeled, "
        f"{report.scored} scored ({report.skipped_no_positive} skipped: "
        "no positive labels)"
    )
    if not report.models:
        print("nothing to score — record feedback via POST /v1/feedback first")
        return
    headers = (
        "model",
        "queries",
        f"nDCG@{report.k}",
        "MRR",
        f"recall@{report.k}",
        "recall@cand",
        "rerank lift",
    )
    rows = [
        (
            m.model,
            str(m.queries),
            _num(m.ndcg),
            _num(m.reciprocal_rank),
            _num(m.recall),
            _num(m.recall_candidates),
            _num(m.rerank_lift),
        )
        for m in report.models
    ]
    print(_format_table(headers, rows))


def _print_replay_report(report: ReplayReport) -> None:
    print(f"golden queries: {report.golden_queries}")
    headers = ("arm", f"nDCG@{report.k}", "MRR", f"recall@{report.k}")
    rows = [
        (arm.label, _num(arm.ndcg), _num(arm.reciprocal_rank), _num(arm.recall))
        for arm in (report.arm_a, report.arm_b)
    ]
    print(_format_table(headers, rows))
    deltas = ", ".join(f"{name}: {value:+.4f}" for name, value in report.deltas.items())
    print(f"deltas (B - A): {deltas}")
    regressions = sorted(report.queries, key=lambda q: q.ndcg_b - q.ndcg_a)[:5]
    worst = [q for q in regressions if q.ndcg_b < q.ndcg_a]
    if worst:
        print("largest per-query nDCG regressions (B vs A):")
        for q in worst:
            print(f"  {q.ndcg_b - q.ndcg_a:+.4f}  {q.query_text}")


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(headers)
    ]

    def line(cells: Sequence[str]) -> str:
        columns = zip(cells, widths, strict=True)
        return "  ".join(cell.ljust(width) for cell, width in columns)

    separator = "  ".join("-" * width for width in widths)
    return "\n".join([line(headers), separator, *(line(row) for row in rows)])
