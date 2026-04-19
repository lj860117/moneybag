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

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "stock_monitor",
    "scope": "private",
    "input": ["user_id"],
    "output": "stock_scan",
    "cost": "cpu",
    "tags": ["盯盘", "异动", "纪律", "股票"],
    "description": "股票持仓CRUD+实时行情+技术指标+异动检测+纪律检查",
    "layer": "data",
    "priority": 1,
}

from config import (
    STOCK_SINGLE_MAX, STOCK_MIN_COUNT, STOCK_INDUSTRY_MAX,
    STOCK_STOP_LOSS, STOCK_TAKE_PROFIT, STOCK_CONCENTRATION_WARN,
)

# ---- 持仓数据路径 ----
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))

# ---- 缓存 ----
_monitor_cache = {}
_MONITOR_TTL = 600  # 10 分钟


def _stock_file(user_id: str = "default") -> Path:
    """按 userId 隔离持仓文件"""
    if user_id == "default":
        return _DATA_DIR / "stock_holdings.json"  # 向后兼容
    return _DATA_DIR / f"stock_holdings_{user_id}.json"


# ============================================================
# 1. 股票持仓 CRUD（支持多用户）
# ============================================================

def load_stock_holdings(user_id: str = "default") -> list:
    """加载股票持仓列表"""
    f = _stock_file(user_id)
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_stock_holdings(holdings: list, user_id: str = "default"):
    """保存股票持仓列表"""
    f = _stock_file(user_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(holdings, ensure_ascii=False, indent=2), encoding="utf-8")


def add_stock_holding(code: str, name: str = "", cost_price: float = 0,
                      shares: int = 0, note: str = "", user_id: str = "default") -> dict:
    """添加一只持仓股票（含买入前纪律检查）"""
    holdings = load_stock_holdings(user_id)
    # 去重
    if any(h["code"] == code for h in holdings):
        return {"error": f"{code} 已在持仓中"}

    # 自动补全名称
    if not name:
        name = _get_stock_name(code)

    # 自动获取行业
    industry = _fetch_industry_safe(code)

    # ---- 买入前纪律检查 ----
    warnings = []
    if cost_price > 0 and shares > 0:
        new_value = cost_price * shares
        # 计算现有总市值（用成本估算，没有实时价就用成本价）
        total_mv = sum(
            (h.get("costPrice", 0) or 0) * (h.get("shares", 0) or 0)
            for h in holdings
        ) + new_value
        if total_mv > 0:
            new_weight = new_value / total_mv
            # 仓位上限检查
            if new_weight > STOCK_SINGLE_MAX:
                warnings.append({
                    "level": "warning", "type": "position_limit",
                    "msg": f"⚠️ {name} 占比 {new_weight*100:.1f}%，超过单只上限 {STOCK_SINGLE_MAX*100:.0f}%",
                })
            # 行业集中度检查
            if industry and industry != "未知":
                same_industry_value = sum(
                    (h.get("costPrice", 0) or 0) * (h.get("shares", 0) or 0)
                    for h in holdings if h.get("industry") == industry
                ) + new_value
                industry_weight = same_industry_value / total_mv
                if industry_weight > STOCK_INDUSTRY_MAX:
                    warnings.append({
                        "level": "warning", "type": "industry_concentration",
                        "msg": f"⚠️ {industry}行业占比将达 {industry_weight*100:.1f}%，超过 {STOCK_INDUSTRY_MAX*100:.0f}% 上限",
                    })

    # 分散度不足提醒（加上新的这只后仍不够 5 只）
    if len(holdings) + 1 < STOCK_MIN_COUNT:
        warnings.append({
            "level": "info", "type": "diversification",
            "msg": f"💡 当前仅 {len(holdings)+1} 只股票，建议至少持有 {STOCK_MIN_COUNT} 只以分散风险",
        })

    holding = {
        "code": code,
        "name": name,
        "costPrice": cost_price,
        "shares": shares,
        "note": note,
        "industry": industry,
        "addedAt": datetime.now().isoformat(),
    }
    holdings.append(holding)
    save_stock_holdings(holdings, user_id)
    return {"ok": True, "holding": holding, "warnings": warnings}


