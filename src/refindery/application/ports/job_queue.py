"""Durable job queue port.

Implementations pair an execution engine (huey) with the jobs ledger in the
metadata store; the ledger is the source of truth for status.
"""

from typing import Protocol

from refindery.domain.ids import JobId
from refindery.domain.models import JobKind


class JobQueue(Protocol):
    """Enqueue and manage durable jobs."""

    async def enqueue(
        self,
        *,
        kind: JobKind,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> JobId | None:
        """Enqueue a job; returns None when the idempotency key is a duplicate."""
        ...

    async def retry(self, job_id: JobId) -> JobId:
        """Re-enqueue a dead job (resets attempts)."""
        ...

    async def recover(self) -> int:
        """Startup recovery: re-enqueue expired leases and orphaned pending rows.

        Returns the number of jobs re-enqueued.
        """
        ...

    async def start(self) -> None:
        """Start the embedded consumer."""
        ...

    async def stop(self) -> None:
        """Stop the embedded consumer and wait for the in-flight job."""
        ...
