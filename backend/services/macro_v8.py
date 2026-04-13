"""
钱袋子 — 扩展宏观因子（V8 补齐）
GDP / 工业增加值 / 社零 / 固投 / 龙虎榜 / 管理层增减持

对标用户需求清单：
  一.1 GDP/工业增加值/社零/固投
  一.5 龙虎榜
  二.4 股东增减持
"""
import time
import math
import traceback
from datetime import datetime

_v8_cache = {}
_V8_TTL = 86400  # 宏观数据月度更新，缓存 24h
_V8_DAILY_TTL = 3600  # 龙虎榜/增减持缓存 1h


def _safe_val(v):
    """安全转换值，处理 NaN/Inf/None"""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return str(v)


# ============================================================
# 1. GDP
# ============================================================

def get_gdp() -> dict:
    cache_key = "gdp"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False}
    try:
        import akshare as ak
        df = ak.macro_china_gdp()
        if df is not None and len(df) > 0:
            latest = df.iloc[0]  # 最新在第一行
            cols = list(df.columns)
            result["available"] = True
            result["period"] = str(latest[cols[0]]) if cols else ""
            # 找同比增长
            for c in cols:
                if "同比" in str(c) or "增速" in str(c):
                    try:
                        result["yoy"] = round(float(latest[c]), 1)
                    except (ValueError, TypeError):
                        pass
            result["total_rows"] = len(df)
            print(f"[GDP] {result.get('period', '')} yoy={result.get('yoy', 'N/A')}%")
    except Exception as e:
        print(f"[GDP] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 2. 工业增加值
# ============================================================

def get_industrial_value_added() -> dict:
    cache_key = "gyzjz"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False}
    try:
        import akshare as ak
        df = ak.macro_china_gyzjz()
        if df is not None and len(df) > 0:
            latest = df.iloc[0]
            cols = list(df.columns)
            result["available"] = True
            result["period"] = str(latest[cols[0]]) if cols else ""
            for c in cols:
                if "同比" in str(c):
                    try:
                        result["yoy"] = round(float(latest[c]), 1)
                    except (ValueError, TypeError):
                        pass
            print(f"[GYZJZ] {result.get('period', '')} yoy={result.get('yoy', 'N/A')}%")
    except Exception as e:
        print(f"[GYZJZ] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 3. 社会消费品零售
# ============================================================

def get_consumer_goods_retail() -> dict:
    cache_key = "retail"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False}
    try:
        import akshare as ak
        df = ak.macro_china_consumer_goods_retail()
        if df is not None and len(df) > 0:
            latest = df.iloc[0]
            cols = list(df.columns)
            result["available"] = True
            result["period"] = str(latest[cols[0]]) if cols else ""
            for c in cols:
                if "同比" in str(c):
                    try:
                        result["yoy"] = round(float(latest[c]), 1)
                    except (ValueError, TypeError):
                        pass
            print(f"[RETAIL] {result.get('period', '')} yoy={result.get('yoy', 'N/A')}%")
    except Exception as e:
        print(f"[RETAIL] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 4. 固定资产投资
# ============================================================

def get_fixed_asset_investment() -> dict:
    cache_key = "fai"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False}
    try:
        import akshare as ak
        df = ak.macro_china_gdzctz()
        if df is not None and len(df) > 0:
            latest = df.iloc[0]
            cols = list(df.columns)
            result["available"] = True
            result["period"] = str(latest[cols[0]]) if cols else ""
            for c in cols:
                if "同比" in str(c) or "增速" in str(c):
                    try:
                        result["yoy"] = round(float(latest[c]), 1)
                    except (ValueError, TypeError):
                        pass
            print(f"[FAI] {result.get('period', '')} yoy={result.get('yoy', 'N/A')}%")
    except Exception as e:
        print(f"[FAI] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 5. 龙虎榜（机构+游资动向）
# ============================================================

def get_lhb_summary() -> dict:
    cache_key = "lhb"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_DAILY_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False, "items": []}
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - __import__("datetime").timedelta(days=3)).strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            items = []
            for _, row in df.head(10).iterrows():
                item = {}
                for c in cols[:6]:  # 取前 6 列
                    v = row[c]
                    sv = _safe_val(v)
                    item[c] = str(sv) if sv is not None else ""
                    item[c] = str(v) if v is not None else ""
                items.append(item)
            result["items"] = items
            result["count"] = len(df)
            result["available"] = True
            print(f"[LHB] Got {len(df)} entries, top: {items[0] if items else 'N/A'}")
    except Exception as e:
        print(f"[LHB] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 6. 管理层增减持
# ============================================================

def get_management_holdings() -> dict:
    cache_key = "mgmt_hold"
    now = time.time()
    if cache_key in _v8_cache and now - _v8_cache[cache_key]["ts"] < _V8_DAILY_TTL:
        return _v8_cache[cache_key]["data"]

    result = {"available": False, "items": []}
    try:
        import akshare as ak
        df = ak.stock_hold_management_detail_cninfo()
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            items = []
            for _, row in df.head(10).iterrows():
                item = {}
                for c in cols[:6]:
                    v = row[c]
                    sv = _safe_val(v)
                    item[c] = str(sv) if sv is not None else ""
                items.append(item)
            result["items"] = items
            result["count"] = len(df)
            result["available"] = True
            print(f"[MGMT] Got {len(df)} entries")
    except Exception as e:
        print(f"[MGMT] FAIL: {e}")

    _v8_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 7. 持仓股票增减持检查
# ============================================================

def check_holding_management_change(stock_codes: list) -> list:
    """检查持仓股票是否有管理层增减持"""
    data = get_management_holdings()
    if not data["available"]:
        return []

    alerts = []
    items = data.get("items", [])
    if not items:
        return []

    cols = list(items[0].keys())
    code_col = next((c for c in cols if "代码" in c), None)
    name_col = next((c for c in cols if "名称" in c or "简称" in c), None)
    type_col = next((c for c in cols if "变动" in c or "增减" in c), None)

    if not code_col:
        return []

    for item in items:
        item_code = str(item.get(code_col, "")).strip()
        for holding_code in stock_codes:
            if item_code == holding_code or item_code in holding_code:
                name = item.get(name_col, item_code) if name_col else item_code
                change_type = item.get(type_col, "变动") if type_col else "变动"
                is_reduce = "减" in str(change_type)
                alerts.append({
                    "code": holding_code,
                    "name": name,
                    "type": change_type,
                    "level": "warning" if is_reduce else "info",
                    "msg": f"{'⚠️' if is_reduce else '💰'} {name}({holding_code}) 管理层{change_type}",
                })
    return alerts


# ============================================================
# 8. 全部扩展宏观数据
# ============================================================

def get_all_v8_macro() -> dict:
    return {
        "gdp": get_gdp(),
        "industrial": get_industrial_value_added(),
        "retail": get_consumer_goods_retail(),
        "fixed_asset": get_fixed_asset_investment(),
        "lhb": get_lhb_summary(),
        "management": get_management_holdings(),
        "updatedAt": datetime.now().isoformat(),
    }


# ============================================================
# 9. 生成摘要文本（供 context 注入）
# ============================================================

def get_v8_macro_summary() -> str:
    lines = []
    gdp = get_gdp()
    if gdp.get("available") and gdp.get("yoy"):
        lines.append(f"GDP同比: {gdp['yoy']}% ({gdp.get('period', '')})")

    ind = get_industrial_value_added()
    if ind.get("available") and ind.get("yoy"):
        lines.append(f"工业增加值同比: {ind['yoy']}%")

    ret = get_consumer_goods_retail()
    if ret.get("available") and ret.get("yoy"):
        lines.append(f"社零同比: {ret['yoy']}%")

    fai = get_fixed_asset_investment()
    if fai.get("available") and fai.get("yoy"):
        lines.append(f"固投同比: {fai['yoy']}%")

    lhb = get_lhb_summary()
    if lhb.get("available") and lhb.get("items"):
        lines.append(f"龙虎榜: 近3日{lhb['count']}条")

    return "\n".join(lines) if lines else ""
