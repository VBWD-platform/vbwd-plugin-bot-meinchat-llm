"""RagChunkRepository — data access for the RAG corpus index (S98.1).

Two concerns: ingest bookkeeping (replace a file's chunks, look up its stored
hash) and retrieval (Postgres FTS over the generated ``content_tsv`` GIN index,
ranked by ``ts_rank``). The retrieval query mirrors the cms SearchRepository
shape (``plainto_tsquery`` + ``ts_rank``) so a query and the generated index
speak the same ``english`` text-search config.
"""
from typing import List, Optional

from sqlalchemy import func

from vbwd.repositories.base import BaseRepository

from plugins.bot_meinchat_llm.bot_meinchat_llm.models.rag_chunk import RagChunk


# Must match the language of the model's generated ``content_tsv`` column so the
# query vector and the indexed vector are comparable.
SEARCH_CONFIG = "english"


class RagChunkRepository(BaseRepository[RagChunk]):
    """Repository for the sales-corpus RAG chunks."""

    def __init__(self, session):
        super().__init__(session=session, model=RagChunk)

    def get_hash_for_file(self, source_file: str) -> Optional[str]:
        """The stored ``file_hash`` for a source file, or ``None`` if unindexed.

        All chunks of one file share the same hash, so the first row's hash is
        authoritative. ``None`` means the file has never been ingested.
        """
        row = (
            self._session.query(RagChunk.file_hash)
            .filter(RagChunk.source_file == source_file)
            .first()
        )
        return row[0] if row is not None else None

    def delete_for_file(self, source_file: str) -> int:
        """Remove every chunk of a source file (used before a re-chunk)."""
        deleted = (
            self._session.query(RagChunk)
            .filter(RagChunk.source_file == source_file)
            .delete(synchronize_session=False)
        )
        self._session.flush()
        return int(deleted)

    def add_chunks(self, chunks: List[RagChunk]) -> None:
        """Persist a batch of freshly built chunks for a single file."""
        self._session.add_all(chunks)
        self._session.flush()

    def search(self, query: str, top_k: int) -> List[RagChunk]:
        """Top-k chunks for ``query`` by Postgres FTS rank (highest first).

        A blank query returns nothing (the caller decides what to do with an
        empty corpus / empty query); a query with no lexical match returns an
        empty list rather than raising.
        """
        if not query or not query.strip() or top_k <= 0:
            return []
        ts_query = func.plainto_tsquery(SEARCH_CONFIG, query)
        rank = func.ts_rank(RagChunk.content_tsv, ts_query)
        return (
            self._session.query(RagChunk)
            .filter(RagChunk.content_tsv.op("@@")(ts_query))
            .order_by(rank.desc(), RagChunk.source_file, RagChunk.chunk_index)
            .limit(top_k)
            .all()
        )
