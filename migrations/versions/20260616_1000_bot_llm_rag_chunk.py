"""S98.1 — create bot_llm_rag_chunk (RAG corpus index, FTS baseline).

One table holding the chunked sales corpus with a Postgres ``tsvector``
GENERATED ALWAYS column (``content_tsv``) + a GIN index, so retrieval ranks
chunks with ``plainto_tsquery`` + ``ts_rank`` and the vector stays correct on
every write — no trigger code (mirrors the cms_post search-vector pattern).

Anchored on the core root revision ``vbwd_001`` — bot-meinchat-llm is a
brand-new plugin with no prior revision and the core root is present in every
deployment, so this resolves standalone without depending on any other plugin
([[project_migration_graph_fragmentation]]). The tsvector expression is shared
with the RagChunk model (``CONTENT_TSV_EXPRESSION``) so create_all() and this
migration produce identical DDL (DRY). Revision id ≤ 32 chars. Validated
up → down → up.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

from plugins.bot_meinchat_llm.bot_meinchat_llm.models.rag_chunk import (
    CONTENT_TSV_EXPRESSION,
    CONTENT_TSV_INDEX,
)

revision = "20260616_1000_bot_llm_rag"
down_revision = "vbwd_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_llm_rag_chunk",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_file", sa.String(length=1024), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_tsv",
            TSVECTOR(),
            sa.Computed(CONTENT_TSV_EXPRESSION, persisted=True),
            nullable=True,
        ),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "source_file",
            "chunk_index",
            name="uq_bot_llm_rag_chunk_source_index",
        ),
    )
    op.create_index(
        "ix_bot_llm_rag_chunk_source_file",
        "bot_llm_rag_chunk",
        ["source_file"],
    )
    op.create_index(
        "ix_bot_llm_rag_chunk_file_hash",
        "bot_llm_rag_chunk",
        ["file_hash"],
    )
    op.create_index(
        CONTENT_TSV_INDEX,
        "bot_llm_rag_chunk",
        ["content_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(CONTENT_TSV_INDEX, table_name="bot_llm_rag_chunk")
    op.drop_index("ix_bot_llm_rag_chunk_file_hash", table_name="bot_llm_rag_chunk")
    op.drop_index("ix_bot_llm_rag_chunk_source_file", table_name="bot_llm_rag_chunk")
    op.drop_table("bot_llm_rag_chunk")
