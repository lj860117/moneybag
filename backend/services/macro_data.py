"""
钱袋子 — 宏观经济数据
经济日历
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "macro_data",
    "scope": "public",
    "input": [],
    "output": "macro_calendar",
    "cost": "cpu",
    "tags": ['宏观', 'CPI', 'PMI', 'M2', 'PPI'],
    "description": "宏观经济日历：CPI/PMI/M2/PPI数据+趋势分析",
    "layer": "data",
    "priority": 2,
}
import time
from datetime import datetime, timedelta
from config import MACRO_CACHE_TTL, FACTOR_CACHE_TTL

_macro_cache = {}


def get_macro_calendar() -> list:
    """获取近期宏观经济事件（CPI/PMI/M2/PPI）"""
    cache_key = "macro_cal"
    now = time.time()
    if cache_key in _macro_cache and now - _macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return _macro_cache[cache_key]["data"]

    events = []
    try:
        import akshare as ak
        import math
        import re

        def _find_col(cols, keywords):
            """模糊匹配列名"""
            for kw in keywords:
                for c in cols:
                    if kw in str(c):
                        return c
            return None

        def _is_valid(v):
            """判断值是否有效（非 None、非 NaN）"""
            if v is None:
                return False
            try:
                if isinstance(v, float) and math.isnan(v):
                    return False
            except (TypeError, ValueError):
                pass
            return True

        def _get_first_valid(df, val_col, date_col, max_rows=10):
            """从头部向下找第一条有效值（数据倒序排列）"""
            for i in range(min(max_rows, len(df))):
                row = df.iloc[i]
                v = row[val_col]
                if _is_valid(v):
                    d = str(row[date_col]) if date_col else ""
                    return str(v), d
            return "", ""

        def _clean_date(raw_date: str) -> str:
            """统一日期格式：'2026年03月份' → '2026-03'"""
            m = re.match(r"(\d{4})\D*(\d{1,2})", raw_date)
            if m:
                return f"{m.group(1)}-{int(m.group(2)):02d}"
            return raw_date

        def _fmt_pct(val_str: str) -> str:
            """数值加 % 后缀"""
            try:
                float(val_str)
                return val_str + "%"
            except (ValueError, TypeError):
                return val_str

        # ---- CPI ----
        try:
            df = ak.macro_china_cpi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] CPI cols={list(df.columns)}, rows={len(df)}")
                val_col = _find_col(df.columns, ["全国-同比增长", "全国-同比", "同比增长"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or df.columns[0]
                if val_col:
                    v, d = _get_first_valid(df, val_col, date_col)
                    if v:
                        events.append({"name": "CPI 居民消费价格指数", "date": _clean_date(d), "value": _fmt_pct(v), "impact": "通胀指标，影响央行货币政策", "icon": "📊"})
                        print(f"[MACRO] CPI={v} date={d}")
        except Exception as e:
            print(f"[MACRO] CPI failed: {e}")
            import traceback; traceback.print_exc()

        # ---- PMI ----
        try:
            df = ak.macro_china_pmi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] PMI cols={list(df.columns)}, rows={len(df)}")
                val_col = _find_col(df.columns, ["制造业-指数", "制造业"]) or (df.columns[1] if len(df.columns) > 1 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or df.columns[0]
                if val_col:
                    v, d = _get_first_valid(df, val_col, date_col)
                    if v:
                        events.append({"name": "PMI 采购经理指数", "date": _clean_date(d), "value": v, "impact": "经济景气度指标，>50扩张、<50收缩", "icon": "🏭"})
                        print(f"[MACRO] PMI={v} date={d}")
        except Exception as e:
            print(f"[MACRO] PMI failed: {e}")

        # ---- M2 ----
        try:
            df = ak.macro_china_money_supply()
            if df is not None and len(df) > 0:
                print(f"[MACRO] M2 cols={list(df.columns)}, rows={len(df)}")
                val_col = _find_col(df.columns, ["货币和准货币(M2)-同比增长", "M2-同比", "M2同比"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or df.columns[0]
                if val_col:
                    v, d = _get_first_valid(df, val_col, date_col)
                    if v:
                        events.append({"name": "M2 广义货币供应量", "date": _clean_date(d), "value": _fmt_pct(v), "impact": "货币宽松/紧缩信号，影响市场流动性", "icon": "💵"})
                        print(f"[MACRO] M2={v} date={d}")
        except Exception as e:
            print(f"[MACRO] M2 failed: {e}")

        # ---- PPI ----
        try:
            df = ak.macro_china_ppi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] PPI cols={list(df.columns)}, rows={len(df)}")
                # 优先匹配"当月同比增长"，然后按列序号降级
                val_col = _find_col(df.columns, ["当月同比增长", "当月同比"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or df.columns[0]
                if val_col:
                    v, d = _get_first_valid(df, val_col, date_col)
                    if v:
                        events.append({"name": "PPI 工业生产者出厂价格指数", "date": _clean_date(d), "value": _fmt_pct(v), "impact": "上游价格指标，领先CPI反映通胀趋势", "icon": "🏭"})
                        print(f"[MACRO] PPI={v} date={d}")
                    else:
                        print(f"[MACRO] PPI: no valid value in first 10 rows")
                else:
                    print(f"[MACRO] PPI: no matching column in {list(df.columns)}")
        except Exception as e:
            print(f"[MACRO] PPI failed: {e}")
            import traceback; traceback.print_exc()

    except Exception as e:
        print(f"[MACRO] Fatal: {e}")

    if not events:
        events = [{"name": "宏观数据加载中", "date": "", "value": "", "impact": "", "icon": "📅"}]
    else:
        print(f"[MACRO] Total {len(events)} indicators loaded")

    _macro_cache[cache_key] = {"data": events, "ts": now}
    return events


# ============================================================
# V4.5 新因子数据层（借鉴幻方量化多因子体系）
# ============================================================

# --- 缓存 ---
factor_cache = {}



