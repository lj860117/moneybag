"""
Manage Balance Sheet Use Case
==============================
Orchestrates domain service (balance_sheet_service) and infra (BalanceSheetProtocol)
to process balance sheet submissions and queries.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/06-family-profile.md sections 三-五
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from domain.models.balance_sheet import BalanceSheet
from domain.protocols.balance_sheet import BalanceSheetProtocol
from domain.services.balance_sheet_service import (
    build_balance_sheet,
    compute_summary,
    validate_balance_sheet,
)


def submit_balance_sheet(
    family_id: str,
    items_data: Dict[str, List[Dict[str, Any]]],
    store: BalanceSheetProtocol,
) -> Tuple[BalanceSheet, List[str]]:
    """Submit or update a family balance sheet.

    Steps:
      1. Load existing balance sheet (if updating)
      2. Build BalanceSheet from items data (merging timestamps)
      3. Validate the balance sheet
      4. If valid, persist via store
      5. Return (balance_sheet, errors)

    Args:
        family_id: unique family identifier
        items_data: dict mapping category name to list of item dicts
        store: injected persistence (BalanceSheetProtocol)

    Returns:
        Tuple of (BalanceSheet, error_list).
        If error_list is non-empty, the balance sheet was NOT saved.
    """
    # 1. Load existing for merge (preserve created_at)
    existing: BalanceSheet | None = None
    existing_data = store.load(family_id)
    if existing_data is not None:
        existing = BalanceSheet.from_dict(existing_data)

    # 2. Build from items data
    sheet = build_balance_sheet(family_id, items_data, existing)

    # 3. Validate
    errors = validate_balance_sheet(sheet)
    if errors:
        return sheet, errors

    # 4. Persist
    store.save(family_id, sheet.to_dict())

    return sheet, []


def get_balance_sheet(
    family_id: str,
    store: BalanceSheetProtocol,
) -> BalanceSheet | None:
    """Load an existing balance sheet.

    Returns None if not found.
    """
    data = store.load(family_id)
    if data is None:
        return None
    return BalanceSheet.from_dict(data)


def get_balance_sheet_summary(
    family_id: str,
    store: BalanceSheetProtocol,
) -> Dict[str, object] | None:
    """Load a balance sheet and compute its summary (with staleness report).

    Returns None if not found. Otherwise returns a summary dict
    including totals, staleness report, and health status.
    """
    sheet = get_balance_sheet(family_id, store)
    if sheet is None:
        return None
    return compute_summary(sheet)


def update_category_items(
    family_id: str,
    category: str,
    items_data: List[Dict[str, Any]],
    store: BalanceSheetProtocol,
) -> Tuple[BalanceSheet | None, List[str]]:
    """Update items in a single category of an existing balance sheet.

    Replaces all items in the specified category while keeping other categories unchanged.

    Returns (updated_sheet, errors). If family not found, returns (None, [error]).
    """
    data = store.load(family_id)
    if data is None:
        return None, [f"Balance sheet for family '{family_id}' not found"]

    existing = BalanceSheet.from_dict(data)

    # Build new items_data dict by merging existing + updated category
    merged: Dict[str, List[Dict[str, Any]]] = {
        "cash_deposits": [item.to_dict() for item in existing.cash_deposits],
        "investments": [item.to_dict() for item in existing.investments],
        "real_estate": [item.to_dict() for item in existing.real_estate],
        "liabilities": [item.to_dict() for item in existing.liabilities],
    }

    from domain.models.balance_sheet import VALID_CATEGORIES
    if category not in VALID_CATEGORIES:
        return None, [f"Invalid category '{category}', must be one of {VALID_CATEGORIES}"]

    merged[category] = items_data

    # Rebuild and validate
    sheet = build_balance_sheet(family_id, merged, existing)
    errors = validate_balance_sheet(sheet)
    if errors:
        return sheet, errors

    store.save(family_id, sheet.to_dict())
    return sheet, []
