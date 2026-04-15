"""
钱袋子 — 全球市场数据层
职责：
  1. 美股指数（道琼斯/标普/纳斯达克）历史+最新
  2. 外汇（美元/人民币、美元指数）
  3. 美联储利率（联邦基金利率历史）
  4. 全球市场估值（美国 PE）
  5. 国际商品（黄金/原油国际价）
  6. 全球→A股影响分析（DeepSeek 联动）

数据源：AKShare（腾讯云实测可用的接口）
缓存：1 小时（全球数据更新频率低于 A 股）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "global_market",
    "scope": "public",
    "input": [],
    "output": "global_snapshot",
    "cost": "cpu",
    "tags": ['全球', '美股', '外汇', '美联储'],
    "description": "全球市场：美股三大指数+外汇+美联储利率+全球PE+影响分析",
    "layer": "data",
    "priority": 2,
}
import os
import time
import json
import math
import traceback
from datetime import datetime, timedelta

_global_cache = {}
_GLOBAL_TTL = 3600  # 1 小时缓存


def _safe_num(v, default=0):
    """安全转数字，处理 NaN/Inf"""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


# ============================================================
# 1. 美股指数
# ============================================================

def get_us_indices() -> dict:
    """获取美股三大指数最新数据（道琼斯/标普500/纳斯达克）"""
    cache_key = "us_indices"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < _GLOBAL_TTL:
        return _global_cache[cache_key]["data"]

    result = {"dji": None, "spx": None, "ixic": None, "available": False}

    try:
        import akshare as ak

        indices = {
            "dji": ".DJI",   # 道琼斯
            "spx": ".INX",   # 标普500
            "ixic": ".IXIC", # 纳斯达克
        }

        for key, symbol in indices.items():
            try:
                df = ak.index_us_stock_sina(symbol=symbol)
                if df is not None and len(df) > 0:
                    last = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else last
                    close = _safe_num(last.iloc[1]) if len(last) > 1 else 0
                    prev_close = _safe_num(prev.iloc[1]) if len(prev) > 1 else close
                    change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    result[key] = {
                        "close": round(close, 2),
                        "change_pct": round(change_pct, 2),
                        "date": str(last.iloc[0]) if len(last) > 0 else "",
                        "trend": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
                    }
            except Exception as e:
                print(f"[GLOBAL] {key} failed: {e}")

        result["available"] = any(result[k] is not None for k in ["dji", "spx", "ixic"])
    except Exception as e:
        print(f"[GLOBAL] US indices failed: {e}")

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 2. 外汇数据
# ============================================================

def get_forex_data() -> dict:
    """获取主要外汇汇率（美元/人民币、欧元等）"""
    cache_key = "forex"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < _GLOBAL_TTL:
        return _global_cache[cache_key]["data"]

    result = {"usdcny": None, "dxy_proxy": None, "available": False}

    try:
        import akshare as ak
        df = ak.fx_spot_quote()
        if df is not None and len(df) > 0:
            # 找美元/人民币
            for _, row in df.iterrows():
                name = str(row.iloc[0]) if len(row) > 0 else ""
                if "美元" in name and "人民币" in name:
                    result["usdcny"] = {
                        "rate": _safe_num(row.iloc[1]) if len(row) > 1 else 0,
                        "name": name,
                    }
                    break

            # 用美元对多币种变化推算美元强弱
            usd_pairs = []
            for _, row in df.iterrows():
                name = str(row.iloc[0])
                if "美元" in name:
                    try:
                        rate = _safe_num(row.iloc[1])
                        usd_pairs.append(rate)
                    except (ValueError, IndexError):
                        pass

            result["available"] = result["usdcny"] is not None
    except Exception as e:
        print(f"[GLOBAL] Forex failed: {e}")

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 3. 美联储利率
# ============================================================

def get_fed_rate() -> dict:
    """获取美联储联邦基金利率历史"""
    cache_key = "fed_rate"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < _GLOBAL_TTL:
        return _global_cache[cache_key]["data"]

    result = {"current_rate": None, "last_change": None, "trend": "hold", "available": False}

    try:
        import akshare as ak
        df = ak.macro_bank_usa_interest_rate()
        if df is not None and len(df) > 0:
            # 取最新几条
            recent = df.tail(5)
            latest = recent.iloc[-1]
            prev = recent.iloc[-2] if len(recent) > 1 else latest

            # 列名可能不同，尝试取值
            cols = list(df.columns)
            rate_col = None
            date_col = None
            for c in cols:
                if "利率" in str(c) or "rate" in str(c).lower() or "今值" in str(c):
                    rate_col = c
                if "日期" in str(c) or "date" in str(c).lower() or "公布" in str(c):
                    date_col = c

            if rate_col:
                try:
                    current = _safe_num(latest[rate_col])
                    previous = _safe_num(prev[rate_col])
                    result["current_rate"] = current
                    result["previous_rate"] = previous
                    if current > previous:
                        result["trend"] = "hiking"
                        result["impact"] = "加息周期，资金回流美国，利空新兴市场"
                    elif current < previous:
                        result["trend"] = "cutting"
                        result["impact"] = "降息周期，资金流入新兴市场，利好A股"
                    else:
                        result["trend"] = "hold"
                        result["impact"] = "利率不变，市场等待政策信号"
                except (ValueError, TypeError):
                    pass

            if date_col:
                result["last_change"] = str(latest[date_col])

            result["available"] = result["current_rate"] is not None
    except Exception as e:
        print(f"[GLOBAL] Fed rate failed: {e}")

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 4. 全球 PE 估值对比
# ============================================================

def get_global_pe() -> dict:
    """获取中美 PE 对比"""
    cache_key = "global_pe"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < _GLOBAL_TTL:
        return _global_cache[cache_key]["data"]

    result = {"us_pe": None, "cn_pe": None, "spread": None, "available": False}

    def _extract_pe(df, label):
        """从 DataFrame 提取 PE 值，带合理性校验"""
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        # 优先找明确的 PE 列
        pe_col = None
        for c in df.columns:
            c_lower = str(c).lower()
            if "pe" in c_lower or "市盈率" in str(c):
                pe_col = c
                break
        if pe_col:
            val = _safe_num(last[pe_col])
        elif len(df.columns) > 1:
            # 降级：取第二列，但必须通过合理性校验
            val = _safe_num(last.iloc[1])
        else:
            return None

        # 合理性校验：全球主要市场 PE 通常在 5~60 之间
        if val is not None and 3 < val < 80:
            return round(val, 2)
        else:
            print(f"[GLOBAL_PE] {label} PE={val} 不在合理区间(3-80)，丢弃")
            return None

    try:
        import akshare as ak
        # 美国 PE
        try:
            df_us = ak.stock_market_pe_lg(symbol="美国")
            result["us_pe"] = _extract_pe(df_us, "美国")
        except Exception as e:
            print(f"[GLOBAL] US PE failed: {e}")

        # 中国 PE
        try:
            df_cn = ak.stock_market_pe_lg(symbol="中国")
            result["cn_pe"] = _extract_pe(df_cn, "中国")
        except Exception as e:
            print(f"[GLOBAL] CN PE failed: {e}")

        # 额外校验：中美 PE 不应相同（如果相同大概率是数据源错误）
        if result["us_pe"] and result["cn_pe"] and result["us_pe"] == result["cn_pe"]:
            print(f"[GLOBAL_PE] 中美PE相同({result['us_pe']})，疑似数据源错误，标记不可用")
            result["us_pe"] = None
            result["cn_pe"] = None
            result["notice"] = "中美PE数据异常（值相同），可能接口返回了错误数据"

        # PE 价差
        if result["us_pe"] and result["cn_pe"]:
            result["spread"] = round(result["us_pe"] - result["cn_pe"], 2)
            if result["spread"] > 10:
                result["assessment"] = "A股估值显著低于美股，性价比较高"
            elif result["spread"] > 0:
                result["assessment"] = "A股估值略低于美股"
            else:
                result["assessment"] = "A股估值高于美股，需谨慎"

        result["available"] = result["us_pe"] is not None or result["cn_pe"] is not None
    except Exception as e:
        print(f"[GLOBAL] Global PE failed: {e}")

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 5. 全球市场综合快照（一次性调用）
# ============================================================

def get_global_snapshot() -> dict:
    """一次性获取全球市场综合快照"""
    cache_key = "global_snapshot"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < 600:  # 10 分钟
        return _global_cache[cache_key]["data"]

    result = {
        "us_indices": get_us_indices(),
        "forex": get_forex_data(),
        "fed_rate": get_fed_rate(),
        "global_pe": get_global_pe(),
        "updatedAt": datetime.now().isoformat(),
    }

    # 生成简洁摘要（供 DeepSeek system prompt 注入）
    summary_lines = ["【全球市场快照】"]

    us = result["us_indices"]
    if us.get("available"):
        for key, name in [("dji", "道琼斯"), ("spx", "标普500"), ("ixic", "纳斯达克")]:
            d = us.get(key)
            if d:
                emoji = "📈" if d["change_pct"] > 0 else "📉"
                summary_lines.append(f"  {emoji} {name}: {d['close']:,.0f} ({d['change_pct']:+.2f}%)")

    fx = result["forex"]
    if fx.get("available") and fx.get("usdcny"):
        summary_lines.append(f"  💱 美元/人民币: {fx['usdcny']['rate']:.4f}")

    fed = result["fed_rate"]
    if fed.get("available"):
        trend_map = {"hiking": "加息周期⬆️", "cutting": "降息周期⬇️", "hold": "按兵不动"}
        summary_lines.append(f"  🏛️ 美联储利率: {fed['current_rate']}% ({trend_map.get(fed['trend'], '')})")

    gpe = result["global_pe"]
    if gpe.get("available"):
        if gpe.get("us_pe") and gpe.get("cn_pe"):
            summary_lines.append(f"  📊 PE对比: 美国{gpe['us_pe']} vs 中国{gpe['cn_pe']} (价差{gpe.get('spread', 0):+.1f})")

    result["summary"] = "\n".join(summary_lines)

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 6. DeepSeek 全球→A股影响分析
# ============================================================

def analyze_global_impact_on_a_shares() -> dict:
    """DeepSeek 分析全球市场对 A 股的影响"""
    cache_key = "global_impact"
    now = time.time()
    if cache_key in _global_cache and now - _global_cache[cache_key]["ts"] < 1800:  # 30 分钟
        return _global_cache[cache_key]["data"]

    snapshot = get_global_snapshot()
    result = {"analysis": "", "source": "none", "snapshot": snapshot}

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        result["analysis"] = snapshot.get("summary", "全球数据暂不可用")
        result["source"] = "data_only"
        _global_cache[cache_key] = {"data": result, "ts": now}
        return result

    prompt = f"""请分析以下全球市场数据对中国A股的影响，给出简洁的投资建议。

