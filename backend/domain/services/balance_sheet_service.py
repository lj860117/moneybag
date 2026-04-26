"""
Balance Sheet Service -- staleness detection, validation, aggregation
=====================================================================
Pure domain logic for the family balance sheet MVP (M2 W3).

Responsibilities:
  - Build a BalanceSheet from raw item data
  - Validate Tier 1 required categories (cash_deposits, investments, real_estate, liabilities)
  - Detect stale items (last_updated > 30 days)
  - Compute aggregates (total_assets, total_liabilities, net_worth)
  - Generate staleness reports for UI and rule engine

Invariant #9: No cross-imports between domain services.
             This module does NOT import family_profile_service or user_preference_service.
Invariant #10: domain/ does not import from infra/. Persistence is handled
              by use_cases/ via BalanceSheetProtocol injection.

Design doc: docs/design/06-family-profile.md sections 三-五
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from domain.models.balance_sheet import (
    BalanceSheet,
    BalanceSheetItem,
    STALE_THRESHOLD_DAYS,
    VALID_CATEGORIES,
)


# ============================================================
# Build balance sheet from raw data
# ============================================================

def build_balance_sheet(
    family_id: str,
    items_data: Dict[str, List[Dict[str, Any]]],
    existing: BalanceSheet | None = None,
) -> BalanceSheet:
    """Construct a BalanceSheet from raw item data per category.

    Args:
        family_id: unique family identifier
        items_data: dict mapping category name to list of item dicts
                    e.g. {"cash_deposits": [{"name": "...", "value": 100000, ...}]}
        existing: if updating, merge timestamp from existing

    Returns:
        A new frozen BalanceSheet instance.
    """
    now = datetime.now().isoformat()

    def _parse_items(raw: List[Dict[str, Any]], category: str) -> Tuple[BalanceSheetItem, ...]:
        items: List[BalanceSheetItem] = []
        for item_dict in raw:
            # Ensure category is set correctly
            item_dict = {**item_dict, "category": category}
            items.append(BalanceSheetItem.from_dict(item_dict))
        return tuple(items)

    return BalanceSheet(
        family_id=family_id,
        cash_deposits=_parse_items(items_data.get("cash_deposits", []), "cash_deposits"),
        investments=_parse_items(items_data.get("investments", []), "investments"),
        real_estate=_parse_items(items_data.get("real_estate", []), "real_estate"),
        liabilities=_parse_items(items_data.get("liabilities", []), "liabilities"),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )


# ============================================================
# Validation
# ============================================================

def validate_balance_sheet(sheet: BalanceSheet) -> List[str]:
    """Validate a BalanceSheet for completeness and field correctness.

    Tier 1 MVP validation rules:
      1. family_id is required
      2. All four Tier 1 categories must have at least one item
      3. All items must have a non-empty name
      4. All items must have value >= 0
      5. All items must have a valid category
      6. All items must have a non-empty last_updated

    Returns:
        List of error messages. Empty list = valid.
    """
    errors: List[str] = []

    # Required: family_id
    if not sheet.family_id:
        errors.append("family_id is required")

    # Tier 1: all four categories must have at least one item
    for cat in VALID_CATEGORIES:
        items = sheet._items_for_category(cat)
        if not items:
            errors.append(f"Tier 1 category '{cat}' requires at least one item")

    # Per-item validation
    seen_names: Dict[str, int] = {}
    for item in sheet.all_items:
        # Name required
        if not item.name:
            errors.append(f"Item in '{item.category}' has empty name")

        # Non-negative value
        if item.value < 0:
            errors.append(f"Item '{item.name}' has negative value: {item.value}")

        # Valid category
        if item.category not in VALID_CATEGORIES:
            errors.append(
                f"Item '{item.name}' has invalid category '{item.category}', "
                f"must be one of {VALID_CATEGORIES}"
            )

        # last_updated required
        if not item.last_updated:
            errors.append(f"Item '{item.name}' has empty last_updated")

        # Duplicate name check within same category
        key = f"{item.category}:{item.name}"
        seen_names[key] = seen_names.get(key, 0) + 1
        if seen_names[key] > 1:
            errors.append(f"Duplicate item name '{item.name}' in category '{item.category}'")

    return errors


# ============================================================
# Staleness detection helpers
# ============================================================

def detect_stale_items(
    sheet: BalanceSheet,
    now: datetime | None = None,
    threshold_days: int = STALE_THRESHOLD_DAYS,
) -> List[Dict[str, object]]:
    """Detect all stale items and return a report list.

    Each entry contains the item details plus staleness metadata.
    Used by the rule engine to decide which rules to skip,
    and by the API to annotate the response.

    Design doc: 06-family-profile.md section 5.1-5.2
    Rule: stale data -> skip related rules, never guess or fill.

    Returns:
        List of dicts with item info + days_since_update.
    """
    if now is None:
        now = datetime.now()

    stale_report: List[Dict[str, object]] = []
    for item in sheet.all_items:
        if item.is_stale(now, threshold_days):
            days_since: int | None = None
            if item.last_updated:
                try:
                    updated_at = datetime.fromisoformat(item.last_updated)
                    days_since = (now - updated_at).days
                except (ValueError, TypeError):
                    pass

            stale_report.append({
                "name": item.name,
                "category": item.category,
                "last_updated": item.last_updated,
                "days_since_update": days_since,
                "threshold_days": threshold_days,
                "message": _stale_message(item.name, item.category, days_since),
            })

    return stale_report


def _stale_message(name: str, category: str, days_since: int | None) -> str:
    """Generate a user-facing staleness message.

    Design doc: 06-family-profile.md section 5.2
    """
    if days_since is None:
        return f"'{name}' 数据缺少更新时间，请更新"
    return f"'{name}' 已 {days_since} 天未更新，请确认最新数据"


# ============================================================
# Aggregation helpers (for API response enrichment)
# ============================================================

def compute_summary(
    sheet: BalanceSheet,
    now: datetime | None = None,
    threshold_days: int = STALE_THRESHOLD_DAYS,
) -> Dict[str, object]:
    """Compute a summary of the balance sheet for API response.

    Includes:
      - total_assets, total_liabilities, net_worth
      - per-category totals
      - staleness report
      - overall health status

    Returns:
        Summary dict suitable for JSON response.
    """
    if now is None:
        now = datetime.now()

    stale = detect_stale_items(sheet, now, threshold_days)
    staleness_report = sheet.staleness_report(now, threshold_days)

    return {
        "family_id": sheet.family_id,
        "total_assets": sheet.total_assets,
        "total_liabilities": sheet.total_liabilities,
        "net_worth": sheet.net_worth,
        "category_totals": {
            "cash_deposits": sum(item.value for item in sheet.cash_deposits),
            "investments": sum(item.value for item in sheet.investments),
            "real_estate": sum(item.value for item in sheet.real_estate),
            "liabilities": sum(item.value for item in sheet.liabilities),
        },
        "item_count": len(sheet.all_items),
        "stale_count": len(stale),
        "stale_items": stale,
        "staleness_report": staleness_report,
        "has_stale_data": len(stale) > 0,
        "updated_at": sheet.updated_at,
    }
