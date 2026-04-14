"""
钱袋子 — 扩展宏观数据
M1/社融/LPR/涨跌家数/美林时钟
Phase 2 新增数据源，对标量化文档"核心数据维度"
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "macro_extended",
    "scope": "public",
    "input": [],
    "output": "macro_extended",
    "cost": "cpu",
    "tags": ['宏观', '美林时钟', 'LPR', 'M1', '社融'],
    "description": "扩展宏观：M1/社融/LPR/涨跌家数/美林时钟经济周期",
    "layer": "data",
    "priority": 3,
}
import time
import traceback
from config import MACRO_CACHE_TTL, FACTOR_CACHE_TTL

_ext_macro_cache = {}


def get_m1_data() -> dict:
    """获取 M1 货币供应量同比增速 + M1-M2 剪刀差"""
    cache_key = "m1_data"
    now = time.time()
    if cache_key in _ext_macro_cache and now - _ext_macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return _ext_macro_cache[cache_key]["data"]

    result = {"m1_growth": None, "m2_growth": None, "scissors": None, "period": "", "available": False}
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            m1_col = next((c for c in cols if "M1" in c and "同比" in c), None)
            m2_col = next((c for c in cols if "M2" in c and "同比" in c or "货币和准货币" in c and "同比" in c), None)
            date_col = next((c for c in cols if "月份" in c or "日期" in c), cols[0])

            if m1_col:
                for i in range(min(10, len(df))):
                    row = df.iloc[i]
                    v = row[m1_col]
                    try:
                        m1_val = float(v)
                        result["m1_growth"] = round(m1_val, 1)
                        result["period"] = str(row[date_col])
                        result["available"] = True
                        break
                    except (ValueError, TypeError):
                        continue

            if m2_col and result["available"]:
                for i in range(min(10, len(df))):
                    try:
                        m2_val = float(df.iloc[i][m2_col])
                        result["m2_growth"] = round(m2_val, 1)
                        if result["m1_growth"] is not None:
                            result["scissors"] = round(result["m1_growth"] - m2_val, 1)
                        break
                    except (ValueError, TypeError):
                        continue

            print(f"[M1] M1={result['m1_growth']}%, M2={result['m2_growth']}%, 剪刀差={result['scissors']}%")
    except Exception as e:
        print(f"[M1] Failed: {e}")
        traceback.print_exc()

    _ext_macro_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_social_financing() -> dict:
    """获取社会融资规模数据"""
    cache_key = "shrzgm"
    now = time.time()
    if cache_key in _ext_macro_cache and now - _ext_macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return _ext_macro_cache[cache_key]["data"]

    result = {"total": None, "period": "", "yoy_change": None, "available": False}
    try:
        import akshare as ak
        df = ak.macro_china_shrzgm()
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            total_col = next((c for c in cols if "社会融资规模" in c or "社融" in c), None)
            date_col = next((c for c in cols if "月份" in c or "日期" in c), cols[0])

            if not total_col and len(cols) > 1:
                total_col = cols[1]

            if total_col:
                for i in range(min(10, len(df))):
                    row = df.iloc[i]
                    try:
                        v = float(row[total_col])
                        result["total"] = round(v, 0)
                        result["period"] = str(row[date_col])
                        result["available"] = True
                        break
                    except (ValueError, TypeError):
                        continue
            print(f"[SHRZGM] total={result['total']}亿, period={result['period']}")
    except Exception as e:
        print(f"[SHRZGM] Failed: {e}")

    _ext_macro_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_lpr_rate() -> dict:
    """获取 LPR 贷款市场报价利率"""
    cache_key = "lpr"
    now = time.time()
    if cache_key in _ext_macro_cache and now - _ext_macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return _ext_macro_cache[cache_key]["data"]

    result = {"lpr_1y": None, "lpr_5y": None, "date": "", "available": False}
    try:
        import akshare as ak
        df = ak.macro_china_lpr()
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            cols = list(df.columns)
            lpr1_col = next((c for c in cols if "LPR1Y" in c or "1年" in c), None)
            lpr5_col = next((c for c in cols if "LPR5Y" in c or "5年" in c), None)
            date_col = next((c for c in cols if "TRADE_DATE" in c or "日期" in c), cols[0])

            if lpr1_col:
                try:
                    result["lpr_1y"] = round(float(latest[lpr1_col]), 2)
                except (ValueError, TypeError):
                    pass
            if lpr5_col:
                try:
                    result["lpr_5y"] = round(float(latest[lpr5_col]), 2)
                except (ValueError, TypeError):
                    pass
            result["date"] = str(latest[date_col])
            result["available"] = result["lpr_1y"] is not None
            print(f"[LPR] 1Y={result['lpr_1y']}%, 5Y={result['lpr_5y']}%, date={result['date']}")
    except Exception as e:
        print(f"[LPR] Failed: {e}")

    _ext_macro_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_market_activity() -> dict:
    """获取市场涨跌家数/赚钱效应"""
    cache_key = "activity"
    now = time.time()
    if cache_key in _ext_macro_cache and now - _ext_macro_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return _ext_macro_cache[cache_key]["data"]

    result = {"items": {}, "available": False}
    try:
        import akshare as ak
        df = ak.stock_market_activity_legu()
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                key = str(row.get("item", "")).strip()
                val = str(row.get("value", "")).strip()
                if key:
                    result["items"][key] = val
            result["available"] = len(result["items"]) > 0
            print(f"[ACTIVITY] {len(result['items'])} items: {list(result['items'].keys())[:5]}")
    except Exception as e:
        print(f"[ACTIVITY] Failed: {e}")

    _ext_macro_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_merrill_lynch_clock() -> dict:
    """美林时钟经济周期判断（基于 PMI + CPI 组合）
    复苏(PMI>50, CPI低) → 过热(PMI>50, CPI高) → 滞胀(PMI<50, CPI高) → 衰退(PMI<50, CPI低)
    """
    cache_key = "merrill_clock"
    now = time.time()
    if cache_key in _ext_macro_cache and now - _ext_macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return _ext_macro_cache[cache_key]["data"]

    result = {
        "cycle": "unknown", "label": "未知",
        "pmi": None, "cpi": None,
        "allocation": {"stock": 25, "bond": 25, "cash": 25, "gold": 25},
        "reasoning": "", "available": False,
    }

    try:
        from services.macro_data import get_macro_calendar
        macro = get_macro_calendar()
        for e in macro:
            name = e.get("name", "")
            val_str = str(e.get("value", "")).replace("%", "")
            try:
                val = float(val_str)
                if "PMI" in name:
                    result["pmi"] = val
                elif "CPI" in name:
                    result["cpi"] = val
            except (ValueError, TypeError):
                continue

        if result["pmi"] is not None and result["cpi"] is not None:
            pmi, cpi = result["pmi"], result["cpi"]
            result["available"] = True

            # CPI 阈值：2.5% 以上算"高通胀"（A 股历史中位数约 2%）
            cpi_high = cpi > 2.5

            if pmi > 50 and not cpi_high:
                result["cycle"] = "recovery"
                result["label"] = "复苏期"
                result["allocation"] = {"stock": 60, "bond": 20, "cash": 10, "gold": 10}
                result["reasoning"] = f"PMI={pmi}(扩张)+CPI={cpi}%(低通胀)→经济回暖但物价温和，股市最受益"
            elif pmi > 50 and cpi_high:
                result["cycle"] = "overheat"
                result["label"] = "过热期"
                result["allocation"] = {"stock": 40, "bond": 15, "cash": 15, "gold": 30}
                result["reasoning"] = f"PMI={pmi}(扩张)+CPI={cpi}%(高通胀)→经济过热，大宗/黄金受益，债券承压"
            elif pmi <= 50 and cpi_high:
                result["cycle"] = "stagflation"
                result["label"] = "滞胀期"
                result["allocation"] = {"stock": 20, "bond": 25, "cash": 35, "gold": 20}
                result["reasoning"] = f"PMI={pmi}(收缩)+CPI={cpi}%(高通胀)→经济放缓+物价上涨，现金为王"
            else:
                result["cycle"] = "recession"
                result["label"] = "衰退期"
                result["allocation"] = {"stock": 25, "bond": 45, "cash": 20, "gold": 10}
                result["reasoning"] = f"PMI={pmi}(收缩)+CPI={cpi}%(低通胀)→经济衰退，债券最受益（降息预期）"

            print(f"[CLOCK] {result['label']}: PMI={pmi}, CPI={cpi}%")
    except Exception as e:
        print(f"[CLOCK] Failed: {e}")

    _ext_macro_cache[cache_key] = {"data": result, "ts": now}
    return result
