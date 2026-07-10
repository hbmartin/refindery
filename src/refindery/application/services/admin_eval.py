"""Durable administration workflow for live eval replay."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

from uuid6 import uuid7

from refindery.adapters.observability.query_log_reader import DuckDbQueryLogReader
from refindery.application.ports.clock import Clock
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.compare_service import CompareService
from refindery.application.services.eval_service import ArmSpec, EvalService
from refindery.domain.models import EvalReplayResult, Job, JobKind


class AdminEvalService:
    """Enqueue and execute eval replay jobs with durable results."""

    def __init__(
        self,
        *,
        path: Path,
        store: MetadataStore,
        queue: JobQueue,
        compare: CompareService,
        clock: Clock,
    ) -> None:
        self._path = path
        self._store = store
        self._queue = queue
        self._compare = compare
        self._clock = clock

    async def enqueue(self, *, payload: dict[str, object]) -> str:
        """Submit one replay and return its durable job id."""
        job_id = await self._queue.enqueue(
            kind=JobKind.EVAL_REPLAY,
            payload={"request": json.dumps(payload)},
            idempotency_key=f"eval-replay:{uuid7()}",
        )
        if job_id is None:  # UUID idempotency keys cannot collide in practice.
            raise RuntimeError("could not enqueue eval replay")  # noqa: TRY003
        return str(job_id)

    async def handle_job(self, job: Job) -> None:
        """Execute both replay arms and persist success or failure."""
        created = self._clock.now()
        try:
            request = json.loads(job.payload["request"])
            model = await self._store.get_active_model()
            if model is None:
                raise RuntimeError("no active embedding model")  # noqa: TRY003, TRY301
            report = await EvalService(reader=DuckDbQueryLogReader(self._path)).replay(
                compare=self._compare,
                active_model_id=model.id,
                arm_a=ArmSpec(
                    model_id=request.get("model_a"), rerank=request["rerank_a"]
                ),
                arm_b=ArmSpec(
                    model_id=request.get("model_b"), rerank=request["rerank_b"]
                ),
                k=request["k"],
                candidates=request["candidates"],
                limit=request.get("limit"),
            )
        except Exception as exc:
            await self._store.put_eval_replay_result(
                EvalReplayResult(
                    job_id=job.id,
                    report=None,
                    error=str(exc),
                    created_at=created,
                    updated_at=self._clock.now(),
                )
            )
            raise
        await self._store.put_eval_replay_result(
            EvalReplayResult(
                job_id=job.id,
                report=cast("dict[str, object]", asdict(report)),
                error=None,
                created_at=created,
                updated_at=self._clock.now(),
            )
        )
