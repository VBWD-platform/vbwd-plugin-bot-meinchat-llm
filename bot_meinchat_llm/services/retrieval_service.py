"""RetrievalService — top-k corpus retrieval for a consultant turn (S98.1).

The FTS baseline (D-Retrieval): a thin service over ``RagChunkRepository.search``
that returns the top-k ``bot_llm_rag_chunk`` rows for a free-text query, ranked
by Postgres ``ts_rank`` over the generated ``content_tsv``. Semantic embeddings
are an explicitly deferred enhancement — this slice is lexical only.
"""
from typing import List

from plugins.bot_meinchat_llm.bot_meinchat_llm.models.rag_chunk import RagChunk
from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
    RagChunkRepository,
)

DEFAULT_TOP_K = 5


class RetrievalService:
    """Lexical (Postgres FTS) top-k retrieval over the sales corpus."""

    def __init__(self, repository: RagChunkRepository) -> None:
        self._repository = repository

    def retrieve(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[RagChunk]:
        """The top-k corpus chunks for ``query`` (highest rank first).

        An empty corpus, a blank query, or a no-match query all yield ``[]`` —
        the consultant simply answers without retrieved grounding (no crash).
        """
        return self._repository.search(query, top_k)
