"""
Balance Sheet Domain Models
============================
Business value objects for the family balance sheet MVP (M2 W3).

Contents:
  - BalanceSheetItem: a single asset/liability line item with value, currency,
    last_updated, data_source, and staleness detection
  - BalanceSheet: the complete family balance sheet containing Tier 1 categories:
    cash_deposits, investments, real_estate, liabilities

All models are frozen dataclasses (immutable facts).
Downstream consumers: rule_engine (03), decision_guard (07), scheduling (05), ai_interface (04).

Design doc: docs/design/06-family-profile.md sections 三-五
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

# Default staleness threshold (days) per doc 06 section 5.1
# MVP uses 30 days for all Tier 1 items; per-category thresholds
# (90d cash, 60d mortgage, 365d insurance) are Tier 2 scope.
STALE_THRESHOLD_DAYS = 30


# ============================================================
# BalanceSheetItem
# ============================================================

@dataclass(frozen=True)
class BalanceSheetItem:
    """A single line item on the balance sheet.

    Every asset or liability entry carries:
      - value: monetary amount (always >= 0; sign is determined by category)
      - currency: ISO 4217 code (default "CNY")
      - last_updated: ISO 8601 timestamp of when this value was last confirmed
      - data_source: origin of the data ("manual", "broker_sync", "bank_import", ...)

    Staleness is computed, not stored -- see is_stale().

    Design doc: docs/design/06-family-profile.md section 四 (Tier 1)
    """

    name: str                          # display label, e.g. "工商银行活期"
    category: str                      # "cash_deposits" | "investments" | "real_estate" | "liabilities"
    value: float = 0.0                 # monetary amount (non-negative)
    currency: str = "CNY"              # ISO 4217
    last_updated: str = ""             # ISO 8601 datetime string
    data_source: str = "manual"        # "manual" | "broker_sync" | "bank_import" | ...

    def is_stale(self, now: datetime | None = None, threshold_days: int = STALE_THRESHOLD_DAYS) -> bool:
        """Check if this item's data is stale (last_updated older than threshold).

        Design doc: 06-family-profile.md section 5.1 -- field staleness thresholds.
        Rule engine principle: stale data -> skip related rules, never guess or fill.

        Args:
            now: reference time (defaults to datetime.now())
            threshold_days: number of days after which data is considered stale

        Returns:
            True if stale or if last_updated is empty/unparseable.
        """
        if not self.last_updated:
            return True
        if now is None:
            now = datetime.now()
        try:
            updated_at = datetime.fromisoformat(self.last_updated)
            return (now - updated_at) > timedelta(days=threshold_days)
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "category": self.category,
            "value": self.value,
            "currency": self.currency,
            "last_updated": self.last_updated,
            "data_source": self.data_source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BalanceSheetItem":
        return cls(
            name=d.get("name", ""),
            category=d.get("category", ""),
            value=float(d.get("value", 0.0)),
            currency=d.get("currency", "CNY"),
            last_updated=d.get("last_updated", ""),
            data_source=d.get("data_source", "manual"),
        )


# ============================================================
# Valid categories (Tier 1 MVP)
# ============================================================

VALID_CATEGORIES = ("cash_deposits", "investments", "real_estate", "liabilities")

# Human-readable labels for each Tier 1 category
CATEGORY_LABELS: Dict[str, str] = {
    "cash_deposits": "现金/存款",
    "investments": "投资资产",
    "real_estate": "房产",
    "liabilities": "负债",
}


# ============================================================
# BalanceSheet
# ============================================================

@dataclass(frozen=True)
class BalanceSheet:
    """Complete family balance sheet -- Tier 1 MVP (M2 W3).

    Tier 1 categories (06-family-profile.md section 4.1):
      - cash_deposits: 现金/理财/余额宝等 (monthly update, very low friction)
      - investments: 股票/基金持仓 (manual or broker import)
      - real_estate: 房产 (acquisition price, low update frequency)
      - liabilities: 房贷/车贷/消费贷/信用卡 (monthly from bank app)

    Each category holds a tuple of BalanceSheetItem instances.
    Items are tuples (not lists) because BalanceSheet is frozen.

    The balance sheet is linked to a family_id and is independent of
    FamilyProfile -- they share family_id as the join key.

    Staleness rules (section 5.1):
      - MVP: any item with last_updated > 30 days is marked stale
      - Rule engine must skip related judgments for stale items
      - UI must show stale items in grey with "update now" prompt

    frozen=True: balance sheets are immutable snapshots.
    """

    family_id: str

    # Tier 1 categories -- tuples of BalanceSheetItem
    cash_deposits: Tuple[BalanceSheetItem, ...] = ()
    investments: Tuple[BalanceSheetItem, ...] = ()
    real_estate: Tuple[BalanceSheetItem, ...] = ()
    liabilities: Tuple[BalanceSheetItem, ...] = ()

    # Metadata
    created_at: str = ""
    updated_at: str = ""

    # ---- Computed properties ----

    @property
    def total_assets(self) -> float:
        """Sum of all asset categories (cash + investments + real_estate)."""
        return (
            sum(item.value for item in self.cash_deposits)
            + sum(item.value for item in self.investments)
            + sum(item.value for item in self.real_estate)
        )

    @property
    def total_liabilities(self) -> float:
        """Sum of all liabilities."""
        return sum(item.value for item in self.liabilities)

    @property
    def net_worth(self) -> float:
        """Net worth = total_assets - total_liabilities."""
        return self.total_assets - self.total_liabilities

    @property
    def all_items(self) -> Tuple[BalanceSheetItem, ...]:
        """Flat tuple of all items across all categories."""
        return self.cash_deposits + self.investments + self.real_estate + self.liabilities

    def stale_items(self, now: datetime | None = None, threshold_days: int = STALE_THRESHOLD_DAYS) -> Tuple[BalanceSheetItem, ...]:
        """Return all items that are stale (last_updated > threshold_days).

        Rule engine should skip judgments that depend on stale items.
        UI should render stale items in grey with update prompt.
        """
        return tuple(item for item in self.all_items if item.is_stale(now, threshold_days))

    def fresh_items(self, now: datetime | None = None, threshold_days: int = STALE_THRESHOLD_DAYS) -> Tuple[BalanceSheetItem, ...]:
        """Return all items that are NOT stale."""
        return tuple(item for item in self.all_items if not item.is_stale(now, threshold_days))

    def has_stale_category(self, category: str, now: datetime | None = None, threshold_days: int = STALE_THRESHOLD_DAYS) -> bool:
        """Check if ANY item in a given category is stale.

        Used by rule engine to decide whether to skip category-specific rules.
        """
        items = self._items_for_category(category)
        if not items:
            return True  # empty category = effectively stale (no data)
        return any(item.is_stale(now, threshold_days) for item in items)

    def staleness_report(self, now: datetime | None = None, threshold_days: int = STALE_THRESHOLD_DAYS) -> Dict[str, object]:
        """Generate a staleness report for all categories.

        Returns a dict with per-category staleness status, suitable for
        API response and UI rendering.

        Design doc: 06-family-profile.md section 5.2
        """
        report: Dict[str, object] = {}
        for cat in VALID_CATEGORIES:
            items = self._items_for_category(cat)
            stale = [item.name for item in items if item.is_stale(now, threshold_days)]
            fresh = [item.name for item in items if not item.is_stale(now, threshold_days)]
            report[cat] = {
                "has_data": len(items) > 0,
                "total_items": len(items),
                "stale_count": len(stale),
                "fresh_count": len(fresh),
                "stale_items": stale,
                "is_category_stale": len(items) == 0 or len(stale) > 0,
            }
        return report

    def _items_for_category(self, category: str) -> Tuple[BalanceSheetItem, ...]:
        """Get items for a given category name."""
        mapping = {
            "cash_deposits": self.cash_deposits,
            "investments": self.investments,
            "real_estate": self.real_estate,
            "liabilities": self.liabilities,
        }
        return mapping.get(category, ())

    # ---- Serialization ----

    def to_dict(self) -> Dict[str, object]:
        """Serialize to JSON-compatible dict for persistence."""
        return {
            "family_id": self.family_id,
            "cash_deposits": [item.to_dict() for item in self.cash_deposits],
            "investments": [item.to_dict() for item in self.investments],
            "real_estate": [item.to_dict() for item in self.real_estate],
            "liabilities": [item.to_dict() for item in self.liabilities],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BalanceSheet":
        """Construct from persisted dict. Ignores unknown keys, fills defaults."""
        def _parse_items(raw: Any) -> Tuple[BalanceSheetItem, ...]:
            if not isinstance(raw, list):
                return ()
            return tuple(BalanceSheetItem.from_dict(item) for item in raw)

        return cls(
            family_id=d.get("family_id", ""),
            cash_deposits=_parse_items(d.get("cash_deposits", [])),
            investments=_parse_items(d.get("investments", [])),
            real_estate=_parse_items(d.get("real_estate", [])),
            liabilities=_parse_items(d.get("liabilities", [])),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
