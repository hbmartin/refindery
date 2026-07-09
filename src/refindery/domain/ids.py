"""Typed identifiers and uuid7 generation.

uuid7 identifiers are time-ordered, which keeps SQLite b-tree inserts
append-mostly and makes ``ORDER BY id`` a creation-time ordering.
"""

from typing import NewType

import uuid6

PageId = NewType("PageId", str)
ChunkId = NewType("ChunkId", str)
JobId = NewType("JobId", str)
EntityId = NewType("EntityId", str)
ClusterId = NewType("ClusterId", str)
ClusterRunId = NewType("ClusterRunId", str)
BlacklistId = NewType("BlacklistId", str)
QueryId = NewType("QueryId", str)


def new_page_id() -> PageId:
    """Generate a fresh time-ordered page id."""
    return PageId(str(uuid6.uuid7()))


def new_chunk_id() -> ChunkId:
    """Generate a fresh time-ordered chunk id."""
    return ChunkId(str(uuid6.uuid7()))


def new_job_id() -> JobId:
    """Generate a fresh time-ordered job id."""
    return JobId(str(uuid6.uuid7()))


def new_entity_id() -> EntityId:
    """Generate a fresh time-ordered entity id."""
    return EntityId(str(uuid6.uuid7()))


def new_cluster_id() -> ClusterId:
    """Generate a fresh time-ordered cluster id."""
    return ClusterId(str(uuid6.uuid7()))


def new_cluster_run_id() -> ClusterRunId:
    """Generate a fresh time-ordered cluster-run id."""
    return ClusterRunId(str(uuid6.uuid7()))


def new_blacklist_id() -> BlacklistId:
    """Generate a fresh time-ordered blacklist-rule id."""
    return BlacklistId(str(uuid6.uuid7()))


def new_query_id() -> QueryId:
    """Generate a fresh time-ordered query id."""
    return QueryId(str(uuid6.uuid7()))
