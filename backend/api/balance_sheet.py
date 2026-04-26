"""
Balance Sheet API -- family balance sheet CRUD + staleness report
=================================================================
From design doc: docs/design/06-family-profile.md sections 三-五

Endpoints:
  POST /api/family/balance-sheet                  Submit or update full balance sheet
  GET  /api/family/balance-sheet/{family_id}      Get balance sheet with staleness report
  GET  /api/family/balance-sheet/{family_id}/summary  Get summary (totals + staleness)
  PUT  /api/family/balance-sheet/{family_id}/{category}  Update items in a single category

Each endpoint <400 lines. Only routing + validation here; business logic in use_cases/.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from infra.store.balance_sheet_store import BalanceSheetStore
from use_cases.manage_balance_sheet import (
    get_balance_sheet,
    get_balance_sheet_summary,
    submit_balance_sheet,
    update_category_items,
)


router = APIRouter(tags=["资产负债表"])

# Singleton store instance (injected by default; tests can override)
_store = BalanceSheetStore()


# ---- Pydantic request/response schemas ----

class BalanceSheetItemSchema(BaseModel):
    """A single balance sheet line item."""
    name: str = Field(..., description="Display label, e.g. '工商银行活期'")
    value: float = Field(0.0, ge=0, description="Monetary amount (non-negative)")
    currency: str = Field("CNY", description="ISO 4217 currency code")
    last_updated: str = Field(..., description="ISO 8601 datetime of last confirmation")
    data_source: str = Field("manual", description="Data origin: manual / broker_sync / bank_import")


class BalanceSheetRequest(BaseModel):
    """Request body for full balance sheet submission."""
    family_id: str = Field(..., description="Unique family identifier")
    cash_deposits: List[BalanceSheetItemSchema] = Field(
        ..., description="Tier 1: 现金/存款/理财/余额宝", min_length=1,
    )
    investments: List[BalanceSheetItemSchema] = Field(
        ..., description="Tier 1: 股票/基金持仓", min_length=1,
    )
    real_estate: List[BalanceSheetItemSchema] = Field(
        ..., description="Tier 1: 房产", min_length=1,
    )
    liabilities: List[BalanceSheetItemSchema] = Field(
        ..., description="Tier 1: 房贷/车贷/消费贷/信用卡", min_length=1,
    )


class CategoryUpdateRequest(BaseModel):
    """Request body for updating items in a single category."""
    items: List[BalanceSheetItemSchema] = Field(
        ..., description="Replacement items for this category", min_length=1,
    )


class BalanceSheetResponse(BaseModel):
    """Response containing the balance sheet + staleness info."""
    status: str = "ok"
    balance_sheet: Dict[str, Any] = Field(default_factory=dict)
    summary: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)


# ---- Endpoints ----

@router.post("/api/family/balance-sheet", response_model=BalanceSheetResponse)
def submit_family_balance_sheet(req: BalanceSheetRequest) -> Dict[str, Any]:
    """Submit or update the family balance sheet.

    All four Tier 1 categories are required with at least one item each.
    Each item must have: name, value, last_updated, data_source.
    """
    items_data: Dict[str, List[Dict[str, Any]]] = {
        "cash_deposits": [item.model_dump() for item in req.cash_deposits],
        "investments": [item.model_dump() for item in req.investments],
        "real_estate": [item.model_dump() for item in req.real_estate],
        "liabilities": [item.model_dump() for item in req.liabilities],
    }

    sheet, errors = submit_balance_sheet(
        family_id=req.family_id,
        items_data=items_data,
        store=_store,
    )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "balance_sheet": sheet.to_dict()},
        )

    from domain.services.balance_sheet_service import compute_summary
    summary = compute_summary(sheet)

    return {
        "status": "ok",
        "balance_sheet": sheet.to_dict(),
        "summary": summary,
        "errors": [],
    }


@router.get("/api/family/balance-sheet/{family_id}", response_model=BalanceSheetResponse)
def get_family_balance_sheet(family_id: str) -> Dict[str, Any]:
    """Get the full family balance sheet with staleness annotations."""
    sheet = get_balance_sheet(family_id, _store)
    if sheet is None:
        raise HTTPException(
            status_code=404,
            detail=f"Balance sheet for family '{family_id}' not found",
        )

    from domain.services.balance_sheet_service import compute_summary
    summary = compute_summary(sheet)

    return {
        "status": "ok",
        "balance_sheet": sheet.to_dict(),
        "summary": summary,
        "errors": [],
    }


@router.get("/api/family/balance-sheet/{family_id}/summary")
def get_family_balance_sheet_summary(family_id: str) -> Dict[str, Any]:
    """Get balance sheet summary (totals + staleness report) without full item details."""
    summary = get_balance_sheet_summary(family_id, _store)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"Balance sheet for family '{family_id}' not found",
        )

    return {
        "status": "ok",
        "summary": summary,
    }


@router.put("/api/family/balance-sheet/{family_id}/{category}")
def update_balance_sheet_category(
    family_id: str,
    category: str,
    req: CategoryUpdateRequest,
) -> Dict[str, Any]:
    """Update items in a single category of the balance sheet.

    Replaces all items in the specified category while keeping other categories unchanged.
    Valid categories: cash_deposits, investments, real_estate, liabilities.
    """
    items_data = [item.model_dump() for item in req.items]

    sheet, errors = update_category_items(
        family_id=family_id,
        category=category,
        items_data=items_data,
        store=_store,
    )

    if sheet is None:
        raise HTTPException(status_code=404, detail=errors[0] if errors else "Not found")

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    from domain.services.balance_sheet_service import compute_summary
    summary = compute_summary(sheet)

    return {
        "status": "ok",
        "balance_sheet": sheet.to_dict(),
        "summary": summary,
        "errors": [],
    }
