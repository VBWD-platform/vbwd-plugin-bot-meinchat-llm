"""RoomCouponRepository — the per-room bot referral coupon cache (S98.4).

Implements the narrow ``room_coupon_cache`` port the ``SalesAttributionService``
depends on: look up the room's cached coupon code, and remember a freshly minted
one. Keeping the cache behind this repo means the service stays unit-testable
with a tiny in-memory fake (Interface Segregation — the service depends only on
``find_code`` / ``remember``).
"""
from typing import Optional
from uuid import UUID

from vbwd.repositories.base import BaseRepository

from plugins.bot_meinchat_llm.bot_meinchat_llm.models.room_coupon import RoomCoupon


class RoomCouponRepository(BaseRepository[RoomCoupon]):
    """Data access for the per-room bot-issued referral coupon cache."""

    def __init__(self, session):
        super().__init__(session=session, model=RoomCoupon)

    def find_code(self, room_id: str) -> Optional[str]:
        """The room's cached coupon code, or ``None`` if it has none yet."""
        row = (
            self._session.query(RoomCoupon.coupon_code)
            .filter(RoomCoupon.room_id == room_id)
            .first()
        )
        return row[0] if row is not None else None

    def remember(
        self, room_id: str, coupon_code: str, coupon_id: Optional[UUID]
    ) -> None:
        """Cache the minted coupon for the room (idempotent on ``room_id``).

        If the room already has a row (a race), the existing one is kept — the
        cache only needs one stable coupon per room.
        """
        existing = (
            self._session.query(RoomCoupon)
            .filter(RoomCoupon.room_id == room_id)
            .first()
        )
        if existing is not None:
            return
        self._session.add(
            RoomCoupon(
                room_id=room_id,
                coupon_code=coupon_code,
                coupon_id=coupon_id,
            )
        )
        self._session.flush()
