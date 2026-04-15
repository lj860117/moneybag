"""
钱袋子 — 市场微观因子
大宗商品期货 + 限售股解禁 + ETF 资金流

这些因子对 A 股有显著影响但之前缺失：
1. 大宗商品（原油/铜/黄金期货）→ 输入性通胀/经济晴雨表
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
    "tags": ['大宗商品', '解禁', 'ETF资金'],
    "description": "市场微观因子：大宗商品期货+限售股解禁+ETF资金流",
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
    """获取 ETF 资金流向（机构偏好风向标）"""
    cache_key = "etf_flow"
    now = time.time()
    if cache_key in _factor_cache and now - _factor_cache[cache_key]["ts"] < 3600:
        return _factor_cache[cache_key]["data"]

    result = {"top_inflow": [], "top_outflow": [], "total_etf": 0, "available": False}
    try:
        import akshare as ak
        df = None

        # 尝试多个可能的接口名（AKShare 频繁改名）
        for func_name in [
            "fund_etf_fund_daily_em",
            "fund_etf_spot_em",
            "fund_etf_fund_info_em",
        ]:
            if hasattr(ak, func_name):
                try:
                    df = getattr(ak, func_name)()
                    if df is not None and len(df) > 0:
                        print(f"[ETF_FLOW] Using interface: {func_name}")
                        break
                except Exception as e:
                    print(f"[ETF_FLOW] {func_name} failed: {e}")
                    df = None

        if df is not None and len(df) > 0:
            cols = list(df.columns)
            result["total_etf"] = len(df)

            # 找资金流相关列
            flow_col = [c for c in cols if "净流" in c or "流入" in c or "flow" in c.lower()]
            name_col = [c for c in cols if "名称" in c or "简称" in c]
            code_col = [c for c in cols if "代码" in c or "code" in c.lower()]

            if flow_col and name_col:
                fc = flow_col[0]
                nc = name_col[0]
                cc = code_col[0] if code_col else None

                # 清理数据
                clean = df[[nc, fc] + ([cc] if cc else [])].copy()
                clean[fc] = clean[fc].apply(lambda x: _safe_float(x))
                clean = clean.dropna(subset=[fc])

                if len(clean) > 0:
                    # TOP 5 净流入
                    top_in = clean.nlargest(5, fc)
                    for _, row in top_in.iterrows():
                        item = {"name": str(row[nc]), "flow": round(float(row[fc]), 2)}
                        if cc:
                            item["code"] = str(row[cc])
                        result["top_inflow"].append(item)

                    # TOP 5 净流出
                    top_out = clean.nsmallest(5, fc)
                    for _, row in top_out.iterrows():
                        item = {"name": str(row[nc]), "flow": round(float(row[fc]), 2)}
                        if cc:
                            item["code"] = str(row[cc])
                        result["top_outflow"].append(item)

                    result["available"] = True
                    print(f"[ETF_FLOW] Got {len(df)} ETFs, top inflow: {result['top_inflow'][0]['name'] if result['top_inflow'] else 'N/A'}")
            else:
                print(f"[ETF_FLOW] 列名匹配失败，可用列: {cols}")

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
