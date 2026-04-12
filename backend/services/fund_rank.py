"""
钱袋子 — 基金排行数据
基金排行、动态收益率
"""
import time
from datetime import datetime
from config import FUND_RANK_CACHE_TTL

_fund_rank_cache = {}

def _load_fund_rank_data() -> dict:
    """加载基金排行数据（含各周期收益率），24小时缓存"""
    cache_key = "fund_rank_all"
    now = time.time()
    if cache_key in _fund_rank_cache and now - _fund_rank_cache[cache_key]["ts"] < FUND_RANK_CACHE_TTL:
        return _fund_rank_cache[cache_key]["data"]
    try:
        import akshare as ak
        df = ak.fund_open_fund_rank_em(symbol="全部")
        if df is not None and len(df) > 0:
            # 建立 code -> row 字典
            code_col = next((c for c in df.columns if "代码" in c), df.columns[0])
            data = {}
            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                data[code] = row
            _fund_rank_cache[cache_key] = {"data": data, "ts": now}
            print(f"[FUND_RANK] Loaded {len(data)} funds")
            return data
    except Exception as e:
        print(f"[FUND_RANK] Failed: {e}")
        import traceback; traceback.print_exc()
    return {}


def get_fund_dynamic_info(code: str) -> dict:
    """获取基金的动态收益率、排名等数据"""
    cache_key = f"fund_info_{code}"
    now = time.time()
    if cache_key in _fund_rank_cache and now - _fund_rank_cache[cache_key]["ts"] < FUND_RANK_CACHE_TTL:
        return _fund_rank_cache[cache_key]["data"]

    rank_data = _load_fund_rank_data()
    row = rank_data.get(code)
    if row is None:
        return {"code": code, "error": "未找到该基金"}

    def _safe_float(val):
        try:
            v = float(val)
            if isinstance(v, float) and not (v != v):  # not NaN
                return round(v, 2)
        except (ValueError, TypeError):
            pass
        return None

    def _find_col(cols, keywords):
        for kw in keywords:
            for c in cols:
                if kw in str(c):
                    return c
        return None

    cols = list(row.index) if hasattr(row, 'index') else []
    result = {
        "code": code,
        "name": str(row.get(_find_col(cols, ["简称", "名称"]) or cols[1], "")),
        "nav": _safe_float(row.get(_find_col(cols, ["单位净值"]), None)),
        "accNav": _safe_float(row.get(_find_col(cols, ["累计净值"]), None)),
        "dayChange": _safe_float(row.get(_find_col(cols, ["日增长率"]), None)),
        "returns": {
            "1w": _safe_float(row.get(_find_col(cols, ["近1周"]), None)),
            "1m": _safe_float(row.get(_find_col(cols, ["近1月"]), None)),
            "3m": _safe_float(row.get(_find_col(cols, ["近3月"]), None)),
            "6m": _safe_float(row.get(_find_col(cols, ["近6月"]), None)),
            "1y": _safe_float(row.get(_find_col(cols, ["近1年"]), None)),
            "2y": _safe_float(row.get(_find_col(cols, ["近2年"]), None)),
            "3y": _safe_float(row.get(_find_col(cols, ["近3年"]), None)),
            "ytd": _safe_float(row.get(_find_col(cols, ["今年来"]), None)),
            "since": _safe_float(row.get(_find_col(cols, ["成立来"]), None)),
        },
        "fee": str(row.get(_find_col(cols, ["手续费"]), "")),
        "updatedAt": datetime.now().strftime("%Y-%m-%d"),
        "source": "东方财富天天基金",
    }
    _fund_rank_cache[cache_key] = {"data": result, "ts": now}
    return result



