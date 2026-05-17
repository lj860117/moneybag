"""TushareChart -- 日线 K 线 + RSI | 不变式 #5: 数据走 infra/data_source"""
from __future__ import annotations
import logging, os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)
_PERIOD_DAYS = {"3m": 90, "6m": 180, "1y": 365, "3y": 1095}


@dataclass
class KLinePoint:
    date: str; open: float; high: float; low: float; close: float


@dataclass
class VolumePoint:
    date: str; volume: int


def resolve_date_range(period: str, end: Optional[date] = None) -> tuple[str, str]:
    end_d = end or date.today()
    start_d = end_d - timedelta(days=_PERIOD_DAYS.get(period, 365))
    return start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")


def _get_api() -> Any:
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    ts.set_token(token)
    return ts.pro_api()


def _fund_code_to_ts_code(code: str) -> str:
    if "." in code: return code
    if code.startswith("5"): return f"{code}.SH"
    if code.startswith("1"): return f"{code}.SZ"
    if code.startswith(("6", "9")): return f"{code}.SH"
    return f"{code}.OF"


def _fmt(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def fetch_daily_kline(ts_code: str, start_date: str, end_date: str) -> list[KLinePoint]:
    try:
        # 场外基金(.OF)用 fund_nav 接口获取净值走势
        if ts_code.endswith(".OF"):
            return _fetch_fund_nav_kline(ts_code, start_date, end_date)
        df = _get_api().daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty: return []
        df = df.sort_values("trade_date")
        return [KLinePoint(_fmt(r["trade_date"]), float(r["open"]), float(r["high"]),
                float(r["low"]), float(r["close"])) for _, r in df.iterrows()]
    except Exception as e:
        logger.warning("fetch_daily_kline(%s): %s", ts_code, e)
        return []


def _fetch_fund_nav_kline(ts_code: str, start_date: str, end_date: str) -> list[KLinePoint]:
    """场外基金用 fund_nav 接口获取净值，转为简化K线（open=close=nav）"""
    try:
        df = _get_api().fund_nav(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty: return []
        df = df.sort_values("ann_date")
        result = []
        for _, r in df.iterrows():
            nav = float(r.get("unit_nav") or r.get("adj_nav") or 0)
            if nav <= 0:
                continue
            d = _fmt(str(r.get("ann_date", r.get("end_date", ""))))
            # 场外基金无盘中数据，OHLC 都用净值
            result.append(KLinePoint(d, nav, nav, nav, nav))
        return result
    except Exception as e:
        logger.warning("_fetch_fund_nav_kline(%s): %s", ts_code, e)
        return []


def fetch_daily_volume(ts_code: str, start_date: str, end_date: str) -> list[VolumePoint]:
    try:
        # 场外基金无成交量数据
        if ts_code.endswith(".OF"):
            return []
        df = _get_api().daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty: return []
        df = df.sort_values("trade_date")
        return [VolumePoint(_fmt(r["trade_date"]), int(r["vol"])) for _, r in df.iterrows()]
    except Exception as e:
        logger.warning("fetch_daily_volume(%s): %s", ts_code, e)
        return []


def calculate_rsi(kline_data: list[KLinePoint], period: int = 14) -> list[dict]:
    """Wilder 平滑法 RSI"""
    if len(kline_data) < period + 1: return []
    closes = [p.close for p in kline_data]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_g = sum(max(d, 0) for d in deltas[:period]) / period
    avg_l = sum(abs(min(d, 0)) for d in deltas[:period]) / period
    def _rsi() -> float: return 100.0 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 2)
    result = [{"date": kline_data[period].date, "rsi": _rsi()}]
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + max(deltas[i], 0)) / period
        avg_l = (avg_l * (period - 1) + abs(min(deltas[i], 0))) / period
        result.append({"date": kline_data[i + 1].date, "rsi": _rsi()})
    return result
