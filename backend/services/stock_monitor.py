"""
股票持仓 & 盯盘引擎 — 独立 service
职责：
  1. 股票持仓 CRUD（后端 JSON 持久化）
  2. 单只股票实时行情 + 技术指标计算
  3. 异动检测（量价/突破/资金流）
  4. 盯盘结果生成（供 cron 脚本调用）
"""
import os
import time
import json
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional

# ---- 持仓数据路径 ----
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
_STOCK_HOLDINGS_FILE = _DATA_DIR / "stock_holdings.json"

# ---- 缓存 ----
_monitor_cache = {}
_MONITOR_TTL = 600  # 10 分钟


# ============================================================
# 1. 股票持仓 CRUD
# ============================================================

def load_stock_holdings() -> list:
    """加载股票持仓列表"""
    if _STOCK_HOLDINGS_FILE.exists():
        try:
            data = json.loads(_STOCK_HOLDINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_stock_holdings(holdings: list):
    """保存股票持仓列表"""
    _STOCK_HOLDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STOCK_HOLDINGS_FILE.write_text(
        json.dumps(holdings, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def add_stock_holding(code: str, name: str = "", cost_price: float = 0,
                      shares: int = 0, note: str = "") -> dict:
    """添加一只持仓股票"""
    holdings = load_stock_holdings()
    # 去重
    if any(h["code"] == code for h in holdings):
        return {"error": f"{code} 已在持仓中"}

    # 自动补全名称
    if not name:
        name = _get_stock_name(code)

    holding = {
        "code": code,
        "name": name,
        "costPrice": cost_price,
        "shares": shares,
        "note": note,
        "addedAt": datetime.now().isoformat(),
    }
    holdings.append(holding)
    save_stock_holdings(holdings)
    return {"ok": True, "holding": holding}


def remove_stock_holding(code: str) -> dict:
    """删除一只持仓股票"""
    holdings = load_stock_holdings()
    before = len(holdings)
    holdings = [h for h in holdings if h["code"] != code]
    if len(holdings) == before:
        return {"error": f"{code} 不在持仓中"}
    save_stock_holdings(holdings)
    return {"ok": True}


def update_stock_holding(code: str, **kwargs) -> dict:
    """更新持仓信息（成本价/股数/备注）"""
    holdings = load_stock_holdings()
    for h in holdings:
        if h["code"] == code:
            for k, v in kwargs.items():
                if k in ("costPrice", "shares", "note", "name"):
                    h[k] = v
            save_stock_holdings(holdings)
            return {"ok": True, "holding": h}
    return {"error": f"{code} 不在持仓中"}


# ============================================================
# 2. 单只股票实时行情
# ============================================================

def get_stock_realtime(code: str) -> dict:
    """获取单只股票实时行情（雪球数据源）"""
    cache_key = f"rt_{code}"
    now = time.time()
    if cache_key in _monitor_cache and now - _monitor_cache[cache_key]["ts"] < 30:
        return _monitor_cache[cache_key]["data"]

    result = {
        "code": code, "name": "", "price": None, "change": None,
        "changePct": None, "volume": None, "amount": None,
        "high": None, "low": None, "open": None, "prevClose": None,
        "pe": None, "pb": None, "marketCap": None, "turnover": None,
    }
    try:
        import akshare as ak
        # 雪球单只查询，字段最全
        df = ak.stock_individual_spot_xq(symbol=code)
        if df is not None and len(df) > 0:
            def _val(col_keywords):
                for kw in col_keywords:
                    for c in df.columns:
                        if kw in str(c):
                            v = df[c].iloc[0]
                            try:
                                return float(v) if v is not None else None
                            except (ValueError, TypeError):
                                return None
                return None

            result["name"] = str(df.iloc[0].get("股票名称", "") or df.iloc[0].get("name", ""))
            result["price"] = _val(["当前价", "现价", "current"])
            result["change"] = _val(["涨跌额"])
            result["changePct"] = _val(["涨跌幅"])
            result["volume"] = _val(["成交量"])
            result["amount"] = _val(["成交额"])
            result["high"] = _val(["最高"])
            result["low"] = _val(["最低"])
            result["open"] = _val(["今开"])
            result["prevClose"] = _val(["昨收"])
            result["pe"] = _val(["市盈率", "PE"])
            result["pb"] = _val(["市净率", "PB"])
            result["marketCap"] = _val(["总市值"])
            result["turnover"] = _val(["换手率"])

    except Exception as e:
        print(f"[MONITOR] {code} realtime fail: {e}")

    _monitor_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


# ============================================================
# 3. 技术指标计算（单只股票）
# ============================================================

def calc_stock_indicators(code: str) -> dict:
    """计算单只股票的 RSI/MACD/均线/量价异动"""
    cache_key = f"ind_{code}"
    now = time.time()
    if cache_key in _monitor_cache and now - _monitor_cache[cache_key]["ts"] < _MONITOR_TTL:
        return _monitor_cache[cache_key]["data"]

    result = {
        "rsi14": None, "macd_trend": None, "ma5": None, "ma20": None,
        "volume_ratio": None, "breakthrough": None,
    }
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=(datetime.now().replace(day=1) -
                                            __import__("datetime").timedelta(days=120)).strftime("%Y%m%d"),
                                end_date=datetime.now().strftime("%Y%m%d"),
                                adjust="qfq")
        if df is None or len(df) < 30:
            _monitor_cache[cache_key] = {"data": result, "ts": now}
            return result

        closes = [float(c) for c in df["收盘"].values]
        volumes = [float(v) for v in df["成交量"].values]

        # RSI14
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(1, min(15, len(closes))):
                d = closes[-i] - closes[-i - 1]
                if d > 0:
                    gains.append(d)
                else:
                    losses.append(abs(d))
            avg_gain = sum(gains) / 14 if gains else 0.001
            avg_loss = sum(losses) / 14 if losses else 0.001
            rs = avg_gain / avg_loss
            result["rsi14"] = round(100 - 100 / (1 + rs), 1)

        # MA5 / MA20
        if len(closes) >= 5:
            result["ma5"] = round(sum(closes[-5:]) / 5, 2)
        if len(closes) >= 20:
            result["ma20"] = round(sum(closes[-20:]) / 20, 2)

        # MACD 趋势
        if len(closes) >= 26:
            ema12 = _ema(closes, 12)
            ema26 = _ema(closes, 26)
            dif = ema12 - ema26
            result["macd_trend"] = "多头" if dif > 0 else "空头"

        # 量比（今日成交量 / 5 日均量）
        if len(volumes) >= 6:
            avg_vol_5 = sum(volumes[-6:-1]) / 5
            today_vol = volumes[-1]
            if avg_vol_5 > 0:
                result["volume_ratio"] = round(today_vol / avg_vol_5, 2)

        # 突破检测（收盘价突破 20 日新高/新低）
        if len(closes) >= 21:
            high_20 = max(closes[-21:-1])
            low_20 = min(closes[-21:-1])
            if closes[-1] > high_20:
                result["breakthrough"] = "突破20日新高"
            elif closes[-1] < low_20:
                result["breakthrough"] = "跌破20日新低"

    except Exception as e:
        print(f"[MONITOR] {code} indicators fail: {e}")

    _monitor_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 4. 异动检测
# ============================================================

def detect_anomalies(code: str, realtime: dict = None, indicators: dict = None) -> list:
    """检测单只股票的异动信号"""
    if realtime is None:
        realtime = get_stock_realtime(code)
    if indicators is None:
        indicators = calc_stock_indicators(code)

    signals = []
    name = realtime.get("name", code)

    # 4.1 涨跌幅异动
    pct = realtime.get("changePct")
    if pct is not None:
        if pct >= 5:
            signals.append({
                "level": "opportunity", "type": "price_surge",
                "msg": f"📈 {name}({code}) 涨幅 {pct:.1f}%，关注是否有利好消息",
            })
        elif pct <= -5:
            signals.append({
                "level": "warning", "type": "price_drop",
                "msg": f"📉 {name}({code}) 跌幅 {pct:.1f}%，关注是否需要止损",
            })

    # 4.2 量比异动（量比 > 2 = 异常放量）
    vr = indicators.get("volume_ratio")
    if vr and vr > 2:
        signals.append({
            "level": "info", "type": "volume_surge",
            "msg": f"🔊 {name}({code}) 量比 {vr:.1f}，成交量异常放大",
        })

    # 4.3 RSI 超买超卖
    rsi = indicators.get("rsi14")
    if rsi is not None:
        if rsi > 75:
            signals.append({
                "level": "warning", "type": "rsi_overbought",
                "msg": f"⚠️ {name}({code}) RSI={rsi}，超买区域，注意回调风险",
            })
        elif rsi < 25:
            signals.append({
                "level": "opportunity", "type": "rsi_oversold",
                "msg": f"💡 {name}({code}) RSI={rsi}，超卖区域，可能存在反弹机会",
            })

    # 4.4 均线突破
    bt = indicators.get("breakthrough")
    if bt:
        lvl = "opportunity" if "新高" in bt else "warning"
        signals.append({
            "level": lvl, "type": "breakthrough",
            "msg": f"🎯 {name}({code}) {bt}",
        })

    # 4.5 盈亏提醒（需要持仓成本价）
    # 这个在 scan_all_holdings() 里处理

    return signals


# ============================================================
# 5. 全持仓扫描（供 cron 调用）
# ============================================================

def scan_all_holdings() -> dict:
    """扫描所有持仓股票，返回实时行情 + 异动信号"""
    holdings = load_stock_holdings()
    if not holdings:
        return {"holdings": [], "signals": [], "scannedAt": datetime.now().isoformat()}

    results = []
    all_signals = []

    for h in holdings:
        code = h["code"]
        rt = get_stock_realtime(code)
        ind = calc_stock_indicators(code)
        anomalies = detect_anomalies(code, rt, ind)

        # 盈亏计算
        cost = h.get("costPrice", 0)
        shares = h.get("shares", 0)
        price = rt.get("price")
        pnl = None
        pnl_pct = None
        if cost and cost > 0 and price and shares > 0:
            pnl = round((price - cost) * shares, 2)
            pnl_pct = round((price - cost) / cost * 100, 2)
            # 盈亏异动
            if pnl_pct >= 20:
                anomalies.append({
                    "level": "opportunity", "type": "profit_target",
                    "msg": f"🎯 {rt.get('name', code)} 盈利 {pnl_pct:.1f}%，考虑分批止盈",
                })
            elif pnl_pct <= -10:
                anomalies.append({
                    "level": "danger", "type": "stop_loss",
                    "msg": f"🚨 {rt.get('name', code)} 亏损 {pnl_pct:.1f}%，触及止损线",
                })

        results.append({
            "code": code,
            "name": rt.get("name") or h.get("name", ""),
            "price": price,
            "changePct": rt.get("changePct"),
            "costPrice": cost,
            "shares": shares,
            "marketValue": round(price * shares, 2) if price and shares else 0,
            "pnl": pnl,
            "pnlPct": pnl_pct,
            "indicators": ind,
            "signals": anomalies,
        })
        all_signals.extend(anomalies)

    return {
        "holdings": results,
        "signals": all_signals,
        "holdingCount": len(holdings),
        "signalCount": len(all_signals),
        "scannedAt": datetime.now().isoformat(),
    }


# ============================================================
# 辅助函数
# ============================================================

def _ema(data: list, period: int) -> float:
    """指数移动平均"""
    if len(data) < period:
        return data[-1]
    k = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for v in data[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _get_stock_name(code: str) -> str:
    """通过代码获取股票名称"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is not None:
            for _, row in df.iterrows():
                if str(row.iloc[0]) == code:
                    return str(row.iloc[1])
    except Exception:
        pass
    return code
