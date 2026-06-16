"""RagChunk model — one indexed slice of a sales-corpus document (S98.1).

Each row is a ~600-900-token chunk of a ``.md`` / ``.pdf`` sales document read
from the corpus directory. ``content_tsv`` is a Postgres ``tsvector`` GENERATED
ALWAYS column over ``content`` (mirrors the proven cms_post search-vector
pattern): the column keeps itself correct on every write — no trigger code, no
Python maintenance — so ``RetrievalService`` can rank chunks with
``plainto_tsquery`` + ``ts_rank`` over a GIN index.

``file_hash`` is the SHA-256 of the whole source file, repeated on every chunk
of that file, so ``RagIngestService`` can skip re-chunking an unchanged file
(hash-incremental ingest) by comparing the stored hash before doing any work.

The weighting expression is the single source of truth shared between this
model's generated column (via ``create_all``) and the in-plugin Alembic
migration (DRY).
"""
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR

from vbwd.extensions import db
from vbwd.models.base import BaseModel


# Single-column tsvector over the chunk body. ``english`` must match the search
# config used by RetrievalService's ``plainto_tsquery`` so query and index speak
# the same language. Source of truth for both create_all and the migration.
CONTENT_TSV_EXPRESSION = "to_tsvector('english', coalesce(content, ''))"
CONTENT_TSV_INDEX = "ix_bot_llm_rag_chunk_content_tsv"


class RagChunk(BaseModel):
    """One full-text-indexed chunk of a sales-corpus document."""

    __tablename__ = "bot_llm_rag_chunk"
    __table_args__ = (
        UniqueConstraint(
            "source_file",
            "chunk_index",
            name="uq_bot_llm_rag_chunk_source_index",
        ),
        Index(
            CONTENT_TSV_INDEX,
            "content_tsv",
            postgresql_using="gin",
        ),
    )

    source_file = db.Column(db.String(1024), nullable=False, index=True)
    chunk_index = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_tsv = db.Column(
        TSVECTOR,
        db.Computed(CONTENT_TSV_EXPRESSION, persisted=True),
        nullable=True,
    )
    file_hash = db.Column(db.String(64), nullable=False, index=True)

    def to_dict(self) -> dict:
        """Serialise the chunk (timestamps as ISO-8601 strings)."""
        return {
            "id": str(self.id),
            "source_file": self.source_file,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "file_hash": self.file_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
