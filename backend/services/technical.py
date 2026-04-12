"""
钱袋子 — 技术指标计算
RSI、MACD、布林带
"""
import time
from services.market_data import _nav_cache
from config import NAV_CACHE_TTL


def calc_rsi(prices: list, period: int = 14) -> float:
    """计算 RSI 指标"""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_macd(prices: list) -> dict:
    """计算 MACD 指标（12/26/9）"""
    if len(prices) < 35:
        return {"macd": 0, "signal": 0, "histogram": 0, "trend": "数据不足"}

    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ema12 = ema(prices, 12)
    ema26 = ema(prices, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(prices))]
    dea = ema(dif, 9)
    macd_val = dif[-1] - dea[-1]

    if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
        trend = "金叉（买入信号）"
    elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
        trend = "死叉（卖出信号）"
    elif dif[-1] > dea[-1]:
        trend = "多头排列"
    else:
        trend = "空头排列"

    return {
        "macd": round(macd_val, 4),
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "trend": trend,
    }


def calc_bollinger(prices: list, period: int = 20) -> dict:
    """计算布林带"""
    if len(prices) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "position": "数据不足"}
    recent = prices[-period:]
    middle = sum(recent) / period
    std = (sum((p - middle) ** 2 for p in recent) / period) ** 0.5
    upper = middle + 2 * std
    lower = middle - 2 * std
    current = prices[-1]

    if current > upper:
        position = "超买（高于上轨）"
    elif current < lower:
        position = "超卖（低于下轨）"
    elif current > middle:
        position = "中轨上方（偏强）"
    else:
        position = "中轨下方（偏弱）"

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "current": round(current, 2),
        "position": position,
    }


def get_technical_indicators(symbol: str = "sh000300") -> dict:
    """获取沪深300的技术指标（RSI/MACD/布林带）"""
    cache_key = f"tech_{symbol}"
    now = time.time()
    if cache_key in _nav_cache and now - _nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        return _nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is not None and len(df) >= 60:
            closes = [float(c) for c in df.tail(120)["close"].values]
            result = {
                "rsi": calc_rsi(closes),
                "macd": calc_macd(closes),
                "bollinger": calc_bollinger(closes),
                "rsi_signal": "超买" if calc_rsi(closes) > 70 else "超卖" if calc_rsi(closes) < 30 else "中性",
            }
            _nav_cache[cache_key] = {"data": result, "ts": now}
            return result
    except Exception as e:
        print(f"[TECH] Failed: {e}")
    return {"rsi": 50, "macd": {"macd": 0, "dif": 0, "dea": 0, "trend": "数据不足"}, "bollinger": {"upper": 0, "middle": 0, "lower": 0, "position": "数据不足"}, "rsi_signal": "数据不足"}


# ---- 新闻资讯 ----

