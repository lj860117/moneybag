"""迷你行情 API -- TradingView 数据端点 | 设计: 08-batch-m9-intervention.md"""
# DONE: 前端集成 — pages/chart.js 弹窗模式 + 基金卡片📊按钮入口
# TODO: 接入 Batch 5/6 行为偏差标记 — 当 include_behavior_marks=True 时
#       从 behavior_detector 读取该标的的历史偏差标记
from __future__ import annotations
from dataclasses import asdict
from typing import Optional
from fastapi import APIRouter
from infra.data_source.providers.tushare_chart import (
    fetch_daily_kline, fetch_daily_volume, calculate_rsi,
    resolve_date_range, _fund_code_to_ts_code,
)

router = APIRouter(tags=["迷你行情"])


@router.get("/api/chart/{fund_code}")
async def get_chart_data(
    fund_code: str, period: str = "1y", include_cost_line: bool = True,
    include_indicators: bool = True, include_behavior_marks: bool = False,
    userId: str = "default",
):
    """迷你行情数据（K线+成交量+指标）— 不输出投资建议"""
    ts_code = _fund_code_to_ts_code(fund_code)
    start, end = resolve_date_range(period)
    kline = fetch_daily_kline(ts_code, start, end)
    volume = fetch_daily_volume(ts_code, start, end)
    cost = _get_cost(fund_code, userId) if include_cost_line else None
    ind = {"rsi_14": calculate_rsi(kline), "pe_percentile": None} if include_indicators and kline else None
    return {
        "fund_code": fund_code, "fund_name": _get_name(fund_code, userId),
        "period": period, "kline_data": [asdict(p) for p in kline],
        "volume_data": [asdict(p) for p in volume],
        "cost_line": cost, "indicators": ind, "behavior_marks": None,
    }


def _get_cost(code: str, uid: str) -> Optional[float]:
    # 先查基金持仓
    try:
        from services.fund_monitor import load_fund_holdings
        for h in load_fund_holdings(uid):
            if h.get("code") == code and h.get("costNav"):
                return float(h["costNav"])
    except Exception:
        pass
    # 再查股票持仓
    try:
        from services.stock_monitor import load_stock_holdings
        for h in load_stock_holdings(uid):
            if h.get("code") == code and h.get("costPrice"):
                return float(h["costPrice"])
    except Exception:
        pass
    return None


def _get_name(code: str, uid: str) -> str:
    try:
        from services.fund_monitor import load_fund_holdings
        for h in load_fund_holdings(uid):
            if h.get("code") == code: return h.get("name", code)
    except Exception:
        pass
    try:
        from services.stock_monitor import load_stock_holdings
        for h in load_stock_holdings(uid):
            if h.get("code") == code: return h.get("name", code)
    except Exception:
        pass
    return code
