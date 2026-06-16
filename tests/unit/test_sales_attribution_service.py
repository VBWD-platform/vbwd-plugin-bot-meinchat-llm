"""S98.4 — SalesAttributionService unit tests (fakes, no DB, no network).

Verifies the offer/reuse logic in isolation:

* a buy intent mints a referral coupon bound to the BOT issuer and surfaces the
  discount + the exact code + a working checkout deep link;
* a duplicate buy intent in the same room REUSES the cached coupon (no second
  mint);
* ``reward_enabled=False`` never mints / never offers a coupon;
* an unconfigured referral program (mint raises ``ReferralError``) degrades to a
  plain recommendation (no coupon, no crash);
* the deep link shape is per-sellable-type (plan vs product).
"""
from dataclasses import dataclass
from typing import Optional

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogItem,
    SELLABLE_PLAN,
    SELLABLE_PRODUCT,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.sales_attribution_service import (  # noqa: E501
    SalesAttributionService,
)


_BOT_USER_ID = "00000000-0000-0000-0000-0000000000bb"


@dataclass
class _FakeReferralCoupon:
    coupon_code: str


class _RecordingReferralService:
    def __init__(self, *, raises: bool = False):
        self.mint_calls = 0
        self.last_issuer_user_id = None
        self._raises = raises

    def mint(self, *, issuer_user_id, issuer_nickname, raw_prefix, **kwargs):
        if self._raises:
            from plugins.referral.referral.services.referral_service import (
                ReferralError,
            )

            raise ReferralError("No referral coupon template is configured.")
        self.mint_calls += 1
        self.last_issuer_user_id = issuer_user_id
        return _FakeReferralCoupon(coupon_code=f"BOT_{raw_prefix}_ABC123")


class _RoomCouponCache:
    """In-memory stand-in for the per-room coupon cache repository."""

    def __init__(self):
        self._store = {}

    def find_code(self, room_id: str) -> Optional[str]:
        return self._store.get(room_id)

    def remember(self, room_id: str, coupon_code: str, coupon_id) -> None:
        self._store[room_id] = coupon_code


def _team_plan() -> CatalogItem:
    return CatalogItem(
        sellable_type=SELLABLE_PLAN,
        name="Team Plan",
        slug="team",
        description="",
        brutto=120.0,
        netto=100.0,
        currency="EUR",
        billing_period="monthly",
    )


def _shop_product() -> CatalogItem:
    return CatalogItem(
        sellable_type=SELLABLE_PRODUCT,
        name="Mug",
        slug="mug",
        description="",
        brutto=12.0,
        netto=10.0,
        currency="EUR",
    )


def _build_service(referral_service, cache, *, reward_enabled=True):
    return SalesAttributionService(
        referral_service_provider=lambda: referral_service,
        bot_user_id_provider=lambda: _BOT_USER_ID,
        bot_nickname="consultant",
        room_coupon_cache=cache,
        reward_enabled=reward_enabled,
    )


def test_buy_intent_mints_coupon_bound_to_bot_issuer():
    referral = _RecordingReferralService()
    cache = _RoomCouponCache()
    service = _build_service(referral, cache)

    offer = service.offer_for_buy(room_id="room-1", item=_team_plan())

    assert offer is not None
    assert referral.mint_calls == 1
    assert str(referral.last_issuer_user_id) == _BOT_USER_ID
    assert offer.coupon_code.startswith("BOT_")
    # The deep link carries the coupon for a plan checkout.
    assert "/tarif-plans/team" in offer.deep_link
    assert f"coupon={offer.coupon_code}" in offer.deep_link


def test_duplicate_buy_intent_reuses_cached_coupon():
    referral = _RecordingReferralService()
    cache = _RoomCouponCache()
    service = _build_service(referral, cache)

    first = service.offer_for_buy(room_id="room-1", item=_team_plan())
    second = service.offer_for_buy(room_id="room-1", item=_team_plan())

    assert referral.mint_calls == 1  # second reused the cache
    assert first.coupon_code == second.coupon_code


def test_reward_disabled_never_offers_coupon():
    referral = _RecordingReferralService()
    cache = _RoomCouponCache()
    service = _build_service(referral, cache, reward_enabled=False)

    offer = service.offer_for_buy(room_id="room-1", item=_team_plan())

    assert offer is None
    assert referral.mint_calls == 0


def test_unconfigured_referral_degrades_to_no_coupon():
    referral = _RecordingReferralService(raises=True)
    cache = _RoomCouponCache()
    service = _build_service(referral, cache)

    offer = service.offer_for_buy(room_id="room-1", item=_team_plan())

    assert offer is None  # graceful: a plain recommendation, no crash


def test_product_deep_link_shape():
    referral = _RecordingReferralService()
    cache = _RoomCouponCache()
    service = _build_service(referral, cache)

    offer = service.offer_for_buy(room_id="room-9", item=_shop_product())

    assert offer is not None
    assert "/shop/products/mug" in offer.deep_link
    assert f"coupon={offer.coupon_code}" in offer.deep_link
