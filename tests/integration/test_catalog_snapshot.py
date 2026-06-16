"""S98.2 — CatalogSnapshotService against real repos + the core PriceFactory.

Seeds one active and one inactive tariff plan, then asserts the snapshot lists
ONLY the active plan and carries the exact PriceFactory brutto + currency (not
the raw stored price). Skips when the subscription package is absent (a bare
per-plugin clone) — subscription is a declared peer in the SDK.
"""
import uuid

import pytest

pytest.importorskip("plugins.subscription.subscription.models")

from vbwd.models.enums import BillingPeriod  # noqa: E402

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E402,E501
    CatalogSnapshotService,
    SELLABLE_PLAN,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan  # noqa: E402


def _seed_default_currency(db):
    """Seed the baseline EUR row so the core PriceFactory resolves a currency.

    Pricing goes through the core PriceFactory, which reads the default currency
    (``default_currency`` setting, EUR by default) against the catalog. Seed the
    row through the model inside the test's rolled-back transaction — never raw
    SQL ([[feedback_no_direct_db_for_test_data]])."""
    from decimal import Decimal

    from vbwd.models.currency import Currency

    if not db.session.query(Currency).filter_by(code="EUR").first():
        db.session.add(
            Currency(
                id=uuid.uuid4(),
                code="EUR",
                name="Euro",
                symbol="€",
                exchange_rate=Decimal("1.0"),
                decimal_places=2,
            )
        )
        db.session.flush()


def _make_plan(*, name, slug, price, is_active):
    return TarifPlan(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        description=f"{name} description",
        price=price,
        billing_period=BillingPeriod.MONTHLY,
        is_active=is_active,
    )


@pytest.mark.integration
def test_snapshot_lists_only_active_plans_priced_via_factory(app):
    from flask import current_app

    from vbwd.extensions import db

    _seed_default_currency(db)
    active = _make_plan(name="Team", slug="team-snap", price=100.0, is_active=True)
    inactive = _make_plan(
        name="Legacy", slug="legacy-snap", price=999.0, is_active=False
    )
    db.session.add_all([active, inactive])
    db.session.flush()

    price_factory = current_app.container.price_factory()
    service = CatalogSnapshotService(lambda: db.session, price_factory)
    block = service.snapshot()

    plan_items = [item for item in block.items if item.sellable_type == SELLABLE_PLAN]
    slugs = {item.slug for item in plan_items}
    assert "team-snap" in slugs
    assert "legacy-snap" not in slugs

    team = next(item for item in plan_items if item.slug == "team-snap")
    # The brutto must equal the factory's output for this exact plan, never the
    # raw stored price read directly.
    expected = price_factory.get_price_from_object(active)
    assert team.brutto == expected.brutto
    assert team.currency == expected.currency
    assert team.billing_period == BillingPeriod.MONTHLY.value
