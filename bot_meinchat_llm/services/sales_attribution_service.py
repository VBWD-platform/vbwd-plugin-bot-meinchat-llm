"""SalesAttributionService — bot-issued referral coupon + checkout link (S98.4).

On a buy intent the consultant offers a referral coupon **bound to the bot
issuer**, reusing the S92 ``referral`` payout verbatim — S98 adds no new money /
token path. The bot is the referral *issuer*; the guest checks out on the web
with the coupon; the existing ``discount.coupon_redeemed`` → ``referral`` payout
credits the bot user's token balance.

Key behaviours (the binding requirements):

* **DRY / reuse** — minting goes through the S92 ``ReferralService.mint(...)``
  (cloning the configured discount template, snapshotting commission). This
  service owns *no* coupon math and *no* payout.
* **One coupon per ``(bot, room)``** — a small plugin-owned cache
  (``room_coupon_cache``) returns the room's existing code so repeated
  buy-intents never spawn duplicate coupons.
* **Graceful degradation** — if the admin has not configured the S92 referral
  settings / template, ``mint`` raises ``ReferralError``; the service returns
  ``None`` so the consultant falls back to a plain recommendation (no crash).
* **``reward_enabled=False``** suppresses the offer entirely (never mints).
* **D-Purchase-scope** — the offer is a discount + the exact code + a *deep link*
  to the existing web checkout. The bot never runs the payment itself.

Collaborators are injected as providers (DIP) so this stays unit-testable with
fakes and never hard-imports a disabled peer at module load.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogItem,
    SELLABLE_ADDON,
    SELLABLE_PLAN,
    SELLABLE_PRODUCT,
    SELLABLE_RESOURCE,
)

logger = logging.getLogger(__name__)

#: The coupon-code prefix for bot-issued referral coupons (distinguishes them in
#: the referral stats list and keeps the generated code readable).
BOT_COUPON_PREFIX = "CONSULT"


@dataclass(frozen=True)
class CouponOffer:
    """A referral discount the consultant surfaces on a buy intent."""

    coupon_code: str
    deep_link: str
    item: CatalogItem

    def to_reply_text(self) -> str:
        """A short, guest-economy-friendly offer line."""
        return (
            f"Use code {self.coupon_code} for a discount on {self.item.name} — "
            f"check out here: {self.deep_link}"
        )


class SalesAttributionService:
    """Mint/reuse a per-room bot referral coupon and build a checkout deep link."""

    def __init__(
        self,
        *,
        referral_service_provider: Callable[[], Any],
        bot_user_id_provider: Callable[[], Any],
        bot_nickname: str,
        room_coupon_cache: Any,
        reward_enabled: bool = True,
        base_url: str = "",
    ) -> None:
        self._referral_service_provider = referral_service_provider
        self._bot_user_id_provider = bot_user_id_provider
        self._bot_nickname = bot_nickname
        self._room_coupon_cache = room_coupon_cache
        self._reward_enabled = reward_enabled
        # Absolute site origin for checkout links (e.g. http://localhost:8080).
        # Empty ⇒ relative paths (back-compat).
        self._base_url = (base_url or "").rstrip("/")

    def offer_for_buy(self, *, room_id: str, item: CatalogItem) -> Optional[CouponOffer]:
        """Return a ``CouponOffer`` for ``item``, or ``None`` to fall back plain.

        ``None`` is returned (a plain recommendation, no crash) when rewards are
        disabled, when the room already has a coupon that can be reused, or when
        the referral program is not configured.
        """
        if not self._reward_enabled:
            return None

        cached_code = self._room_coupon_cache.find_code(room_id)
        if cached_code:
            return CouponOffer(
                coupon_code=cached_code,
                deep_link=self._build_deep_link(item, cached_code),
                item=item,
            )

        coupon_code = self._mint_for_room(room_id)
        if coupon_code is None:
            return None
        return CouponOffer(
            coupon_code=coupon_code,
            deep_link=self._build_deep_link(item, coupon_code),
            item=item,
        )

    # ── minting ─────────────────────────────────────────────────────────────
    def _mint_for_room(self, room_id: str) -> Optional[str]:
        """Mint a bot-issued referral coupon for the room, cache, return its code.

        Degrades to ``None`` on any referral configuration / runtime error so the
        consultant can still recommend without a coupon.
        """
        try:
            bot_user_id = self._bot_user_id_provider()
            if bot_user_id is None:
                return None
            referral_coupon = self._referral_service_provider().mint(
                issuer_user_id=bot_user_id,
                issuer_nickname=self._bot_nickname,
                raw_prefix=BOT_COUPON_PREFIX,
            )
        except Exception as error:  # noqa: BLE001 — degrade to no-coupon
            logger.info(
                "[bot-meinchat-llm] referral coupon not minted (%s) — "
                "offering a plain recommendation",
                error,
            )
            return None

        self._room_coupon_cache.remember(
            room_id, referral_coupon.coupon_code, getattr(referral_coupon, "id", None)
        )
        return referral_coupon.coupon_code

    # ── deep links (D-Purchase-scope) ───────────────────────────────────────
    def _build_deep_link(self, item: CatalogItem, coupon_code: str) -> str:
        """An ABSOLUTE web checkout link with the coupon pre-applied, per sellable
        type (prefixed with the configured ``base_url``; relative if unset).

        Telegram parity is out of scope (the sprint): these are web-app links.
        """
        path_by_type = {
            SELLABLE_PLAN: f"/tarif-plans/{item.slug}",
            SELLABLE_ADDON: f"/tarif-plans?addon={item.slug}",
            SELLABLE_PRODUCT: f"/shop/products/{item.slug}",
            SELLABLE_RESOURCE: f"/booking/resources/{item.slug}",
        }
        path = path_by_type.get(item.sellable_type, f"/tarif-plans/{item.slug}")
        separator = "&" if "?" in path else "?"
        return f"{self._base_url}{path}{separator}coupon={coupon_code}"
