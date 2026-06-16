"""S98.4 — create bot_llm_room_coupon (per-room bot referral coupon cache).

One row per chat room caching the bot-issued referral coupon minted for that
room, so repeated buy-intents reuse the same coupon (no duplicate mints).
``room_id`` is unique — the cache is a one-coupon-per-room map.

Anchored on the plugin's OWN prior revision ``20260616_1000_bot_llm_rag`` (NOT a
core or peer revision) so the plugin's migration chain resolves standalone
([[project_migration_graph_fragmentation]]). Revision id ≤ 32 chars. Validated
up → down → up.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260616_1100_bot_llm_room"
down_revision = "20260616_1000_bot_llm_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_llm_room_coupon",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("coupon_code", sa.String(length=255), nullable=False),
        sa.Column("coupon_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("room_id", name="uq_bot_llm_room_coupon_room_id"),
    )
    op.create_index(
        "ix_bot_llm_room_coupon_room_id",
        "bot_llm_room_coupon",
        ["room_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_llm_room_coupon_room_id", table_name="bot_llm_room_coupon"
    )
    op.drop_table("bot_llm_room_coupon")
