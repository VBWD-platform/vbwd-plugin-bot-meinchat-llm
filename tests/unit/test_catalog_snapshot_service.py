"""S98.2 — CatalogSnapshotService unit tests (fake repos + fake PriceFactory).

Verifies: each item carries the EXACT PriceFactory brutto + currency (never the
raw price); billing period is flattened; a category whose repo import fails is
simply absent (soft-guard, no crash).
"""
from dataclasses import dataclass
from typing import List

import builtins

import pytest

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogSnapshotService,
    SELLABLE_PLAN,
    SELLABLE_PRODUCT,
)


@dataclass
class _Tax:
    code: str
    rate: float


@dataclass
class _FakeSellable:
    name: str
    slug: str
    description: str
    raw_price: float
    taxes: List[_Tax]
    billing_period: object = None


@dataclass
class _FakePrice:
    netto: float
    brutto: float
    currency: str
    taxes: list


class _FakePriceFactory:
    """Returns brutto = raw_price * 1.2 in EUR — deliberately different from the
    raw price so a test proves the snapshot renders the FACTORY value, not the
    raw column."""

    def get_price_from_object(self, sellable):
        return _FakePrice(
            netto=sellable.raw_price,
            brutto=round(sellable.raw_price * 1.2, 4),
            currency="EUR",
            taxes=[],
        )


def _patch_only_plans(monkeypatch, plans):
    """Make the plan reader return ``plans`` and every other reader empty."""
    service_cls = CatalogSnapshotService
    monkeypatch.setattr(service_cls, "_snapshot_addons", lambda self: [], raising=True)
    monkeypatch.setattr(
        service_cls, "_snapshot_products", lambda self: [], raising=True
    )
    monkeypatch.setattr(
        service_cls, "_snapshot_resources", lambda self: [], raising=True
    )

    class _FakePlanRepo:
        def __init__(self, _session):
            pass

        def find_active(self):
            return plans

    monkeypatch.setattr(
        service_cls,
        "_snapshot_plans",
        lambda self: [
            self._build_item(SELLABLE_PLAN, plan, self._billing_value(plan))
            for plan in _FakePlanRepo(None).find_active()
        ],
        raising=True,
    )


def test_snapshot_prices_via_factory_not_raw(monkeypatch):
    plan = _FakeSellable(
        name="Team Plan",
        slug="team",
        description="For small teams.\nSecond line.",
        raw_price=100.0,
        taxes=[_Tax("VAT", 20.0)],
        billing_period="monthly",
    )
    _patch_only_plans(monkeypatch, [plan])
    service = CatalogSnapshotService(lambda: None, _FakePriceFactory())

    block = service.snapshot()
    assert len(block.items) == 1
    item = block.items[0]
    assert item.sellable_type == SELLABLE_PLAN
    assert item.slug == "team"
    # Factory brutto (120.0), NOT the raw 100.0.
    assert item.brutto == pytest.approx(120.0)
    assert item.currency == "EUR"
    assert block.currency == "EUR"
    # One-line description only.
    assert item.description == "For small teams."
    assert item.billing_period == "monthly"


def test_billing_period_enum_is_flattened(monkeypatch):
    class _Period:
        value = "yearly"

    plan = _FakeSellable("Yearly", "yearly", "", 50.0, [], billing_period=_Period())
    _patch_only_plans(monkeypatch, [plan])
    service = CatalogSnapshotService(lambda: None, _FakePriceFactory())
    block = service.snapshot()
    assert block.items[0].billing_period == "yearly"


def test_disabled_peer_category_is_absent(monkeypatch):
    # Simulate the shop package being absent: the products reader's guarded
    # import raises ImportError and the category contributes nothing (no crash).
    real_import = builtins.__import__

    def _fail_shop(name, *args, **kwargs):
        if name.startswith("plugins.shop"):
            raise ImportError("shop disabled (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_shop)
    service = CatalogSnapshotService(lambda: None, _FakePriceFactory())

    products = service._snapshot_products()

    assert products == []
    assert all(item.sellable_type != SELLABLE_PRODUCT for item in products)
