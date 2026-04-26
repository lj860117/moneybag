"""
市场微观因子 API
=================
大宗商品、限售股解禁、ETF 资金流、综合因子。

Design doc: docs/design/12-framework-refactor.md §二
"""
from datetime import datetime
from fastapi import APIRouter

from services.market_factors import (
    get_commodity_prices, get_stock_unlock_schedule,
    get_etf_fund_flow, get_all_market_factors,
)

router = APIRouter(tags=["市场微观因子"])


@router.get("/api/market-factors/commodities")
def commodities_api():
    """大宗商品期货（黄金/铜）"""
    return get_commodity_prices()


@router.get("/api/market-factors/unlock")
def unlock_api():
    """限售股解禁计划"""
    return get_stock_unlock_schedule()


@router.get("/api/market-factors/etf-flow")
def etf_flow_api():
    """ETF 资金流向"""
    return get_etf_fund_flow()


@router.get("/api/market-factors/all")
def market_factors_all():
    """全部市场微观因子（非交易日加友好提示）"""
    result = get_all_market_factors()
    # 非交易日友好提示
    sr = result.get("sector_rotation", result) if isinstance(result, dict) else {}
    if not sr.get("available") and datetime.now().weekday() >= 5:
        if isinstance(result, dict):
            result["_weekend_note"] = "📅 非交易日，行业/大宗数据暂无更新，将在下个交易日自动恢复"
    return result