def remove_stock_holding(code: str, user_id: str = "default") -> dict:
    """删除一只持仓股票"""
    holdings = load_stock_holdings(user_id)
    before = len(holdings)
    holdings = [h for h in holdings if h["code"] != code]
    if len(holdings) == before:
        return {"error": f"{code} 不在持仓中"}
    save_stock_holdings(holdings, user_id)
    return {"ok": True}


def update_stock_holding(code: str, user_id: str = "default", **kwargs) -> dict:
    """更新持仓信息（成本价/股数/备注）"""
    holdings = load_stock_holdings(user_id)
    for h in holdings:
        if h["code"] == code:
            for k, v in kwargs.items():
                if k in ("costPrice", "shares", "note", "name"):
                    h[k] = v
            save_stock_holdings(holdings, user_id)
            return {"ok": True, "holding": h}
    return {"error": f"{code} 不在持仓中"}


# ============================================================
# 2. 单只股票实时行情
# ============================================================

def _fallback_hist_close(code: str) -> dict:
    """FIX 2026-04-19 F2: 实时数据拿不到时（非交易日/东财反爬），用最近一个交易日的日线收盘数据兜底
    
    多源降级：东方财富 → 新浪 → 返回空
    """
    import akshare as ak
    from datetime import datetime, timedelta

    # 源 1：东方财富（可能反爬）
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else last
            close = float(last.get("收盘", 0) or 0)
            prev_close = float(prev.get("收盘", 0) or 0)
            if close > 0:
                change = close - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                return {
                    "price": close,
                    "change": round(change, 2),
                    "changePct": round(change_pct, 2),
                    "volume": float(last.get("成交量", 0) or 0),
                    "amount": float(last.get("成交额", 0) or 0),
                    "high": float(last.get("最高", 0) or 0),
                    "low": float(last.get("最低", 0) or 0),
                    "open": float(last.get("开盘", 0) or 0),
                    "prevClose": prev_close,
                    "data_date": str(last.get("日期", ""))[:10],
                    "is_snapshot": True,
                }
    except Exception as e:
        print(f"[MONITOR-FALLBACK] {code} 东方财富日线失败: {e}")

    # 源 2：新浪（降级）
    try:
        sym = (f"sh{code}" if code.startswith("6") else f"sz{code}")
        df2 = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
        if df2 is not None and len(df2) >= 1:
            df2 = df2.tail(15)
            last = df2.iloc[-1]
            prev = df2.iloc[-2] if len(df2) >= 2 else last
            close_col = next((c for c in df2.columns if "close" in str(c).lower()), None)
            if close_col:
                close = float(last[close_col])
                prev_close = float(prev[close_col])
                change = close - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                date_val = last.name if hasattr(last, "name") else ""
                return {
                    "price": close,
                    "change": round(change, 2),
                    "changePct": round(change_pct, 2),
                    "prevClose": prev_close,
                    "data_date": str(date_val)[:10],
                    "is_snapshot": True,
                }
    except Exception as e:
        print(f"[MONITOR-FALLBACK] {code} 新浪日线也失败: {e}")

    return {}


