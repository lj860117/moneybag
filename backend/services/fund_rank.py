"""
钱袋子 — 基金排行数据
基金排行、动态收益率
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "fund_rank",
    "scope": "public",
    "input": [],
    "output": "fund_ranking",
    "cost": "cpu",
    "tags": ['基金排行', '收益率'],
    "description": "基金排行数据：全量基金多周期收益率排行",
    "layer": "data",
    "priority": 2,
}
import time
from datetime import datetime
from config import FUND_RANK_CACHE_TTL
from infra.cache import MemoryCache

_fund_rank_cache = MemoryCache(default_ttl=3600)

def _load_fund_rank_data() -> dict:
    """加载基金排行数据（含各周期收益率），24小时缓存"""
    cache_key = "fund_rank_all"
    now = time.time()
    cached = _fund_rank_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        from infra.data_source.market.stocks import get_fund_rank
        df = get_fund_rank(symbol="全部")
        if df is not None and len(df) > 0:
            # 建立 code -> row 字典
            code_col = next((c for c in df.columns if "代码" in c), df.columns[0])
            data = {}
            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                data[code] = row
            _fund_rank_cache.set(cache_key, data, ttl=FUND_RANK_CACHE_TTL)
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
    cached = _fund_rank_cache.get(cache_key)
    if cached is not None:
        return cached

    rank_data = _load_fund_rank_data()
    row = rank_data.get(code)
    if row is None:
        # AKShare 排行数据没有该基金（常见于QDII/新基金），尝试 Tushare 直查
        return _fallback_fund_info(code)

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
    _fund_rank_cache.set(cache_key, result)
    return result


def _fallback_fund_info(code: str) -> dict:
    """AKShare 排行没有该基金时，用 Tushare fund_nav 直查基本信息"""
    result = {"code": code, "name": code, "nav": None, "returns": {}, "fee": ""}
    try:
        from services.tushare_data import _call_tushare, is_configured
        if not is_configured():
            return result

        # 尝试多种 ts_code 格式
        for suffix in [".OF", ".SZ", ".SH"]:
            ts_code = code + suffix
            # 获取基金名称
            basic = _call_tushare("fund_basic", {"ts_code": ts_code}, "ts_code,name,fund_type")
            if basic:
                result["name"] = basic[0].get("name", code)
                result["fund_type"] = basic[0].get("fund_type", "")
                break

        # 获取最新净值
        for suffix in [".OF", ".SZ", ".SH"]:
            ts_code = code + suffix
            navs = _call_tushare("fund_nav", {"ts_code": ts_code, "limit": "5"},
                                 "ts_code,nav_date,unit_nav,accum_nav")
            if navs and len(navs) > 0:
                latest = navs[0]
                result["nav"] = float(latest.get("unit_nav") or latest.get("accum_nav") or 0)
                break

        result["source"] = "tushare_fallback"
    except Exception as e:
        print(f"[FUND_RANK] fallback for {code} failed: {e}")
    return result



