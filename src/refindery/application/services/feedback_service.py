"""Relevance feedback: append-only, joined to the query log at eval time.

Unknown query_ids are accepted by design — validating them would route reads
through the single DuckDB writer; orphans are dropped by the eval-time join.
"""

from refindery.application.ports.clock import Clock
from refindery.application.ports.query_log import FeedbackRecord, QueryLogSink
from refindery.domain.ids import PageId, QueryId


class FeedbackService:
    """Records POST /v1/feedback."""

    def __init__(self, *, query_log: QueryLogSink, clock: Clock) -> None:
        self._query_log = query_log
        self._clock = clock

    def record(self, *, query_id: QueryId, page_id: PageId, relevant: bool) -> None:
        """Buffer one feedback row."""
        self._query_log.log_feedback(
            FeedbackRecord(
                query_id=query_id,
                page_id=page_id,
                relevant=relevant,
                ts=self._clock.now(),
            )
        )
