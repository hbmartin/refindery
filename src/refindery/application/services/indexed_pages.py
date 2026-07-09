"""Retrieval surfaces hydrate INDEXED pages only; one filter enforces it.

Search, suggestions, similar, compare, and clustering all hydrate page ids
that may point at queued/failed/purged pages. Metadata is authoritative:
anything not currently INDEXED is dropped here, in one place.
"""

from collections.abc import Iterable

from refindery.application.ports.metadata_store import MetadataStore
from refindery.domain.ids import PageId
from refindery.domain.models import Page, PageStatus


async def indexed_pages_by_id(
    store: MetadataStore, page_ids: Iterable[PageId]
) -> dict[PageId, Page]:
    """Hydrate ``page_ids`` and keep only pages currently INDEXED."""
    pages = await store.get_pages(list(page_ids))
    return {page.id: page for page in pages if page.status is PageStatus.INDEXED}


async def indexed_page_ids(
    store: MetadataStore, page_ids: Iterable[PageId]
) -> frozenset[PageId]:
    """Return the subset of ``page_ids`` whose pages are currently INDEXED."""
    return frozenset(await indexed_pages_by_id(store, page_ids))
