"""BM25 sparse encoding for the Qdrant adapter.

Uses fastembed's ``Qdrant/bm25`` model: term-frequency sparse vectors whose
IDF half is applied server-side (the sparse vector space is configured with
``modifier=IDF``). Isolated here so a different encoder can replace it
without touching the adapter.
"""

from dataclasses import dataclass

from fastembed import SparseTextEmbedding

_MODEL = "Qdrant/bm25"


@dataclass(frozen=True, slots=True)
class SparseVec:
    """Indices/values pairs for a Qdrant sparse vector."""

    indices: list[int]
    values: list[float]


class Bm25SparseEncoder:
    """Encodes documents and queries as BM25 sparse vectors."""

    def __init__(self) -> None:
        self._model = SparseTextEmbedding(model_name=_MODEL)

    def encode_documents(self, texts: list[str]) -> list[SparseVec]:
        """Encode document chunks."""
        return [
            SparseVec(
                indices=embedding.indices.tolist(),
                values=embedding.values.tolist(),
            )
            for embedding in self._model.embed(texts)
        ]

    def encode_query(self, text: str) -> SparseVec:
        """Encode a query."""
        embedding = next(iter(self._model.query_embed(text)))
        return SparseVec(
            indices=embedding.indices.tolist(), values=embedding.values.tolist()
        )
