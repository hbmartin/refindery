"""Cluster run orchestration: fit -> stable-id match -> persist -> label.

Always a full refit. Noise (-1) never becomes a cluster. Stable ids come
from the Jaccard/Hungarian matching layer; tombstoned clusters remain
resolvable by id so stale agent references degrade gracefully.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt

from refindery.adapters.llm.openai_compat import OpenAiCompatClient
from refindery.application.ports.clock import Clock
from refindery.application.ports.cluster_engine import (
    ClusterEngine,
    ClusterFitResult,
    ClusterParams,
)
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.application.services.indexed_pages import indexed_page_ids
from refindery.config import ClusterSettings
from refindery.domain.clustering import (
    MatchOutcome,
    dynamic_hdbscan_params,
    match_clusters,
)
from refindery.domain.ctfidf import compute_ctfidf
from refindery.domain.errors import NoActiveModelError, RefinderyError
from refindery.domain.ids import ClusterId, PageId, new_cluster_run_id
from refindery.domain.models import Cluster, ClusterRun, Job, JobKind
from refindery.domain.rollup import l2_normalize

logger = logging.getLogger(__name__)

_BODY_SNIPPET = 2_000
_LABEL_TITLES = 10


@dataclass(frozen=True, slots=True)
class _ClusterInput:
    """Indexed vectors ready for one clustering fit."""

    model_id: str
    page_ids: list[PageId]
    matrix: npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class _ClusterMembership:
    """Cluster labels and per-page probabilities from the fitted model."""

    by_label: dict[int, set[PageId]]
    probabilities: dict[PageId, float]


class ClusterRunInFlightError(RefinderyError):
    """A run is already executing."""

    def __init__(self) -> None:
        super().__init__("a cluster run is already in flight")


class ClusterRunService:
    """Runs clustering end to end (the ``cluster`` job handler)."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        engine: ClusterEngine,
        queue: JobQueue,
        clock: Clock,
        canonicalization: CanonicalizationService,
        settings: ClusterSettings,
        labeler: OpenAiCompatClient | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._queue = queue
        self._clock = clock
        self._canonicalization = canonicalization
        self._settings = settings
        self._labeler = labeler
        self._in_flight = False

    async def request_run(self, *, trigger: str) -> bool:
        """Enqueue a run job; False when below the corpus minimum."""
        if await self._store.count_indexed_pages() < self._settings.min_pages:
            return False
        await self._queue.enqueue(
            kind=JobKind.CLUSTER,
            payload={"trigger": trigger},
            idempotency_key=f"cluster:{self._clock.now().isoformat()}",
        )
        return True

    async def handle_cluster_job(self, job: Job) -> None:
        """Run clustering as the durable cluster job."""
        await self.run(trigger=job.payload.get("trigger", "manual"))

    async def run(self, *, trigger: str) -> ClusterRun | None:
        """Full refit; returns the run record (None when skipped)."""
        if self._in_flight:
            raise ClusterRunInFlightError
        self._in_flight = True
        try:
            return await self._run(trigger=trigger)
        finally:
            self._in_flight = False

    async def _run(self, *, trigger: str) -> ClusterRun | None:
        cluster_input = await self._cluster_input()
        if cluster_input is None:
            return None

        params = self._cluster_params(n_pages=len(cluster_input.page_ids))
        started = self._clock.now()
        run = self._new_run(
            trigger=trigger,
            params=params,
            model_id=cluster_input.model_id,
            started=started,
        )
        await self._store.insert_cluster_run(run)

        result = await self._engine.fit(vectors=cluster_input.matrix, params=params)
        membership = self._cluster_membership(
            page_ids=cluster_input.page_ids,
            labels=result.labels.tolist(),
            probabilities=result.probabilities.tolist(),
        )
        outcome = match_clusters(
            old=await self._old_members_by_cluster(),
            new={k: frozenset(v) for k, v in membership.by_label.items()},
        )
        await self._persist_clusters(
            cluster_input=cluster_input,
            membership=membership,
            outcome=outcome,
            run=run,
        )
        await self._finalize_run(
            run=run,
            started=started,
            n_pages=len(cluster_input.page_ids),
            n_clusters=len(membership.by_label),
            result=result,
        )
        return run

    async def _cluster_input(self) -> _ClusterInput | None:
        n_pages = await self._store.count_indexed_pages()
        if n_pages < self._settings.min_pages:
            logger.info(
                "skipping cluster run: %d pages < min %d",
                n_pages,
                self._settings.min_pages,
            )
            return None
        if (model := await self._store.get_active_model()) is None:
            raise NoActiveModelError

        rows = await self._store.get_page_vectors(model_id=model.id)
        if len(rows) < self._settings.min_pages:
            return None
        indexed_ids = await indexed_page_ids(self._store, [row.page_id for row in rows])
        rows = [row for row in rows if row.page_id in indexed_ids]
        if len(rows) < self._settings.min_pages:
            return None
        page_ids = [row.page_id for row in rows]
        matrix = np.ascontiguousarray(
            np.stack([np.frombuffer(row.vector, dtype=np.float32) for row in rows])
        )
        return _ClusterInput(model_id=model.id, page_ids=page_ids, matrix=matrix)

    def _cluster_params(self, *, n_pages: int) -> ClusterParams:
        min_cluster_size, min_samples = dynamic_hdbscan_params(n_pages)
        return ClusterParams(
            algorithm=self._settings.algorithm,
            reducer=self._settings.reducer,
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            leiden_resolution=self._settings.leiden_resolution,
        )

    def _new_run(
        self,
        *,
        trigger: str,
        params: ClusterParams,
        model_id: str,
        started: datetime,
    ) -> ClusterRun:
        return ClusterRun(
            id=new_cluster_run_id(),
            trigger=trigger,
            algorithm=params.algorithm,
            params={
                "reducer": params.reducer,
                "min_cluster_size": params.min_cluster_size,
                "min_samples": params.min_samples,
                "leiden_resolution": params.leiden_resolution,
                "model_id": model_id,
            },
            started_at=started,
        )

    @staticmethod
    def _cluster_membership(
        *, page_ids: list[PageId], labels: list[int], probabilities: list[float]
    ) -> _ClusterMembership:
        by_label: dict[int, set[PageId]] = {}
        by_page: dict[PageId, float] = {}
        for page_id, label, probability in zip(
            page_ids, labels, probabilities, strict=True
        ):
            by_page[page_id] = float(probability)
            if label >= 0:
                by_label.setdefault(int(label), set()).add(page_id)
        return _ClusterMembership(by_label=by_label, probabilities=by_page)

    async def _old_members_by_cluster(self) -> dict[ClusterId, frozenset[PageId]]:
        live = await self._store.list_clusters(include_tombstoned=False)
        old: dict[ClusterId, frozenset[PageId]] = {}
        for cluster in live:
            members = await self._store.cluster_members(ClusterId(cluster.id))
            old[ClusterId(cluster.id)] = frozenset(member.page_id for member in members)
        return old

    async def _persist_clusters(
        self,
        *,
        cluster_input: _ClusterInput,
        membership: _ClusterMembership,
        outcome: MatchOutcome,
        run: ClusterRun,
    ) -> None:
        vectors_by_page = dict(
            zip(cluster_input.page_ids, cluster_input.matrix, strict=True)
        )
        keywords = await self._keywords(
            {
                str(outcome.ids_by_label[label]): members
                for label, members in membership.by_label.items()
            }
        )
        now = self._clock.now()
        for label, members in membership.by_label.items():
            cluster_id = outcome.ids_by_label[label]
            centroid = l2_normalize(
                np.mean([vectors_by_page[pid] for pid in members], axis=0)
            )
            await self._store.upsert_cluster(
                self._cluster_record(
                    cluster_id=cluster_id,
                    members=members,
                    model_id=cluster_input.model_id,
                    now=now,
                    centroid=centroid,
                    keywords=keywords.get(str(cluster_id), []),
                )
            )
            await self._store.replace_cluster_members(
                cluster_id=ClusterId(cluster_id),
                members=[
                    (pid, membership.probabilities.get(pid, 1.0)) for pid in members
                ],
            )
        await self._store.tombstone_clusters(list(outcome.tombstoned), now=now)
        await self._store.insert_lineage(run_id=run.id, records=list(outcome.lineage))
        await self._label_clusters(
            {
                str(outcome.ids_by_label[label]): keywords.get(
                    str(outcome.ids_by_label[label]), []
                )
                for label in membership.by_label
            }
        )

    @staticmethod
    def _cluster_record(
        *,
        cluster_id: ClusterId,
        members: set[PageId],
        model_id: str,
        now: datetime,
        centroid: npt.NDArray[np.float32],
        keywords: list[str],
    ) -> Cluster:
        return Cluster(
            id=cluster_id,
            label=None,
            keywords=keywords,
            size=len(members),
            model_id=model_id,
            created_at=now,
            updated_at=now,
            centroid=centroid.tobytes(),
        )

    async def _finalize_run(
        self,
        *,
        run: ClusterRun,
        started: datetime,
        n_pages: int,
        n_clusters: int,
        result: ClusterFitResult,
    ) -> None:
        finished = self._clock.now()
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1_000)
        run.n_pages = n_pages
        run.n_clusters = n_clusters
        run.n_noise = int((result.labels < 0).sum())
        await self._store.finalize_cluster_run(run)
        await self._queue.enqueue(
            kind=JobKind.CANONICALIZE_ENTITIES,
            payload={"run_id": run.id},
            idempotency_key=f"canon:{run.id}",
        )
        logger.info(
            "cluster run %s: %d pages -> %d clusters (%d noise) in %dms",
            run.id,
            run.n_pages,
            run.n_clusters,
            run.n_noise,
            run.duration_ms,
        )

    async def _keywords(
        self, members_by_cluster: dict[str, set[PageId]]
    ) -> dict[str, list[str]]:
        docs: dict[str, str] = {}
        for cluster_id, members in members_by_cluster.items():
            pages = await self._store.get_pages(list(members))
            parts = [
                f"{page.title or ''} {(page.body_text or '')[:_BODY_SNIPPET]}"
                for page in pages
            ]
            docs[cluster_id] = "\n".join(parts)
        return compute_ctfidf(docs)

    async def _label_clusters(self, keywords_by_id: dict[str, list[str]]) -> None:
        """LLM labels when configured; joined-keywords fallback otherwise."""
        for cluster_id, keywords in keywords_by_id.items():
            if not keywords:
                continue
            if self._labeler is None:
                await self._store.set_cluster_label(
                    cluster_id=ClusterId(cluster_id),
                    label=", ".join(keywords[:5]),
                )
                continue
            try:
                members = await self._store.cluster_members(ClusterId(cluster_id))
                pages = await self._store.get_pages(
                    [member.page_id for member in members[:_LABEL_TITLES]]
                )
                titles = [page.title for page in pages if page.title]
                prompt = (
                    "Give a short noun-phrase topic label (2-5 words, no "
                    "quotes) for a cluster of web pages.\nTitles: "
                    f"{json.dumps(titles[:_LABEL_TITLES])}\nKeywords: "
                    f"{', '.join(keywords)}\nLabel:"
                )
                label = await self._labeler.complete(prompt, max_tokens=20)
                await self._store.set_cluster_label(
                    cluster_id=ClusterId(cluster_id),
                    label=label.splitlines()[0][:80],
                )
            except Exception:  # noqa: BLE001 — labels are cosmetic, never fatal
                logger.warning("LLM labeling failed; keeping keywords fallback")
                await self._store.set_cluster_label(
                    cluster_id=ClusterId(cluster_id),
                    label=", ".join(keywords[:5]),
                )

    async def handle_canonicalize_job(self, job: Job) -> None:
        """Periodic re-canonicalization chained after each run."""
        merges = await self._canonicalization.periodic_recanonicalize()
        logger.info(
            "canonicalization after run %s: %d merges",
            job.payload.get("run_id", "?"),
            merges,
        )

    async def close(self) -> None:
        """Release optional cluster resources."""
        close = getattr(self._engine, "close", None)
        if callable(close):
            close()
        if self._labeler is not None:
            await self._labeler.aclose()
