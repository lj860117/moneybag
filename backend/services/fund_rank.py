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
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from config import FUND_RANK_CACHE_TTL, DATA_DIR
from infra.cache import MemoryCache

_fund_rank_cache = MemoryCache(default_ttl=3600)

# 排行榜归档目录（与 fund_rank_build.py 输出一致）
_RANK_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_ts_rank(file_path: Path = None) -> dict:
    """加载 fund_rank_ts.json（Tushare 精算版），返回 {code: rank_item}"""
    if file_path is None:
        file_path = _RANK_DATA_DIR / "fund_rank_ts.json"
    if not file_path.exists():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        ranks_all = data.get("ranks", {}).get("all", [])
        # 建立 code -> {rank, item} 映射
        result = {}
        for i, item in enumerate(ranks_all):
            code = item.get("code", "")
            if code:
                result[code] = {**item, "rank": i + 1, "total": len(ranks_all)}
        return result
    except Exception as e:
        print(f"[FUND_RANK] _load_ts_rank failed: {e}")
        return {}


def _find_prev_rank_file() -> Path | None:
    """找上一周的归档文件"""
    today = datetime.now()
    for days_back in range(6, 15):  # 往前 6~14 天找（上周同期）
        candidate_date = (today - timedelta(days=days_back)).strftime("%Y%m%d")
        candidate = _RANK_DATA_DIR / f"fund_rank_ts_{candidate_date}.json"
        if candidate.exists():
            return candidate
    return None