def get_stock_realtime(code: str) -> dict:
    """获取单只股票实时行情（雪球数据源），非交易日/失败时降级到最近一个交易日日线"""
    cache_key = f"rt_{code}"
    now = time.time()
    if cache_key in _monitor_cache and now - _monitor_cache[cache_key]["ts"] < 30:
        return _monitor_cache[cache_key]["data"]

    result = {
        "code": code, "name": "", "price": None, "change": None,
        "changePct": None, "volume": None, "amount": None,
        "high": None, "low": None, "open": None, "prevClose": None,
        "pe": None, "pb": None, "marketCap": None, "turnover": None,
        "data_date": None, "is_snapshot": False,
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

    # FIX F2: 如果实时接口没拿到价格，降级到日线
    if result["price"] is None:
        fb = _fallback_hist_close(code)
        if fb:
            result.update({k: v for k, v in fb.items() if v is not None})
            print(f"[MONITOR] {code} 降级到日线数据 ({result.get('data_date')})")

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

def scan_all_holdings(user_id: str = "default") -> dict:
    """扫描所有持仓股票，返回实时行情 + 异动信号 + 纪律检查"""
    holdings = load_stock_holdings(user_id)
    if not holdings:
        return {"holdings": [], "signals": [], "discipline": [],
                "scannedAt": datetime.now().isoformat()}

    results = []
    all_signals = []
    discipline_alerts = []  # 纪律类信号单独收集

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
            # 止盈纪律：+20% 强提醒
            if pnl_pct >= STOCK_TAKE_PROFIT * 100:
                anomalies.append({
                    "level": "danger", "type": "take_profit",
                    "msg": f"🎯 {rt.get('name', code)} 盈利 {pnl_pct:.1f}%，"
                           f"触发止盈线({STOCK_TAKE_PROFIT*100:.0f}%)！建议立即分批卖出 50%，锁定利润",
                })
            # 止损纪律：-8% 强提醒
            elif pnl_pct <= STOCK_STOP_LOSS * 100:
                anomalies.append({
                    "level": "danger", "type": "stop_loss",
                    "msg": f"🚨 {rt.get('name', code)} 亏损 {pnl_pct:.1f}%，"
                           f"触发止损线({STOCK_STOP_LOSS*100:.0f}%)！建议立即止损卖出，不要补仓",
                })

        results.append({
            "code": code,
            "name": rt.get("name") or h.get("name", ""),
            "price": price,
            "changePct": rt.get("changePct"),
            "costPrice": cost,
            "shares": shares,
            "industry": h.get("industry", ""),
            "marketValue": round(price * shares, 2) if price and shares else 0,
            "pnl": pnl,
            "pnlPct": pnl_pct,
            "indicators": ind,
            "signals": anomalies,
            # FIX 2026-04-19 F2: 透传数据新鲜度元信息
            "is_snapshot": rt.get("is_snapshot", False),
            "data_date": rt.get("data_date"),
        })
        all_signals.extend(anomalies)

    # ---- 组合级纪律检查 ----
    total_mv = sum(r["marketValue"] for r in results)
    if total_mv > 0:
        # 单只集中度检查
        for r in results:
            weight = r["marketValue"] / total_mv if total_mv > 0 else 0
            r["weight"] = round(weight * 100, 1)  # 注入权重供前端显示
            if weight > STOCK_CONCENTRATION_WARN:
                discipline_alerts.append({
                    "level": "warning", "type": "concentration",
                    "msg": f"⚠️ {r['name']}({r['code']}) 占比 {weight*100:.1f}%，"
                           f"超过集中度警戒线 {STOCK_CONCENTRATION_WARN*100:.0f}%",
                })

        # 行业集中度检查
        industry_mv = {}
        for r in results:
            ind_name = r.get("industry", "") or "未知"
            industry_mv[ind_name] = industry_mv.get(ind_name, 0) + r["marketValue"]
        for ind_name, mv in industry_mv.items():
            if ind_name != "未知" and mv / total_mv > STOCK_INDUSTRY_MAX:
                discipline_alerts.append({
                    "level": "warning", "type": "industry_concentration",
                    "msg": f"⚠️ {ind_name}行业占比 {mv/total_mv*100:.1f}%，"
                           f"超过行业上限 {STOCK_INDUSTRY_MAX*100:.0f}%",
                })

    # 分散度检查
    if len(holdings) < STOCK_MIN_COUNT:
        discipline_alerts.append({
            "level": "info", "type": "diversification",
            "msg": f"💡 当前仅 {len(holdings)} 只股票，建议至少持有 {STOCK_MIN_COUNT} 只以分散风险",
        })

    return {
        "holdings": results,
        "signals": all_signals,
        "discipline": discipline_alerts,
        "holdingCount": len(holdings),
        "signalCount": len(all_signals),
        "disciplineCount": len(discipline_alerts),
        "totalMarketValue": round(total_mv, 2),
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


def _fetch_industry_safe(code: str) -> str:
    """安全获取个股行业（调用 holding_intelligence，失败返回空字符串）"""
    try:
        from services.holding_intelligence import get_stock_industry
        return get_stock_industry(code)
    except Exception:
        return ""
