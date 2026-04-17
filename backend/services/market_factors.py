"""
钱袋子 — 市场微观因子
大宗商品期货 + 限售股解禁 + ETF 资金流 + 原油/铁矿石/螺纹钢（V6 Phase 1 新增）

这些因子对 A 股有显著影响但之前缺失：
1. 大宗商品（原油/铜/黄金/铁矿石/螺纹钢期货）→ 输入性通胀/经济晴雨表
2. 限售股解禁 → 抛压/个股风险
3. ETF 资金流 → 机构资金流向/行业偏好
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "market_factors",
    "scope": "public",
    "input": [],
    "output": "market_micro",
    "cost": "cpu",
    "tags": ['大宗商品', '解禁', 'ETF资金', '原油', '铁矿石'],
    "description": "市场微观因子：大宗商品(黄金/铜/原油/铁矿石/螺纹钢)+限售股解禁+ETF资金流",
    "layer": "data",
    "priority": 3,
}
import time
import traceback
from datetime import datetime, timedelta

_factor_cache = {}


def get_commodity_prices() -> dict:
    """获取大宗商品期货价格（黄金/铜）"""
    cache_key = "commodities"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    result = {"gold": None, "copper": None, "available": False}
    try:
        import akshare as ak
        # 黄金期货（上期所）
        try:
            df = ak.futures_main_sina(symbol="AU0")
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                cols = list(df.columns)
                close_col = [c for c in cols if "收" in c or "close" in c.lower()]
                if close_col:
                    price = float(last[close_col[0]])
                    # 计算涨跌
                    prev = float(df.iloc[-2][close_col[0]]) if len(df) > 1 else price
                    change = round((price - prev) / prev * 100, 2) if prev > 0 else 0
                    result["gold"] = {"price": price, "change_pct": change, "unit": "元/克"}
        except Exception as e:
            print(f"[COMMODITY] Gold fail: {e}")

        # 铜期货（上期所）
        try:
            df = ak.futures_main_sina(symbol="CU0")
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                cols = list(df.columns)
                close_col = [c for c in cols if "收" in c or "close" in c.lower()]
                if close_col:
                    price = float(last[close_col[0]])
                    prev = float(df.iloc[-2][close_col[0]]) if len(df) > 1 else price
                    change = round((price - prev) / prev * 100, 2) if prev > 0 else 0
                    result["copper"] = {"price": price, "change_pct": change, "unit": "元/吨"}
        except Exception as e:
            print(f"[COMMODITY] Copper fail: {e}")

        result["available"] = result["gold"] is not None or result["copper"] is not None

    except Exception as e:
        print(f"[COMMODITY] Failed: {e}")

    _factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_stock_unlock_schedule() -> dict:
    """获取近期限售股解禁计划"""
    cache_key = "unlock"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    result = {"items": [], "total_value": 0, "available": False}
    try:
        import akshare as ak
        df = ak.stock_restricted_release_summary_em()
        if df is not None and len(df) > 0:
            cols = list(df.columns)

            # 找日期列并按日期排序
            date_col = next((c for c in cols if "日期" in str(c) or "date" in str(c).lower() or "解禁" in str(c)), None)
            if date_col:
                try:
                    import pandas as pd
                    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                    df = df.dropna(subset=[date_col])
                    df = df.sort_values(date_col, ascending=False)
                    # 只保留最近60天内的数据
                    cutoff = datetime.now() - timedelta(days=60)
                    df = df[df[date_col] >= cutoff]
                except Exception as e:
                    print(f"[UNLOCK] Date sort failed: {e}")
                    # 即使排序失败也继续，至少返回原始数据

            items = []
            for _, row in df.head(15).iterrows():
                item = {}
                for c in cols:
                    val = row[c]
                    # 处理 NaN
                    try:
                        if val != val:  # NaN check
                            val = None
                    except Exception:
                        pass
                    # 日期转字符串
                    try:
                        if hasattr(val, "strftime"):
                            val = val.strftime("%Y-%m-%d")
                    except Exception:
                        pass
                    item[c] = val
                items.append(item)

            # 尝试提取总解禁市值
            value_col = [c for c in cols if "市值" in c or "金额" in c]
            if value_col and items:
                total = 0
                for it in items:
                    v = it.get(value_col[0])
                    if v is not None:
                        try:
                            total += float(v)
                        except (ValueError, TypeError):
                            pass
                result["total_value"] = round(total, 2)

            result["items"] = items
            result["count"] = len(items)
            result["available"] = len(items) > 0
            print(f"[UNLOCK] Got {len(items)} unlock events (recent 60d)")

    except Exception as e:
        print(f"[UNLOCK] Failed: {e}")
        traceback.print_exc()

    _factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def check_holding_unlock(stock_codes: list) -> list:
    """检查用户持仓股票是否有近期解禁"""
    unlock = get_stock_unlock_schedule()
    if not unlock["available"]:
        return []

    alerts = []
    items = unlock.get("items", [])
    cols = list(items[0].keys()) if items else []
    code_col = [c for c in cols if "代码" in c or "code" in c.lower()]
    name_col = [c for c in cols if "名称" in c or "简称" in c]
    date_col = [c for c in cols if "日期" in c or "date" in c.lower()]

    if not code_col:
        return []

    cc = code_col[0]
    nc = name_col[0] if name_col else None
    dc = date_col[0] if date_col else None

    for item in items:
        item_code = str(item.get(cc, "")).strip()
        for holding_code in stock_codes:
            if item_code == holding_code or item_code in holding_code:
                name = item.get(nc, item_code) if nc else item_code
                date = item.get(dc, "未知") if dc else "未知"
                alerts.append({
                    "code": holding_code,
                    "name": name,
                    "unlock_date": str(date),
                    "level": "warning",
                    "msg": f"⚠️ {name}({holding_code}) 近期有限售股解禁({date})，注意抛压风险",
                })
    return alerts


def get_etf_fund_flow() -> dict:
    """获取 ETF 资金流向（机构偏好风向标）

    优先用真实资金流接口（如果存在），
    降级方案：用 fund_etf_fund_daily_em 的增长率排名反映 ETF 市场热度。
    """
    cache_key = "etf_flow"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    result = {"top_inflow": [], "top_outflow": [], "total_etf": 0, "available": False}
    try:
        import akshare as ak

        # ---------- 方案 A: 真实资金流接口 ----------
        flow_done = False
        for func_name in ["fund_etf_fund_flow_em", "fund_etf_spot_em"]:
            if not hasattr(ak, func_name):
                continue
            try:
                df = getattr(ak, func_name)()
                if df is None or len(df) == 0:
                    continue
                cols = list(df.columns)
                flow_col = [c for c in cols if "净流" in c or "流入" in c or "flow" in c.lower()]
                name_col = [c for c in cols if "名称" in c or "简称" in c]
                code_col = [c for c in cols if "代码" in c or "code" in c.lower()]
                if flow_col and name_col:
                    fc, nc = flow_col[0], name_col[0]
                    cc = code_col[0] if code_col else None
                    clean = df[[nc, fc] + ([cc] if cc else [])].copy()
                    clean[fc] = clean[fc].apply(lambda x: _safe_float(x))
                    clean = clean.dropna(subset=[fc])
                    if len(clean) > 0:
                        result["total_etf"] = len(df)
                        for _, row in clean.nlargest(5, fc).iterrows():
                            item = {"name": str(row[nc]), "flow": round(float(row[fc]), 2)}
                            if cc: item["code"] = str(row[cc])
                            result["top_inflow"].append(item)
                        for _, row in clean.nsmallest(5, fc).iterrows():
                            item = {"name": str(row[nc]), "flow": round(float(row[fc]), 2)}
                            if cc: item["code"] = str(row[cc])
                            result["top_outflow"].append(item)
                        result["available"] = True
                        flow_done = True
                        print(f"[ETF_FLOW] 方案A成功: {func_name}, {len(df)} ETFs")
                        break
            except Exception as e:
                print(f"[ETF_FLOW] {func_name} failed: {e}")

        # ---------- 方案 B: 用增长率排名当替代 ----------
        if not flow_done:
            try:
                df = ak.fund_etf_fund_daily_em()
                if df is not None and len(df) > 0:
                    cols = list(df.columns)
                    name_col = [c for c in cols if "简称" in c or "名称" in c]
                    code_col = [c for c in cols if "代码" in c]
                    rate_col = [c for c in cols if "增长率" in c or "涨跌幅" in c]
                    nc = name_col[0] if name_col else None
                    cc = code_col[0] if code_col else None
                    rc = rate_col[0] if rate_col else None

                    if nc and rc:
                        clean = df[[nc, rc] + ([cc] if cc else [])].copy()
                        # 增长率可能是 "0.03%" 格式，先去掉百分号再转 float
                        clean[rc] = clean[rc].apply(lambda x: _safe_float(
                            str(x).replace("%", "").strip() if x is not None else x
                        ))
                        clean = clean.dropna(subset=[rc])
                        result["total_etf"] = len(df)

                        if len(clean) > 0:
                            # 涨幅 TOP5 = 资金涌入方向
                            for _, row in clean.nlargest(5, rc).iterrows():
                                item = {"name": str(row[nc]), "flow": round(float(row[rc]), 2)}
                                if cc: item["code"] = str(row[cc])
                                result["top_inflow"].append(item)
                            # 跌幅 TOP5 = 资金流出方向
                            for _, row in clean.nsmallest(5, rc).iterrows():
                                item = {"name": str(row[nc]), "flow": round(float(row[rc]), 2)}
                                if cc: item["code"] = str(row[cc])
                                result["top_outflow"].append(item)
                            result["available"] = True
                            result["notice"] = "ETF资金流接口暂不可用，使用增长率排名替代"
                            print(f"[ETF_FLOW] 方案B(增长率替代): {len(df)} ETFs, top={result['top_inflow'][0]['name'] if result['top_inflow'] else 'N/A'}")
                    else:
                        print(f"[ETF_FLOW] 方案B列名匹配失败: {cols}")
            except Exception as e:
                print(f"[ETF_FLOW] 方案B failed: {e}")

    except Exception as e:
        print(f"[ETF_FLOW] Failed: {e}")
        traceback.print_exc()

    _factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_all_market_factors() -> dict:
    """获取全部市场微观因子"""
    return {
        "commodities": get_commodity_prices(),
        "unlock": get_stock_unlock_schedule(),
        "etf_flow": get_etf_fund_flow(),
        "updatedAt": datetime.now().isoformat(),
    }


def _safe_float(x):
    """安全转换为 float"""
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (ValueError, TypeError):
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# V6 Phase 1 新增：原油 + 铁矿石 + 螺纹钢
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_futures_price(symbol: str, label: str, unit: str) -> dict:
    """通用期货价格获取（复用黄金/铜的逻辑）"""
    try:
        import akshare as ak
        df = ak.futures_main_sina(symbol=symbol)
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            cols = list(df.columns)
            close_col = [c for c in cols if "收" in c or "close" in c.lower()]
            if close_col:
                price = float(last[close_col[0]])
                prev = float(df.iloc[-2][close_col[0]]) if len(df) > 1 else price
                change = round((price - prev) / prev * 100, 2) if prev > 0 else 0

                # 计算 vs 30 日均线偏离
                try:
                    close_vals = [float(row[close_col[0]]) for _, row in df.tail(30).iterrows()]
                    if len(close_vals) >= 5:
                        avg_30 = sum(close_vals) / len(close_vals)
                        vs_30d = round((price - avg_30) / avg_30 * 100, 1) if avg_30 > 0 else 0
                    else:
                        vs_30d = 0
                except Exception:
                    vs_30d = 0

                return {
                    "price": price,
                    "change_pct": change,
                    "unit": unit,
                    "vs_30d_avg_pct": vs_30d,
                    "available": True,
                }
    except Exception as e:
        print(f"[COMMODITY] {label}({symbol}) fail: {e}")
    return {"price": 0, "change_pct": 0, "unit": unit, "available": False}


def get_crude_oil_price() -> dict:
    """获取原油价格（国内 SC + 国际布伦特）

    Returns:
        {
            "sc": {price, change_pct, unit, vs_30d_avg_pct, available},
            "brent": {price, change_pct, unit, vs_30d_avg_pct, available},
            "alert_level": "normal" / "warning" / "crisis",
            "available": bool,
        }
    """
    cache_key = "crude_oil"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    # 国内原油期货 SC0（上海国际能源交易中心）
    sc = _fetch_futures_price("SC0", "原油SC", "元/桶")

    # 布伦特原油（通过外盘获取）
    brent = {"price": 0, "available": False, "unit": "美元/桶", "change_pct": 0}
    try:
        import akshare as ak
        # 先尝试 futures_foreign_hist
        try:
            df = ak.futures_foreign_hist(symbol="布伦特原油")
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                cols = list(df.columns)
                close_col = [c for c in cols if "收" in c or "close" in c.lower() or "收盘" in c]
                if close_col:
                    price = float(last[close_col[0]])
                    prev = float(df.iloc[-2][close_col[0]]) if len(df) > 1 else price
                    change = round((price - prev) / prev * 100, 2) if prev > 0 else 0
                    brent = {"price": price, "change_pct": change, "unit": "美元/桶", "available": True}
        except Exception as e:
            print(f"[COMMODITY] Brent(foreign_hist) fail: {e}")

        # 降级：用国内 SC 价格 / 汇率估算
        if not brent["available"] and sc["available"]:
            # SC0 是元/桶，粗略用 7.2 汇率估算 USD
            estimated_usd = round(sc["price"] / 7.2, 1)
            brent = {
                "price": estimated_usd,
                "change_pct": sc["change_pct"],
                "unit": "美元/桶(估算)",
                "available": True,
                "estimated": True,
            }
    except Exception as e:
        print(f"[COMMODITY] Brent total fail: {e}")

    # 油价告警等级
    from config import OIL_BRENT_NORMAL, OIL_BRENT_WARNING, OIL_BRENT_CRISIS
    brent_price = brent.get("price", 0)
    if brent_price >= OIL_BRENT_CRISIS:
        alert_level = "crisis"
    elif brent_price >= OIL_BRENT_WARNING:
        alert_level = "warning"
    else:
        alert_level = "normal"

    result = {
        "sc": sc,
        "brent": brent,
        "alert_level": alert_level,
        "available": sc["available"] or brent["available"],
    }

    print(f"[COMMODITY] 原油: SC={sc.get('price', 'N/A')}, Brent≈{brent_price}, alert={alert_level}")
    _factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_industrial_metals() -> dict:
    """获取工业金属期货价格（铁矿石 + 螺纹钢）

    Returns:
        {
            "iron_ore": {price, change_pct, unit, available},
            "rebar": {price, change_pct, unit, available},
            "available": bool,
        }
    """
    cache_key = "industrial_metals"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    iron = _fetch_futures_price("I0", "铁矿石", "元/吨")
    rebar = _fetch_futures_price("RB0", "螺纹钢", "元/吨")

    result = {
        "iron_ore": iron,
        "rebar": rebar,
        "available": iron["available"] or rebar["available"],
    }

    print(f"[COMMODITY] 工业金属: 铁矿石={iron.get('price', 'N/A')}, 螺纹钢={rebar.get('price', 'N/A')}")
    _factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_commodity_impact_assessment() -> dict:
    """大宗商品价格 → A股影响评估（纯规则，0 LLM）

    Returns:
        {
            "oil_impact": str,
            "metal_impact": str,
            "overall_tone": "bullish" / "bearish" / "neutral",
            "details": [{commodity, signal, reason}],
        }
    """
    oil = get_crude_oil_price()
    metals = get_industrial_metals()
    base = get_commodity_prices()

    details = []
    bearish_count = 0
    bullish_count = 0

    # 原油影响判断
    if oil["available"]:
        brent_price = oil["brent"].get("price", 0)
        if oil["alert_level"] == "crisis":
            details.append({"commodity": "原油", "signal": "bearish",
                            "reason": f"布伦特{brent_price}美元/桶（危机线），输入性通胀+航空化工承压"})
            bearish_count += 2
        elif oil["alert_level"] == "warning":
            details.append({"commodity": "原油", "signal": "bearish",
                            "reason": f"布伦特{brent_price}美元/桶（警戒线），关注能源冲击传导"})
            bearish_count += 1
        else:
            details.append({"commodity": "原油", "signal": "neutral",
                            "reason": f"布伦特{brent_price}美元/桶，正常区间"})

    # 黄金 → 避险情绪指标
    if base.get("available") and base.get("gold"):
        gold_change = base["gold"].get("change_pct", 0)
        if gold_change > 2:
            details.append({"commodity": "黄金", "signal": "bearish",
                            "reason": f"金价大涨{gold_change}%，避险情绪升温"})
            bearish_count += 1
        elif gold_change < -2:
            details.append({"commodity": "黄金", "signal": "bullish",
                            "reason": f"金价下跌{gold_change}%，风险偏好回升"})
            bullish_count += 1

    # 铜 → 经济晴雨表
    if base.get("available") and base.get("copper"):
        copper_change = base["copper"].get("change_pct", 0)
        if copper_change > 2:
            details.append({"commodity": "铜", "signal": "bullish",
                            "reason": f"铜价上涨{copper_change}%，经济复苏预期"})
            bullish_count += 1
        elif copper_change < -2:
            details.append({"commodity": "铜", "signal": "bearish",
                            "reason": f"铜价下跌{copper_change}%，经济放缓信号"})
            bearish_count += 1

    # 铁矿石 → 基建活跃度
    if metals["available"] and metals["iron_ore"].get("available"):
        iron_change = metals["iron_ore"].get("change_pct", 0)
        if abs(iron_change) > 2:
            signal = "bullish" if iron_change > 0 else "bearish"
            reason = "基建链活跃" if iron_change > 0 else "基建需求放缓"
            details.append({"commodity": "铁矿石", "signal": signal,
                            "reason": f"铁矿石涨跌{iron_change}%，{reason}"})
            if iron_change > 0:
                bullish_count += 1
            else:
                bearish_count += 1

    # 总基调
    if bearish_count > bullish_count + 1:
        overall_tone = "bearish"
        oil_impact = "大宗商品信号偏空，输入性通胀压力上升" if oil["available"] else "大宗商品信号偏空"
    elif bullish_count > bearish_count + 1:
        overall_tone = "bullish"
        oil_impact = "大宗商品信号偏多，经济复苏预期" if oil["available"] else "大宗商品信号偏多"
    else:
        overall_tone = "neutral"
        oil_impact = "大宗商品信号中性"

    return {
        "oil_impact": oil_impact,
        "metal_impact": f"工业金属{'活跃' if bullish_count > 0 else '平淡'}",
        "overall_tone": overall_tone,
        "details": details,
        "oil_alert_level": oil.get("alert_level", "normal") if oil["available"] else "unknown",
    }


def get_all_market_factors_v6() -> dict:
    """V6 扩展版：获取全部市场微观因子（含原油+工业金属）"""
    return {
        "commodities": get_commodity_prices(),
        "crude_oil": get_crude_oil_price(),
        "industrial_metals": get_industrial_metals(),
        "unlock": get_stock_unlock_schedule(),
        "etf_flow": get_etf_fund_flow(),
        "impact_assessment": get_commodity_impact_assessment(),
        "updatedAt": datetime.now().isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pipeline enrich() — 核心接入函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 把大宗商品数据注入 DecisionContext

    接口规范：
      - scope=public，不需要 user_id
      - 把结果写入 ctx.modules_results["market_factors"]
      - 返回修改后的 ctx
    """
    try:
        oil = get_crude_oil_price()
        base = get_commodity_prices()
        metals = get_industrial_metals()
        impact = get_commodity_impact_assessment()

        # 方向判断：油价危机 → bearish，正常 → neutral
        tone = impact.get("overall_tone", "neutral")
        if oil.get("alert_level") == "crisis":
            direction = "bearish"
            score = 20
        elif oil.get("alert_level") == "warning":
            direction = "neutral"  # 警戒但非危机
            score = 35
        else:
            direction = tone
            score = 50

        ctx.modules_results["market_factors"] = {
            "direction": direction,
            "score": score,
            "confidence": 60,
            "available": True,
            "detail": impact.get("oil_impact", ""),
            "oil_alert_level": oil.get("alert_level", "unknown"),
            "oil_brent_price": oil.get("brent", {}).get("price", 0),
            "oil_sc_price": oil.get("sc", {}).get("price", 0),
            "gold_price": base.get("gold", {}).get("price", 0) if base.get("gold") else 0,
            "copper_price": base.get("copper", {}).get("price", 0) if base.get("copper") else 0,
            "iron_ore_price": metals.get("iron_ore", {}).get("price", 0),
            "rebar_price": metals.get("rebar", {}).get("price", 0),
            "impact_details": impact.get("details", []),
        }

        if "market_factors" not in ctx.modules_called:
            ctx.modules_called.append("market_factors")

    except Exception as e:
        print(f"[MARKET_FACTORS] enrich failed: {e}")
        ctx.modules_results["market_factors"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0,
        }

    return ctx