def get_holding_rank_compare(user_id: str) -> dict:
    """对比持仓基金在排行榜中的当前排名 vs 上周排名
    
    返回：
    {
      "generated_at": "...",
      "holdings": [
        {
          "code": "110011",
          "name": "易方达蓝筹精选",
          "current_rank": 58,
          "prev_rank": 45,
          "rank_change": -13,  # 负数 = 排名下滑
          "return_1y": 23.5,
          "score": 28.1,
          "in_top30": False,
          "alert": "排名从45下滑至58，已跌出TOP50，建议关注",
        },
        ...
      ],
      "summary": "5只持仓基金中，3只排名上升，1只下滑，1只进入TOP30"
    }
    """
    cache_key = f"holding_rank_compare_{user_id}"
    cached = _fund_rank_cache.get(cache_key)
    if cached is not None:
        return cached

    # 加载当前排行榜
    current_map = _load_ts_rank()
    if not current_map:
        return {"error": "排行榜数据不可用，请等待每周日22:00自动更新", "holdings": []}

    # 加载上周排行榜
    prev_file = _find_prev_rank_file()
    prev_map = _load_ts_rank(prev_file) if prev_file else {}
    has_prev = bool(prev_map)

    # 拉用户持仓
    try:
        from services.fund_monitor import load_fund_holdings
        my_funds = load_fund_holdings(user_id) or []
    except Exception:
        my_funds = []

    if not my_funds:
        return {"error": "暂无基金持仓记录", "holdings": []}

    total_in_rank = max(v.get("total", 1000) for v in current_map.values()) if current_map else 1000

    results = []
    for fund in my_funds:
        code = fund.get("code", "")
        name = fund.get("name", code)
        if not code:
            continue

        cur_info = current_map.get(code)
        prev_info = prev_map.get(code) if has_prev else None

        cur_rank = cur_info.get("rank") if cur_info else None
        prev_rank = prev_info.get("rank") if prev_info else None
        rank_change = None
        if cur_rank is not None and prev_rank is not None:
            rank_change = cur_rank - prev_rank  # 正数 = 排名下滑（数字越大越靠后）

        in_top30 = cur_rank is not None and cur_rank <= 30
        in_top100 = cur_rank is not None and cur_rank <= 100

        # 告警逻辑
        alert = None
        if cur_rank is None:
            alert = "⚠️ 该基金未在本周榜单中（可能为新基金或QDII）"
        elif rank_change is not None and rank_change > 50:
            alert = f"📉 排名大幅下滑 {rank_change} 位（{prev_rank}→{cur_rank}），建议关注"
        elif cur_rank > 200 and (prev_rank is None or prev_rank <= 200):
            alert = f"⚠️ 已跌出TOP200（当前第{cur_rank}名），建议评估是否继续持有"

        entry = {
            "code": code,
            "name": name,
            "current_rank": cur_rank,
            "prev_rank": prev_rank,
            "rank_change": rank_change,
            "total_in_rank": total_in_rank,
            "return_1y": cur_info.get("return_1y") if cur_info else None,
            "return_3y": cur_info.get("return_3y") if cur_info else None,
            "score": cur_info.get("score") if cur_info else None,
            "in_top30": in_top30,
            "in_top100": in_top100,
            "alert": alert,
            "rank_updated": current_map.get(code, {}).get("generated_at", ""),
        }
        results.append(entry)

    # 生成摘要
    ranked = [r for r in results if r["current_rank"] is not None]
    up_count = sum(1 for r in ranked if r["rank_change"] is not None and r["rank_change"] < 0)
    down_count = sum(1 for r in ranked if r["rank_change"] is not None and r["rank_change"] > 0)
    top30_count = sum(1 for r in ranked if r["in_top30"])

    summary_parts = []
    if len(results) > 0:
        summary_parts.append(f"{len(results)}只持仓基金")
    if has_prev and (up_count or down_count):
        if up_count:
            summary_parts.append(f"{up_count}只排名上升")
        if down_count:
            summary_parts.append(f"{down_count}只排名下滑")
    if top30_count:
        summary_parts.append(f"{top30_count}只在TOP30")
    if not has_prev:
        summary_parts.append("（首次生成，暂无上周数据对比）")

    result = {
        "generated_at": datetime.now().isoformat(),
        "has_comparison": has_prev,
        "prev_rank_file": str(prev_file.name) if prev_file else None,
        "holdings": results,
        "summary": "，".join(summary_parts) if summary_parts else "暂无数据",
        "alerts": [r["alert"] for r in results if r["alert"]],
    }

    _fund_rank_cache.set(cache_key, result, ttl=3600)
    return result

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
            print(f"[FUND_RANK] Loaded {len(data)} funds (AKShare)")
            return data
    except Exception as e:
        print(f"[FUND_RANK] AKShare failed: {e}")
    
    # v9.5.123: finshare fallback(多源故障切换,更稳定)
    try:
        import finshare as fs
        # finshare的fund接口获取基金列表+净值
        fund_list = fs.get_fund_list()
        if fund_list is not None and len(fund_list) > 0:
            import pandas as pd
            code_col = next((c for c in fund_list.columns if "代码" in str(c) or "code" in str(c).lower()), fund_list.columns[0])
            data = {}
            for _, row in fund_list.iterrows():
                code = str(row[code_col]).strip()
                data[code] = row
            _fund_rank_cache.set(cache_key, data, ttl=FUND_RANK_CACHE_TTL)
            print(f"[FUND_RANK] Loaded {len(data)} funds (finshare fallback)")
            return data
    except Exception as e:
        print(f"[FUND_RANK] finshare fallback also failed: {e}")
    
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
    
    # 尝试用 Tushare 增强历史收益率（更精确）
    _augment_with_tushare_returns(result, code)
    
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
    
    # 尝试用 Tushare 获取历史收益率
    _augment_with_tushare_returns(result, code)
    
    return result


def _augment_with_tushare_returns(result: dict, code: str):
    """
    用 Tushare 历史收益率增强 result
    优先使用 Tushare 数据（更精确），失败则保持原数据
    """
    try:
        from services.fund_history_returns import get_fund_history_returns
        ts_result = get_fund_history_returns(code)
        
        if ts_result and ts_result.get('date'):
            returns = result.setdefault('returns', {})
            
            # 映射 Tushare 数据到 returns
            if ts_result.get('1m') is not None:
                returns['1m'] = ts_result['1m']
            if ts_result.get('3m') is not None:
                returns['3m'] = ts_result['3m']
            if ts_result.get('6m') is not None:
                returns['6m'] = ts_result['6m']
            if ts_result.get('1y') is not None:
                returns['1y'] = ts_result['1y']
            if ts_result.get('3y') is not None:
                returns['3y'] = ts_result['3y']
            
            # 更新数据源标注
            old_source = result.get('source', '')
            if old_source:
                result['source'] = f"{old_source} + Tushare"
            else:
                result['source'] = "Tushare"
            result['returns_date'] = ts_result.get('date')
            
            print(f"  ✅ Tushare 增强 {code}: {ts_result.get('date')}")
    except Exception as e:
        # 静默失败，使用原始数据
        pass



