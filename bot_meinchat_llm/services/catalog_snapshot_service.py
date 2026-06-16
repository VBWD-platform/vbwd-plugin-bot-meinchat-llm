"""CatalogSnapshotService — the live, authoritatively-priced catalog (S98.2).

Reads the four sellable repos (tariff plans, addons, shop products, bookable
resources) and prices each item through the **core** ``PriceFactory`` so the
consultant never invents a price — it renders exactly the factory's brutto +
currency. The result is a compact ``CatalogBlock`` injected into the prompt.

Every peer-plugin import is **soft-guarded** (try/except ImportError): a disabled
or absent catalog plugin simply contributes no items for that category — no hard
``from plugins.*`` crash, no failed enable (Open/Closed + the core-agnostic
"degrade when a peer is off" rule).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# Sellable-type discriminators carried on each item (used later by the buy-choice
# router in S98.4 — kept stable here).
SELLABLE_PLAN = "plan"
SELLABLE_ADDON = "addon"
SELLABLE_PRODUCT = "product"
SELLABLE_RESOURCE = "resource"

# Shop pagination upper bound for a single snapshot. The snapshot is a prompt
# grounding block, not a full catalogue export, so one generous page is enough.
PRODUCT_SNAPSHOT_PAGE_SIZE = 100


@dataclass(frozen=True)
class CatalogItem:
    """One priced sellable the consultant may recommend."""

    sellable_type: str
    name: str
    slug: str
    description: str
    brutto: float
    netto: float
    currency: str
    billing_period: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise to a compact, prompt-friendly dict."""
        return {
            "sellable_type": self.sellable_type,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "brutto": self.brutto,
            "netto": self.netto,
            "currency": self.currency,
            "billing_period": self.billing_period,
        }


@dataclass(frozen=True)
class CatalogBlock:
    """The full priced catalog snapshot for one currency / pricing mode."""

    currency: str
    items: List[CatalogItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "items": [item.to_dict() for item in self.items],
        }


class CatalogSnapshotService:
    """Build a priced ``CatalogBlock`` from the live sellable repos.

    ``session_provider`` returns the request-scoped DB session; ``price_factory``
    is the core ``container.price_factory()``. Both are injected so the service
    stays unit-testable with fakes.
    """

    def __init__(
        self,
        session_provider: Callable[[], Any],
        price_factory: Any,
    ) -> None:
        self._session_provider = session_provider
        self._price_factory = price_factory

    def snapshot(self, currency: Optional[str] = None) -> CatalogBlock:
        """List every active sellable, each priced via the core PriceFactory.

        ``currency`` is informational: the authoritative currency comes from the
        global pricing config via the factory, so it is read back from the first
        priced item (falling back to the caller's hint, then "").
        """
        items: List[CatalogItem] = []
        items.extend(self._snapshot_plans())
        items.extend(self._snapshot_addons())
        items.extend(self._snapshot_products())
        items.extend(self._snapshot_resources())

        resolved_currency = items[0].currency if items else (currency or "")
        return CatalogBlock(currency=resolved_currency, items=items)

    # ── per-category readers (each soft-guarded) ────────────────────────────
    def _snapshot_plans(self) -> List[CatalogItem]:
        try:
            from plugins.subscription.subscription.repositories.tarif_plan_repository import (  # noqa: E501
                TarifPlanRepository,
            )
        except ImportError:
            return self._skip("subscription (plans)")
        repository = TarifPlanRepository(self._session_provider())
        return [
            self._build_item(SELLABLE_PLAN, plan, self._billing_value(plan))
            for plan in repository.find_active()
        ]

    def _snapshot_addons(self) -> List[CatalogItem]:
        try:
            from plugins.subscription.subscription.repositories.addon_repository import (  # noqa: E501
                AddOnRepository,
            )
        except ImportError:
            return self._skip("subscription (addons)")
        repository = AddOnRepository(self._session_provider())
        return [
            self._build_item(SELLABLE_ADDON, addon, self._billing_value(addon))
            for addon in repository.find_active()
        ]

    def _snapshot_products(self) -> List[CatalogItem]:
        try:
            from plugins.shop.shop.repositories.product_repository import (
                ProductRepository,
            )
        except ImportError:
            return self._skip("shop (products)")
        repository = ProductRepository(self._session_provider())
        products = repository.find_active(page=1, per_page=PRODUCT_SNAPSHOT_PAGE_SIZE)
        return [
            self._build_item(SELLABLE_PRODUCT, product, None) for product in products
        ]

    def _snapshot_resources(self) -> List[CatalogItem]:
        try:
            from plugins.booking.booking.repositories.resource_repository import (
                ResourceRepository,
            )
        except ImportError:
            return self._skip("booking (resources)")
        repository = ResourceRepository(self._session_provider())
        return [
            self._build_item(SELLABLE_RESOURCE, resource, None)
            for resource in repository.find_all(active_only=True)
        ]

    # ── helpers ─────────────────────────────────────────────────────────────
    def _build_item(
        self, sellable_type: str, sellable: Any, billing_period: Optional[str]
    ) -> CatalogItem:
        price = self._price_factory.get_price_from_object(sellable)
        return CatalogItem(
            sellable_type=sellable_type,
            name=getattr(sellable, "name", ""),
            slug=getattr(sellable, "slug", ""),
            description=self._one_line(getattr(sellable, "description", None)),
            brutto=price.brutto,
            netto=price.netto,
            currency=price.currency,
            billing_period=billing_period,
        )

    @staticmethod
    def _billing_value(sellable: Any) -> Optional[str]:
        """The sellable's billing period as a plain string, if it has one.

        Plans/addons expose a ``billing_period`` (an enum on plans, a string on
        addons); products/resources have none. Normalised to ``.value`` when it
        is an enum so the prompt block carries a flat string.
        """
        period = getattr(sellable, "billing_period", None)
        if period is None:
            return None
        return getattr(period, "value", period)

    @staticmethod
    def _one_line(description: Optional[str]) -> str:
        """First non-empty line of a description, trimmed (compact for a prompt)."""
        if not description:
            return ""
        for line in description.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @staticmethod
    def _skip(category: str) -> List[CatalogItem]:
        logger.info(
            "[bot-meinchat-llm] catalog plugin for %s absent/disabled — "
            "category omitted from snapshot",
            category,
        )
        return []