{snapshot.get('summary', '')}

要求：
1. 逐项分析每个全球因素对 A 股的影响（利好/利空/中性）
2. 特别关注：美联储政策→资金流向、美股走势→情绪传染、汇率→北向资金
3. 给出综合判断（一句话）
4. 给出具体操作建议（加仓/减仓/持有+针对哪类资产）
5. 控制在 200 字以内
6. 用 emoji 标注利好/利空"""

    try:
        import httpx
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "你是全球宏观分析师，专注分析国际市场对中国A股的传导效应。简洁、有数据支撑。"},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.5,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                result["analysis"] = data["choices"][0]["message"]["content"]
                result["source"] = "ai"
    except Exception as e:
        print(f"[GLOBAL_IMPACT] DeepSeek failed: {e}")
        result["analysis"] = snapshot.get("summary", "分析暂不可用")
        result["source"] = "data_only"

    _global_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 7. 决策数据包（供 Claude 快速拉取）
# ============================================================

def get_decision_data_pack(user_id: str = "default") -> dict:
    """一次性返回全量决策数据包 — 供 Claude 做投资决策用"""
    from services.stock_monitor import load_stock_holdings, scan_all_holdings
    from services.fund_monitor import load_fund_holdings, scan_all_fund_holdings
    from services.data_layer import (
        get_valuation_percentile, get_fear_greed_index,
        get_technical_indicators, get_market_news,
        get_northbound_flow, get_margin_trading,
    )
    from services.portfolio_overview import get_portfolio_overview

    pack = {
        "timestamp": datetime.now().isoformat(),
        "global": get_global_snapshot(),
        "global_impact": analyze_global_impact_on_a_shares(),
    }

    # A 股核心数据
    try:
        pack["valuation"] = get_valuation_percentile()
    except Exception:
        pack["valuation"] = {"error": "unavailable"}

    try:
        pack["fear_greed"] = get_fear_greed_index()
    except Exception:
        pack["fear_greed"] = {"error": "unavailable"}

    try:
        pack["technical"] = get_technical_indicators()
    except Exception:
        pack["technical"] = {"error": "unavailable"}

    try:
        pack["northbound"] = get_northbound_flow()
    except Exception:
        pack["northbound"] = {"error": "unavailable"}

    try:
        pack["margin"] = get_margin_trading()
    except Exception:
        pack["margin"] = {"error": "unavailable"}

    # 持仓数据
    try:
        pack["stock_holdings"] = scan_all_holdings(user_id)
    except Exception:
        pack["stock_holdings"] = {"error": "unavailable"}

    try:
        pack["fund_holdings"] = scan_all_fund_holdings(user_id)
    except Exception:
        pack["fund_holdings"] = {"error": "unavailable"}

    try:
        pack["portfolio_overview"] = get_portfolio_overview(user_id)
    except Exception:
        pack["portfolio_overview"] = {"error": "unavailable"}

    # 最新新闻
    try:
        pack["news"] = get_market_news(5)
    except Exception:
        pack["news"] = []

    # 国内政策数据
    try:
        from services.policy_data import get_all_policy_topics, get_real_estate_data
        pack["policy_topics"] = get_all_policy_topics()
        pack["real_estate"] = get_real_estate_data()
    except Exception:
        pack["policy_topics"] = {"error": "unavailable"}

    # 市场微观因子（大宗商品+限售解禁+ETF 资金流）
    try:
        from services.market_factors import get_all_market_factors
        pack["market_factors"] = get_all_market_factors()
    except Exception:
        pack["market_factors"] = {"error": "unavailable"}

    # 持仓关联智能（个股新闻+资金流+行业+解禁）
    try:
        from services.holding_intelligence import scan_all_holding_intelligence
        pack["holding_intelligence"] = scan_all_holding_intelligence()
    except Exception:
        pack["holding_intelligence"] = {"error": "unavailable"}

    # V8 扩展宏观（GDP/工业增加值/社零/固投/龙虎榜/管理层增减持）
    try:
        from services.macro_v8 import get_all_v8_macro
        pack["macro_v8"] = get_all_v8_macro()
    except Exception:
        pack["macro_v8"] = {"error": "unavailable"}

    return pack
