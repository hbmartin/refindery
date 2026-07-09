"""Cluster run orchestration: fit -> stable-id match -> persist -> label.

Always a full refit. Noise (-1) never becomes a cluster. Stable ids come
from the Jaccard/Hungarian matching layer; tombstoned clusters remain
resolvable by id so stale agent references degrade gracefully.
"""

import json
import logging

import numpy as np

from refindery.adapters.llm.openai_compat import OpenAiCompatClient
from refindery.application.ports.clock import Clock
from refindery.application.ports.cluster_engine import ClusterEngine, ClusterParams
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.config import ClusterSettings
from refindery.domain.clustering import dynamic_hdbscan_params, match_clusters
from refindery.domain.ctfidf import compute_ctfidf
from refindery.domain.errors import NoActiveModelError, RefinderyError
from refindery.domain.ids import ClusterId, PageId, new_cluster_run_id
from refindery.domain.models import Cluster, ClusterRun, Job, JobKind, PageStatus
from refindery.domain.rollup import l2_normalize

logger = logging.getLogger(__name__)

_BODY_SNIPPET = 2_000
_LABEL_TITLES = 10


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
        indexed = await self._store.get_pages([row.page_id for row in rows])
        indexed_ids = {page.id for page in indexed if page.status is PageStatus.INDEXED}
        rows = [row for row in rows if row.page_id in indexed_ids]
        if len(rows) < self._settings.min_pages:
            return None
        page_ids = [row.page_id for row in rows]
        matrix = np.ascontiguousarray(
            np.stack([np.frombuffer(row.vector, dtype=np.float32) for row in rows])
        )

        min_cluster_size, min_samples = dynamic_hdbscan_params(len(page_ids))
        params = ClusterParams(
            algorithm=self._settings.algorithm,
            reducer=self._settings.reducer,
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
        )
        started = self._clock.now()
        run = ClusterRun(
            id=new_cluster_run_id(),
            trigger=trigger,
            algorithm=params.algorithm,
            params={
                "reducer": params.reducer,
                "min_cluster_size": min_cluster_size,
                "min_samples": min_samples,
                "model_id": model.id,
            },
            started_at=started,
        )
        await self._store.insert_cluster_run(run)

        result = await self._engine.fit(vectors=matrix, params=params)

        new_members: dict[int, set[PageId]] = {}
        probabilities: dict[PageId, float] = {}
        for page_id, label, probability in zip(
            page_ids,
            result.labels.tolist(),
            result.probabilities.tolist(),
            strict=True,
        ):
            probabilities[page_id] = float(probability)
            if label >= 0:
                new_members.setdefault(int(label), set()).add(page_id)

        live = await self._store.list_clusters(include_tombstoned=False)
        old: dict[ClusterId, frozenset[PageId]] = {}
        for cluster in live:
            members = await self._store.cluster_members(ClusterId(cluster.id))
            old[ClusterId(cluster.id)] = frozenset(member.page_id for member in members)

        outcome = match_clusters(
            old=old, new={k: frozenset(v) for k, v in new_members.items()}
        )

        vectors_by_page = dict(zip(page_ids, matrix, strict=True))
        keywords = await self._keywords(
            {outcome.ids_by_label[k]: v for k, v in new_members.items()}
        )
        now = self._clock.now()
        for label, members in new_members.items():
            cluster_id = outcome.ids_by_label[label]
            centroid = l2_normalize(
                np.mean([vectors_by_page[pid] for pid in members], axis=0)
            )
            cluster_keywords = keywords.get(cluster_id, [])
            await self._store.upsert_cluster(
                Cluster(
                    id=cluster_id,
                    label=None,
                    keywords=cluster_keywords,
                    size=len(members),
                    model_id=model.id,
                    created_at=now,
                    updated_at=now,
                    centroid=centroid.tobytes(),
                )
            )
            await self._store.replace_cluster_members(
                cluster_id=ClusterId(cluster_id),
                members=[(pid, probabilities.get(pid, 1.0)) for pid in members],
            )
        await self._store.tombstone_clusters(list(outcome.tombstoned), now=now)
        await self._store.insert_lineage(run_id=run.id, records=list(outcome.lineage))
        await self._label_clusters(
            {
                outcome.ids_by_label[k]: keywords.get(outcome.ids_by_label[k], [])
                for k in new_members
            }
        )

        finished = self._clock.now()
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1_000)
        run.n_pages = len(page_ids)
        run.n_clusters = len(new_members)
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
        return run

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
