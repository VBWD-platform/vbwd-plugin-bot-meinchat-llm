"""S98.4 — sales attribution + reward via S92 referral (integration).

Drives the FULL real path against PG, reusing S92's payout verbatim:

* the bot's ``SalesAttributionService`` mints a referral coupon bound to the BOT
  issuer (cloned from the configured template, commission snapshotted) and
  surfaces it with a working checkout deep link;
* redeeming that coupon on the real ``discount.coupon_redeemed`` path credits the
  BOT user the configured commission (asserted via
  ``token_service().get_balance(bot_user_id)``), idempotent on (coupon, invoice);
* ``reward_enabled=False`` → no coupon;
* a non-referral (plain) coupon redemption rewards the bot nothing;
* a duplicate buy-intent in the same room reuses the cached coupon (no 2nd mint).

The referral plugin's redemption subscriber (enabled in conftest) does the
credit — S98 adds no new payout. All data is created through services/repos.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from vbwd.events.bus import event_bus
from vbwd.models.enums import InvoiceStatus
from vbwd.models.invoice import UserInvoice

from plugins.discount.discount.checkout_adjustment import checkout_price_adjustment
from plugins.discount.discount.models.coupon import Coupon
from plugins.discount.discount.models.discount import (
    DiscountRule,
    DiscountScope,
    DiscountType,
)
from plugins.discount.discount.repositories.coupon_repository import CouponRepository
from plugins.discount.discount.repositories.discount_repository import (
    DiscountRepository,
)
from plugins.referral.referral.models.referral_coupon import ReferralCommissionType
from plugins.referral.referral.service_factory import build_referral_service

from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.room_coupon_repository import (  # noqa: E501
    RoomCouponRepository,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.service_factory import resolve_bot_user_id
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogItem,
    SELLABLE_PLAN,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.sales_attribution_service import (  # noqa: E501
    SalesAttributionService,
)

COMMISSION_TOKENS = 50


def _team_plan_item() -> CatalogItem:
    return CatalogItem(
        sellable_type=SELLABLE_PLAN,
        name="Team Plan",
        slug="team",
        description="",
        brutto=120.0,
        netto=100.0,
        currency="EUR",
    )


def _seed_template(session, *, value="10.00"):
    discount = DiscountRepository(session).save(
        DiscountRule(
            id=uuid4(),
            name="Bot referral template",
            slug=f"bottmpl-{uuid4().hex[:6]}",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal(value),
            scope=DiscountScope.GLOBAL,
            is_active=True,
            priority=10,
        )
    )
    return CouponRepository(session).save(
        Coupon(
            id=uuid4(),
            code=f"BOTTMPL{uuid4().hex[:6].upper()}",
            discount_id=discount.id,
            is_active=True,
        )
    )


def _configure_referral(session, template):
    service = build_referral_service(session)
    service.set_settings(
        commission_type=ReferralCommissionType.ABSOLUTE_TOKENS,
        commission_value=Decimal(str(COMMISSION_TOKENS)),
        selected_template_coupon_ids=[str(template.id)],
    )


def _build_attribution(session, *, reward_enabled=True) -> SalesAttributionService:
    return SalesAttributionService(
        referral_service_provider=lambda: build_referral_service(session),
        bot_user_id_provider=resolve_bot_user_id,
        bot_nickname="consultant",
        room_coupon_cache=RoomCouponRepository(session),
        reward_enabled=reward_enabled,
    )


def _seed_buyer(session):
    from vbwd.models.enums import UserRole, UserStatus
    from vbwd.models.user import User

    buyer = User(
        id=uuid4(),
        email=f"buyer-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    session.add(buyer)
    session.commit()
    return buyer


def _redeem(session, *, code, buyer, subtotal="100.00"):
    result = checkout_price_adjustment(
        code=code,
        subtotal=Decimal(subtotal),
        user_id=str(buyer.id),
        scope="SUBSCRIPTION",
        currency="EUR",
    )
    assert result.valid is True
    invoice = UserInvoice(
        id=uuid4(),
        user_id=buyer.id,
        invoice_number=f"INV-{uuid4().hex[:8]}",
        amount=Decimal(subtotal),
        currency="EUR",
        status=InvoiceStatus.PAID,
    )
    session.add(invoice)
    session.commit()
    result.on_committed(str(invoice.id), str(buyer.id))
    return invoice.id


def test_buy_intent_mints_bot_coupon_and_credits_bot_on_redemption(app, db):
    template = _seed_template(db.session)
    _configure_referral(db.session, template)
    attribution = _build_attribution(db.session)

    offer = attribution.offer_for_buy(room_id="room-credit", item=_team_plan_item())
    db.session.commit()

    assert offer is not None
    assert "/tarif-plans/team" in offer.deep_link
    assert f"coupon={offer.coupon_code}" in offer.deep_link

    bot_user_id = resolve_bot_user_id()
    token_service = app.container.token_service()
    assert token_service.get_balance(bot_user_id) == 0

    buyer = _seed_buyer(db.session)
    _redeem(db.session, code=offer.coupon_code, buyer=buyer)

    # The bot (issuer) was credited the configured commission via the S92 path.
    assert token_service.get_balance(bot_user_id) == COMMISSION_TOKENS


def test_duplicate_redemption_event_credits_bot_once(app, db):
    template = _seed_template(db.session)
    _configure_referral(db.session, template)
    attribution = _build_attribution(db.session)
    offer = attribution.offer_for_buy(room_id="room-idem", item=_team_plan_item())
    db.session.commit()

    bot_user_id = resolve_bot_user_id()
    buyer = _seed_buyer(db.session)
    invoice_id = _redeem(db.session, code=offer.coupon_code, buyer=buyer)

    cache = RoomCouponRepository(db.session)
    coupon_id = (
        db.session.query(
            __import__(
                "plugins.bot_meinchat_llm.bot_meinchat_llm.models.room_coupon",
                fromlist=["RoomCoupon"],
            ).RoomCoupon
        )
        .filter_by(room_id="room-idem")
        .first()
        .coupon_id
    )

    # Replay the same (coupon, invoice) redemption — must pay only once.
    event_bus.publish(
        "discount.coupon_redeemed",
        {
            "coupon_id": str(coupon_id),
            "coupon_code": offer.coupon_code,
            "user_id": str(buyer.id),
            "invoice_id": str(invoice_id),
            "discount_amount": "10.00",
            "sale_net_amount": "100.00",
        },
    )

    token_service = app.container.token_service()
    assert token_service.get_balance(bot_user_id) == COMMISSION_TOKENS
    assert cache.find_code("room-idem") == offer.coupon_code


def test_reward_disabled_offers_no_coupon(app, db):
    template = _seed_template(db.session)
    _configure_referral(db.session, template)
    attribution = _build_attribution(db.session, reward_enabled=False)

    offer = attribution.offer_for_buy(room_id="room-off", item=_team_plan_item())

    assert offer is None


def test_duplicate_buy_intent_reuses_cached_coupon(app, db):
    template = _seed_template(db.session)
    _configure_referral(db.session, template)
    attribution = _build_attribution(db.session)

    first = attribution.offer_for_buy(room_id="room-reuse", item=_team_plan_item())
    db.session.commit()
    second = attribution.offer_for_buy(room_id="room-reuse", item=_team_plan_item())

    assert first is not None and second is not None
    assert first.coupon_code == second.coupon_code


def test_plain_coupon_redemption_rewards_bot_nothing(app, db):
    # A plain (non-referral) discount coupon redemption must not credit the bot.
    bot_user_id = resolve_bot_user_id()
    db.session.commit()
    token_service = app.container.token_service()
    baseline = token_service.get_balance(bot_user_id)

    discount = DiscountRepository(db.session).save(
        DiscountRule(
            id=uuid4(),
            name="Plain",
            slug=f"plain-{uuid4().hex[:6]}",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10.00"),
            scope=DiscountScope.GLOBAL,
            is_active=True,
            priority=10,
        )
    )
    coupon = CouponRepository(db.session).save(
        Coupon(
            id=uuid4(),
            code=f"PLAIN{uuid4().hex[:6].upper()}",
            discount_id=discount.id,
            is_active=True,
        )
    )
    buyer = _seed_buyer(db.session)
    _redeem(db.session, code=coupon.code, buyer=buyer)

    assert token_service.get_balance(bot_user_id) == baseline
