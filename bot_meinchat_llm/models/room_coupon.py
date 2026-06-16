"""RoomCoupon model — one bot-issued referral coupon cached per chat room (S98.4).

The consultant mints ONE referral coupon per ``(bot, room)`` and reuses it for
every subsequent buy-intent in that room, so a chatty conversation never spawns
duplicate coupons. This table is that cache: a unique ``room_id`` → the minted
discount coupon's code + id.

``room_id`` is the provider-scoped chat identifier carried on the inbound's
``chat_ref.chat_id`` (a meinchat conversation id). It is unique so the cache is a
true one-coupon-per-room map.
"""
from sqlalchemy import UniqueConstraint

from vbwd.extensions import db
from vbwd.models.base import BaseModel


class RoomCoupon(BaseModel):
    """A bot-issued referral coupon remembered for one chat room."""

    __tablename__ = "bot_llm_room_coupon"
    __table_args__ = (
        UniqueConstraint("room_id", name="uq_bot_llm_room_coupon_room_id"),
    )

    room_id = db.Column(db.String(255), nullable=False, index=True)
    coupon_code = db.Column(db.String(255), nullable=False)
    coupon_id = db.Column(db.UUID, nullable=True)

    def to_dict(self) -> dict:
        """Serialise the cache row (timestamps as ISO-8601 strings)."""
        return {
            "id": str(self.id),
            "room_id": self.room_id,
            "coupon_code": self.coupon_code,
            "coupon_id": str(self.coupon_id) if self.coupon_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
