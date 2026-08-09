"""迷你行情 API -- TradingView 数据端点 | 设计: 08-batch-m9-intervention.md"""
# DONE: 前端集成 — pages/chart.js 弹窗模式 + 基金卡片📊按钮入口
# TODO: 接入 Batch 5/6 行为偏差标记 — 当 include_behavior_marks=True 时
#       从 behavior_detector 读取该标的的历史偏差标记
from __future__ import annotations
from dataclasses import asdict
import json
import os
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from infra.data_source.providers.tushare_chart import (
    fetch_daily_kline, fetch_daily_volume, calculate_rsi,
    resolve_date_range, _fund_code_to_ts_code,
)

router = APIRouter(tags=["迷你行情"])

_CHART_CACHE_DIR = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "chart_api"
_CHART_CACHE_TTL = 36000  # 10h，早晚预热后日内秒回
_CHART_CACHE_STALE = 259200  # 72h，接口异常时先回旧值


def _chart_cache_key(fund_code: str, period: str, user_id: str, include_cost_line: bool, include_indicators: bool) -> str:
    return f"chart_{fund_code}_{period}_{user_id or 'default'}_{int(include_cost_line)}_{int(include_indicators)}"



def _get_chart_cached(cache_key: str, allow_stale: bool = False) -> Optional[dict]:
    try:
        path = _CHART_CACHE_DIR / f"{cache_key}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - payload.get("t", 0)
        if age < _CHART_CACHE_TTL:
            return payload.get("v")
        if allow_stale and age < _CHART_CACHE_STALE:
            return payload.get("v")
    except Exception:
        pass
    return None



def _set_chart_cached(cache_key: str, value: dict):
    try:
        _CHART_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CHART_CACHE_DIR / f"{cache_key}.json"
        path.write_text(json.dumps({"v": value, "t": time.time()}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@router.get("/api/chart/{fund_code}")
async def get_chart_data(
    fund_code: str, period: str = "1y", include_cost_line: bool = True,
    include_indicators: bool = True, include_behavior_marks: bool = False,
    userId: str = "default",
):
    """迷你行情数据（K线+成交量+指标）— 不输出投资建议"""
    cache_key = _chart_cache_key(fund_code, period, userId, include_cost_line, include_indicators)
    cached = _get_chart_cached(cache_key, allow_stale=True)
    if cached:
        return {**cached, "from_cache": True}

    ts_code = _fund_code_to_ts_code(fund_code)
    start, end = resolve_date_range(period)
    kline = fetch_daily_kline(ts_code, start, end)
    volume = fetch_daily_volume(ts_code, start, end)
    cost = _get_cost(fund_code, userId) if include_cost_line else None
    ind = {"rsi_14": calculate_rsi(kline), "pe_percentile": None} if include_indicators and kline else None
    result = {
        "fund_code": fund_code, "fund_name": _get_name(fund_code, userId),
        "period": period, "kline_data": [asdict(p) for p in kline],
        "volume_data": [asdict(p) for p in volume],
        "cost_line": cost, "indicators": ind, "behavior_marks": None,
        "from_cache": False,
    }
    _set_chart_cached(cache_key, result)
    return result



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
            if h.get("code") == code:
                return h.get("name", code)
    except Exception:
        pass
    try:
        from services.stock_monitor import load_stock_holdings
        for h in load_stock_holdings(uid):
            if h.get("code") == code:
                return h.get("name", code)
    except Exception:
        pass
    return code
