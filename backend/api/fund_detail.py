"""
基金详情 + 经理规模战绩 + 政策受益映射 API
v9.3.4 新增
v9.5.100 升级：详情缓存改为文件持久化（跨重启）
"""
from __future__ import annotations

import time
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from config import DATA_DIR

router = APIRouter()

# v9.5.100: 文件缓存目录（跨进程跨重启）
_DETAIL_CACHE_DIR = os.path.join(str(DATA_DIR.resolve()), "_cache", "fund_detail")
try:
    os.makedirs(_DETAIL_CACHE_DIR, exist_ok=True)
except Exception:
    pass

# 简易内存缓存
_detail_cache: dict = {}
_CACHE_TTL = 3600 * 24  # v9.5.121: 放宽到24h（基金经理/规模/持仓季度才变）

# 购买数据全局缓存（每天只拉一次，26000条数据）
_purchase_df_cache = {"df": None, "t": 0}
_PURCHASE_TTL = 3600 * 24  # 24小时

def _get_purchase_df():
    """懒加载天天基金购买数据（24小时缓存）

    v9.9.x: 接入 ak_call() 超时保护。实测 fund_purchase_em 全市场
    ~2.7万条数据耗时约 15s（已逼近 ak_call 默认 15s 超时），故单独
    传 timeout=25s 留余量；网络异常挂死时 25s 后放弃而不是无限等待。
    """
    if _purchase_df_cache["df"] is not None and time.time() - _purchase_df_cache["t"] < _PURCHASE_TTL:
        return _purchase_df_cache["df"]
    try:
        import akshare as ak
        from services.utils import ak_call
        df = ak_call(ak.fund_purchase_em, timeout=25)
        if df is not None:
            _purchase_df_cache["df"] = df
            _purchase_df_cache["t"] = time.time()
        return df
    except Exception:
        return None

def _get_fund_purchase_info(code: str) -> dict:
    """获取基金申购状态、限额、费率"""
    try:
        df = _get_purchase_df()
        if df is None or len(df) == 0:
            return {"available": False}
        # 按代码查找（第2列是代码）
        row = df[df.iloc[:, 1].astype(str) == str(code)]
        if len(row) == 0:
            return {"available": False, "reason": "未找到购买信息"}
        r = row.iloc[0]
        cols = list(df.columns)
        def gc(keywords):
            for c in cols:
                if any(k in c for k in keywords):
                    return r.get(c)
            return None
        status = gc(["申购状态", "申购"])
        redeem = gc(["赎回状态", "赎回"])
        min_buy = gc(["起购金额", "最低", "起购"])
        limit = gc(["申购限额", "限额", "每日"])
        fee = gc(["手续费", "费率"])
        return {
            "available": True,
            "purchase_status": str(status) if status else "未知",
            "redeem_status": str(redeem) if redeem else "未知",
            "min_buy": float(min_buy) if min_buy and str(min_buy) != 'nan' else None,
            "daily_limit": float(limit) if limit and str(limit) != 'nan' else None,
            "fee_rate": float(fee) if fee and str(fee) != 'nan' else None,
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


# v9.5.39 P6: 分红/拆分检测（1 年内）
_dividend_cache = {}  # code → (info, ts)
def _get_fund_dividend_recent(code: str) -> dict:
    """检测基金 1 年内是否有分红/拆分

    AKShare 提供两个相关 indicator:
    - 分红送配详情：年份 / 权益登记日 / 除息日 / 每份分红 / 分红发放日
    - 拆分详情：年份 / 拆分折算日 / 拆分类型 / 拆分折算比例
    """
    import time
    now_ts = time.time()
    cached = _dividend_cache.get(code)
    if cached and (now_ts - cached[1]) < 86400:
        return cached[0]

    result = {"has_recent": False, "events": [], "has_history": False}
    try:
        import akshare as ak
        from services.utils import ak_call
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=365)
        history_all = []  # 历史全量事件

        # --- 1. 分红 ---
        try:
            df_div = ak_call(ak.fund_open_fund_info_em, symbol=code, indicator="分红送配详情")
            if df_div is not None and len(df_div) > 0:
                date_col = next((c for c in df_div.columns if "除息" in c or "登记" in c), None)
                ratio_col = next((c for c in df_div.columns if "分红" in c or "派息" in c), None)
                if date_col:
                    for _, row in df_div.iterrows():
                        try:
                            dstr = str(row[date_col]).split()[0]
                            d = datetime.strptime(dstr, "%Y-%m-%d")
                            ev = {"type": "dividend", "date": dstr, "detail": str(row[ratio_col]) if ratio_col else ""}
                            history_all.append(ev)
                            if d >= cutoff:
                                result["events"].append(ev)
                        except Exception:
                            continue
        except Exception:
            pass

        # --- 2. 拆分 ---
        try:
            df_split = ak_call(ak.fund_open_fund_info_em, symbol=code, indicator="拆分详情")
            if df_split is not None and len(df_split) > 0:
                date_col = next((c for c in df_split.columns if "折算" in c or "日" in c), None)
                ratio_col = next((c for c in df_split.columns if "比例" in c), None)
                type_col = next((c for c in df_split.columns if "类型" in c), None)
                if date_col:
                    for _, row in df_split.iterrows():
                        try:
                            dstr = str(row[date_col]).split()[0]
                            d = datetime.strptime(dstr, "%Y-%m-%d")
                            ev = {"type": "split", "date": dstr, "detail": (str(row[type_col]) if type_col else "") + " " + (str(row[ratio_col]) if ratio_col else "")}
                            history_all.append(ev)
                            if d >= cutoff:
                                result["events"].append(ev)
                        except Exception:
                            continue
        except Exception:
            pass

        # 历史全量（用于成本校验提醒）
        if history_all:
            history_all.sort(key=lambda x: x["date"], reverse=True)
            result["has_history"] = True
            result["history_count"] = len(history_all)
            result["history_latest_date"] = history_all[0]["date"]
            result["history_latest_type"] = history_all[0]["type"]
            type_zh = "分红" if history_all[0]["type"] == "dividend" else "拆分"
            result["history_label"] = f"{history_all[0]['date'][:7]} {type_zh}"

        if result["events"]:
            result["events"].sort(key=lambda x: x["date"], reverse=True)
            result["has_recent"] = True
            latest = result["events"][0]
            result["latest_date"] = latest["date"]
            result["latest_type"] = latest["type"]
            type_zh = "分红" if latest["type"] == "dividend" else "拆分"
            result["label"] = f"{latest['date'][:7]} {type_zh}"
            result["count_1y"] = len(result["events"])

    except Exception as e:
        result["error"] = str(e)[:80]

    _dividend_cache[code] = (result, now_ts)
    return result


# v9.5.89: 基金经理换届检测
# v9.5.108: 文件持久化（跨重启）
_MANAGER_CACHE_FILE = os.path.join(os.environ.get("DATA_DIR", "data"), "_cache", "_manager_change_cache.json")
_MANAGER_CACHE_TTL = 3600 * 24   # 24 小时


def _load_manager_cache() -> dict:
    try:
        if os.path.exists(_MANAGER_CACHE_FILE):
            with open(_MANAGER_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now_ts = time.time()
            return {k: tuple(v) for k, v in data.items() if (now_ts - v[1]) < _MANAGER_CACHE_TTL}
    except Exception:
        pass
    return {}


def _save_manager_cache():
    try:
        os.makedirs(os.path.dirname(_MANAGER_CACHE_FILE), exist_ok=True)
        with open(_MANAGER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in _manager_change_cache.items()}, f, ensure_ascii=False)
    except Exception:
        pass


_manager_change_cache: dict = _load_manager_cache()  # 启动恢复

def _get_fund_manager_change(code: str) -> dict:
    """检测近 6 个月内基金经理是否发生变更。
    
    使用天天基金 EM API 拉取基金经理历史，判断最近任职起始日期。
    返回：{has_change: bool, manager_name: str, start_date: str, days_since: int, warn: str}
    """
    import time as _t
    now_ts = _t.time()
    cached = _manager_change_cache.get(code)
    if cached and (now_ts - cached[1]) < _MANAGER_CACHE_TTL:
        return cached[0]

    result = {"has_change": False, "manager_name": "", "start_date": "", "days_since": None}
    try:
        import requests, re, json as _js
        from datetime import datetime, timedelta
        url = "https://api.fund.eastmoney.com/f10/jjjl"
        headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params={"callback": "x", "symbol": code, "pageIndex": 1, "pageSize": 1},
                         headers=headers, timeout=8)
        body = re.sub(r"^x\(", "", r.text.strip()).rstrip(")")
        data = _js.loads(body)
        managers = data.get("Data", {}).get("Data", [])
        if managers:
            latest = managers[0]
            # 字段：基金经理姓名 / 任职起始日期
            name = latest.get("MANAGERNAME") or latest.get("BASICINFO", {}).get("NAME", "")
            start_str = latest.get("BEGINDATE", "")[:10]
            if start_str:
                try:
                    start_dt = datetime.strptime(start_str, "%Y-%m-%d")
                    days_since = (datetime.now() - start_dt).days
                    result["manager_name"] = name
                    result["start_date"] = start_str
                    result["days_since"] = days_since
                    if days_since <= 180:
                        result["has_change"] = True
                        result["warn"] = f"⚠️ 基金经理{days_since}天前变更为 {name or '新任'}，历史业绩参考价值降低"
                    else:
                        result["current_manager"] = f"{name}（任职{days_since//30}个月）"
                except Exception:
                    pass
    except Exception as e:
        result["error"] = str(e)[:60]

    _manager_change_cache[code] = (result, now_ts)
    _save_manager_cache()  # v9.5.108: 写文件持久化
    return result


def _get_nav_history_cached(code: str, days: int = 365) -> list:
    """v9.5.123: 拉取净值历史(天天基金) + 本地文件缓存(7天有效)
    
    避免每次fund_detail都HTTP拉20页净值,被限流后返空。
    缓存文件: DATA_DIR/_cache/nav_history_{code}.json
    """
    cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / f"nav_history_{code}.json"
    
    # 读缓存(7天有效)
    if cache_fp.exists():
        try:
            age = time.time() - cache_fp.stat().st_mtime
            if age < 604800:  # 7天
                data = json.loads(cache_fp.read_text(encoding="utf-8"))
                if len(data) > 20:
                    return data
        except Exception:
            pass
    
    # 从天天基金API拉取
    all_navs = []
    try:
        import requests, re as _re_nav
        headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        for page in range(1, 20):  # ~400条≈近1年
            r = requests.get("https://api.fund.eastmoney.com/f10/lsjz",
                params={"callback": "x", "fundCode": code, "pageIndex": page, "pageSize": 20},
                headers=headers, timeout=8)
            body = _re_nav.sub(r"^x\(", "", r.text.strip()).rstrip(")")
            items = json.loads(body).get("Data", {}).get("LSJZList", [])
            if not items:
                break
            for item in items:
                nav_val = item.get("DWJZ")
                if nav_val:
                    try:
                        all_navs.append(float(nav_val))
                    except (ValueError, TypeError):
                        pass
            time.sleep(0.2)  # 限流保护
    except Exception:
        pass
    
    if all_navs:
        navs_ordered = list(reversed(all_navs))  # 按时间正序
        # 写缓存
        try:
            cache_fp.parent.mkdir(parents=True, exist_ok=True)
            cache_fp.write_text(json.dumps(navs_ordered), encoding="utf-8")
        except Exception:
            pass
        return navs_ordered
    
    return []


def _get_cached(key: str, allow_stale=False):
    # 1) 内存缓存（TTL 内直接返回）
    entry = _detail_cache.get(key)
    if entry and time.time() - entry["t"] < _CACHE_TTL:
        return entry["v"]
    # 2) 文件缓存
    try:
        path = os.path.join(_DETAIL_CACHE_DIR, f"{key}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                fc = json.load(f)
            age = time.time() - fc.get("t", 0)
            if age < _CACHE_TTL:
                _detail_cache[key] = fc  # 回填内存
                return fc["v"]
            # v9.5.121: stale-while-revalidate — 过期但 <72h，先返旧数据（后台会定时预热）
            if allow_stale and age < 259200 and fc.get("v"):
                _detail_cache[key] = fc  # 也回填内存（虽然过期）
                return fc["v"]
    except Exception:
        pass
    return None


def _set_cached(key: str, val):
    rec = {"v": val, "t": time.time()}
    _detail_cache[key] = rec
    # v9.5.100: 同步写入文件
    try:
        path = os.path.join(_DETAIL_CACHE_DIR, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception:
        pass


def _resolve_stock_names(symbols: list[str]) -> dict[str, dict]:
    """将股票代码列表解析为 {symbol: {"name": ..., "industry": ...}} 映射"""
    if not symbols:
        return {}
    cache_key = "stock_info_map"
    info_map = _get_cached(cache_key) or {}

    # 找出缓存中缺失的代码
    missing = [s for s in symbols if s and s not in info_map]
    if not missing:
        return {s: info_map.get(s, {"name": "", "industry": ""}) for s in symbols}

    # Tushare stock_basic 批量查（含行业）
    try:
        from services.tushare_data import _call_tushare
        rows = _call_tushare(
            "stock_basic",
            {"exchange": "", "list_status": "L"},
            "ts_code,name,industry"
        )
        if rows:
            for r in rows:
                ts_code = r.get("ts_code", "")
                info_map[ts_code] = {
                    "name": r.get("name", ""),
                    "industry": r.get("industry", ""),
                }
            _set_cached(cache_key, info_map)
    except Exception:
        pass

    # 还有缺失的尝试单个查询
    for s in missing:
        if s not in info_map:
            try:
                from services.tushare_data import _call_tushare
                rows = _call_tushare("stock_basic", {"ts_code": s}, "ts_code,name,industry")
                if rows:
                    info_map[s] = {"name": rows[0].get("name", ""), "industry": rows[0].get("industry", "")}
            except Exception:
                info_map[s] = {"name": "", "industry": ""}

    _set_cached(cache_key, info_map)
    return {s: info_map.get(s, {"name": "", "industry": ""}) for s in symbols}


# ──────────────────────────────────────────────────────────
# Phase 1: 基金详情（经理 + 基本信息 + 持仓）
# ──────────────────────────────────────────────────────────
@router.get("/api/fund/detail/{code}")
def fund_detail(code: str, userId: str = ""):
    """基金完整详情：基本信息 + 经理 + 持仓 + 收益

    v9.5.121: stale-while-revalidate — 缓存过期时先返旧数据（最多72h），
    后台 cache_warmer 每天刷新。用户永远不需要等。

    v9.8.7: 新增 userId 参数 — 传值时补充持仓决策增强数据（合并 detail+holdings/detail 为单次调用）。
    """
    # v9.8.7/v9.9.3: 带 userId 时先查用户缓存；如果用户并未持有该基金，则应复用共享详情缓存，
    # 否则长持榜里的非持仓基金会重复走一次慢路径，点击弹窗容易超时。
    shared_cache_key = f"fund_detail_{code}"
    user_cache_key = f"{shared_cache_key}_{userId}" if userId else shared_cache_key

    if userId:
        cached = _get_cached(user_cache_key, allow_stale=True)
        if cached:
            return cached
        shared_cached = _get_cached(shared_cache_key, allow_stale=True)
        if shared_cached:
            enriched_cached = _enrich_detail_with_holding(dict(shared_cached), code, userId)
            if enriched_cached.get("holding_relation") == "🔵 已持仓":
                _set_cached(user_cache_key, enriched_cached)
                return enriched_cached
            return shared_cached
    else:
        cached = _get_cached(shared_cache_key, allow_stale=True)
        if cached:
            return cached

    from services.tushare_data import get_fund_manager, get_fund_portfolio, get_fund_share
    from services.fund_rank import get_fund_dynamic_info

    # 基础信息（收益率/费率/净值）
    info = get_fund_dynamic_info(code)

    # 基金经理
    mgr_data = get_fund_manager(code)
    manager = None
    if mgr_data.get("available") and mgr_data.get("managers"):
        m = mgr_data["managers"][0]
        begin = m.get("begin_date", "")
        tenure_years = 0
        if begin:
            try:
                bd = datetime.strptime(begin, "%Y%m%d")
                tenure_years = round((datetime.now() - bd).days / 365.25, 1)
            except Exception:
                pass
        manager = {
            "name": m.get("name", "未知"),
            "gender": m.get("gender", ""),
            "begin_date": begin,
            "tenure_years": tenure_years,
            "resume": (m.get("resume") or "")[:200],
        }

    # 基金规模（份额 × 净值）
    scale_billion = None
    # 尝试多种 ts_code 格式
    ts_code_candidates = [
        code if "." in code else f"{code}.OF",
        f"{code}.SZ",
        f"{code}.SH",
    ]
    for ts_code in ts_code_candidates:
        share_data = get_fund_share(ts_code, days=10)
        if share_data.get("available"):
            break
    if share_data.get("available") and info.get("nav"):
        shares_yi = share_data.get("shares_latest", 0)  # 亿份
        nav = info["nav"]
        scale_billion = round(shares_yi * nav, 2)  # 亿份 × 元/份 = 亿元

    # AKShare 兜底：fund_individual_basic_info_xq 拿规模 + 补充经理/类型
    # v9.5.125: 扩展触发条件 — 不只 manager is None，还包括 tenure_years=0 / resume 空的情况
    _need_ak = (scale_billion is None or manager is None or
                (manager and (not manager.get("tenure_years") or not manager.get("resume"))))
    ak_extra = {}
    if _need_ak:
        try:
            from services.tushare_data import get_fund_extra_info_ak
            ak_extra = get_fund_extra_info_ak(code)
            if ak_extra:
                # 解析规模
                if scale_billion is None and ak_extra.get('最新规模'):
                    scale_str = str(ak_extra['最新规模']).replace('亿', '').strip()
                    try:
                        scale_billion = round(float(scale_str), 2)
                    except (ValueError, TypeError):
                        pass
                # 补充经理信息（manager is None）
                if manager is None and ak_extra.get('基金经理'):
                    mgr_names = str(ak_extra['基金经理']).split()
                    manager = {
                        "name": mgr_names[0] if mgr_names else "未知",
                        "gender": "",
                        "begin_date": "",
                        "tenure_years": 0,
                        "resume": "",
                    }
                # v9.5.127: 任期/简历统一在 result 构建前兜底处理，这里仅补充经理名字
        except Exception:
            pass

    # 持仓明细 + v9.5.123: 季度变动(对比上一期,看经理加减仓)
    portfolio_data = get_fund_portfolio(code)
    top_holdings = []
    portfolio_changes = []  # 新增/退出/加仓/减仓
    if portfolio_data.get("available"):
        top_holdings = portfolio_data.get("top_holdings", [])[:5]
        # 尝试获取上一期持仓做对比
        prev_holdings = portfolio_data.get("prev_holdings", [])
        if prev_holdings and top_holdings:
            cur_symbols = {h.get("symbol", "") for h in top_holdings}
            prev_symbols = {h.get("symbol", "") for h in prev_holdings}
            # 新增持仓
            for s in cur_symbols - prev_symbols:
                if s:
                    portfolio_changes.append({"symbol": s, "action": "新增", "emoji": "🆕"})
            # 退出持仓
            for s in (prev_symbols - cur_symbols):
                if s:
                    portfolio_changes.append({"symbol": s, "action": "退出", "emoji": "🚪"})

    # 解析持仓股票名称 + 行业
    holding_symbols = [h.get("symbol", "") for h in top_holdings if h.get("symbol")]
    stock_info = _resolve_stock_names(holding_symbols)
    # 给变动也补名称
    for pc in portfolio_changes:
        info = stock_info.get(pc["symbol"], {})
        pc["name"] = info.get("name", pc["symbol"])

    # 从持仓行业推导经理投资偏好
    industries = [stock_info.get(s, {}).get("industry", "") for s in holding_symbols]
    industries = [i for i in industries if i]
    # 统计行业频次，取前3个
    from collections import Counter
    industry_counter = Counter(industries)
    manager_focus = [ind for ind, _ in industry_counter.most_common(3)]

    # 写入经理信息
    if manager:
        manager["focus_industries"] = manager_focus

    # 购买限制信息（从天天基金申购数据）
    purchase_info = _get_fund_purchase_info(code)

    # v9.5.39 P6: 分红/拆分检测（1 年内）
    dividend_info = _get_fund_dividend_recent(code)

    # v9.5.123: 最大回撤 + 同类排名 + 成立以来年化 + 夏普/Sortino/Alpha
    max_drawdown = None
    category_rank = None
    annual_since = None
    sharpe = None
    sortino = None
    alpha_annualized = None
    try:
        # 最大回撤: 从净值历史计算(近1年)
        # 优先Tushare, fallback天天基金API
        navs_for_dd = []
        try:
            from services.tushare_data import get_fund_nav, is_configured
            if is_configured():
                nav_data = get_fund_nav(code, days=365)
                if nav_data and nav_data.get("available") and nav_data.get("navs"):
                    navs_for_dd = [float(n.get("unit_nav", 0)) for n in nav_data["navs"] if n.get("unit_nav")]
        except Exception:
            pass
        
        # Tushare数据不足60条时用天天基金补充(60条以上才能算夏普/Sortino)
        if len(navs_for_dd) < 60:
            cached_navs = _get_nav_history_cached(code)
            if len(cached_navs) > len(navs_for_dd):
                navs_for_dd = cached_navs
        
        if len(navs_for_dd) > 20:
            peak = navs_for_dd[0]
            max_dd = 0
            for n in navs_for_dd:
                if n > peak:
                    peak = n
                dd = (peak - n) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            max_drawdown = round(max_dd, 1)
            
            # v9.5.123 P3-4: 夏普+Sortino+Alpha(用同一份净值数据)
            if len(navs_for_dd) >= 60:
                import math
                rf = 0.015  # 无风险利率1.5%(货币基金水平)
                rf_daily = rf / 252
                
                # 日收益率序列
                daily_returns = [(navs_for_dd[i] - navs_for_dd[i-1]) / navs_for_dd[i-1] 
                                for i in range(1, len(navs_for_dd)) if navs_for_dd[i-1] > 0]
                if daily_returns:
                    avg_daily = sum(daily_returns) / len(daily_returns)
                    std_daily = math.sqrt(sum((r - avg_daily)**2 for r in daily_returns) / len(daily_returns))
                    
                    # 夏普比率
                    if std_daily > 0:
                        ann_return = avg_daily * 252
                        ann_vol = std_daily * math.sqrt(252)
                        sharpe = round((ann_return - rf) / ann_vol, 2)
                    
                    # Sortino比率(只用下行波动率)
                    downside_returns = [r for r in daily_returns if r < rf_daily]
                    if len(downside_returns) > 10:
                        downside_dev = math.sqrt(sum((r - rf_daily)**2 for r in downside_returns) / len(downside_returns))
                        if downside_dev > 0:
                            ann_downside = downside_dev * math.sqrt(252)
                            sortino = round((ann_return - rf) / ann_downside, 2)
                    
                    # 阿尔法系数(vs沪深300基准)
                    try:
                        # 拉沪深300同期净值做基准
                        bench_navs = _get_nav_history_cached("110020")  # 沪深300ETF联接
                        if len(bench_navs) >= len(navs_for_dd) * 0.8:
                            # 截取同等长度
                            bn = bench_navs[-len(daily_returns)-1:]
                            bench_returns = [(bn[i] - bn[i-1]) / bn[i-1] 
                                           for i in range(1, min(len(bn), len(daily_returns)+1)) if bn[i-1] > 0]
                            if len(bench_returns) >= 30:
                                # Alpha = 基金年化收益 - Beta × 基准年化收益
                                avg_bench = sum(bench_returns[:len(daily_returns)]) / len(bench_returns[:len(daily_returns)])
                                ann_bench = avg_bench * 252
                                # Beta = Cov(fund, bench) / Var(bench)
                                n = min(len(daily_returns), len(bench_returns))
                                cov_sum = sum((daily_returns[i] - avg_daily) * (bench_returns[i] - avg_bench) for i in range(n))
                                var_bench = sum((bench_returns[i] - avg_bench)**2 for i in range(n))
                                if var_bench > 0:
                                    beta = cov_sum / var_bench
                                    alpha = round((ann_return - rf) - beta * (ann_bench - rf), 4)
                                    alpha_annualized = round(alpha * 100, 2)  # 转为百分比
                    except Exception:
                        pass
    except Exception:
        pass
    
    try:
        # 同类排名: 从天天基金排行数据计算百分位
        from services.fund_rank import _load_fund_rank_data
        rank_data = _load_fund_rank_data()
        if rank_data and code in rank_data:
            # 计算在所有基金中近1年收益的排名百分位
            all_r1y = []
            for c, row in rank_data.items():
                cols = list(row.index) if hasattr(row, 'index') else []
                for col in cols:
                    if "近1年" in str(col):
                        try:
                            v = float(row[col])
                            if v == v:  # not NaN
                                all_r1y.append((c, v))
                        except (ValueError, TypeError):
                            pass
                        break
            if all_r1y:
                all_r1y.sort(key=lambda x: x[1], reverse=True)
                my_idx = next((i for i, (c, _) in enumerate(all_r1y) if c == code), -1)
                if my_idx >= 0:
                    pct = round((1 - my_idx / len(all_r1y)) * 100, 1)
                    category_rank = {"percentile": pct, "rank": my_idx + 1, "total": len(all_r1y)}
    except Exception:
        pass
    
    try:
        # 成立以来年化: 用成立以来总收益 / 年数
        returns = info.get("returns", {})
        since_total = returns.get("since")
        founded_str = ak_extra.get("成立时间", "")
        if since_total and founded_str:
            from datetime import datetime as _dt
            founded_date = _dt.strptime(founded_str[:10], "%Y-%m-%d") if "-" in founded_str else _dt.strptime(founded_str[:8], "%Y%m%d")
            years = max((datetime.now() - founded_date).days / 365.25, 1)
            annual_since = round(since_total / years, 1)
    except Exception:
        pass

    # v9.5.124: 8维走势预估(含完整维度分解)
    trend_data = {}
    try:
        import sys as _sys_trend
        _sig_mod = _sys_trend.modules.get("api.signals")
        if _sig_mod is None:
            import importlib
            _sig_mod = importlib.import_module("api.signals")
        _enrich_fn = getattr(_sig_mod, "_enrich_trend_forecast", None)
        if _enrich_fn:
            _trend_tmp = [{"code": code, "name": info.get("name", code), "returns": info.get("returns", {})}]
            _enrich_fn(_trend_tmp, include_dimensions=True)
            if _trend_tmp[0].get("trend_label"):
                trend_data = {
                    "trend_label": _trend_tmp[0].get("trend_label"),
                    "trend_direction": _trend_tmp[0].get("trend_direction"),
                    "trend_score": _trend_tmp[0].get("trend_score"),
                    "trend_confidence": _trend_tmp[0].get("trend_confidence"),
                    "trend_reason": _trend_tmp[0].get("trend_reason"),
                    "trend_dimensions": _trend_tmp[0].get("trend_dimensions"),
                }
    except Exception:
        pass

    # v9.5.127: 统一任期估算 — 如果 manager 仍然没有任期，用成立时间 / Tushare list_date 兜底
    # 不依赖 ak_extra（可能为空），直接用 ts_rank_map 或 ak_extra 的成立时间
    if manager and not manager.get("tenure_years"):
        _founded_str = ak_extra.get("成立时间", "")
        # fallback: 从 Tushare ts_rank_map 拿 list_date
        if not _founded_str:
            _ts_info = _load_ts_rank_map().get(code, {})
            _founded_str = _ts_info.get("list_date", "")
        if _founded_str:
            try:
                _fmt = "%Y-%m-%d" if "-" in str(_founded_str) else "%Y%m%d"
                _fd = datetime.strptime(str(_founded_str)[:10], _fmt)
                manager["tenure_years"] = round((datetime.now() - _fd).days / 365.25, 1)
                manager["begin_date"] = str(_founded_str).replace("-", "")[:8]
                manager["tenure_note"] = "estimate"
            except Exception:
                pass
        # 投资策略作为简历
        if not manager.get("resume"):
            strategy = ak_extra.get("投资策略", "") or ak_extra.get("投资目标", "")
            if strategy:
                manager["resume"] = str(strategy)[:200]

    result = {
        "code": code,
        "name": info.get("name", code),
        "nav": info.get("nav"),
        "fee": info.get("fee", ""),
        "scale_billion": scale_billion,
        "fund_type": ak_extra.get("基金类型", ""),
        "founded": ak_extra.get("成立时间", ""),
        "company": ak_extra.get("基金公司", ""),
        "returns": info.get("returns", {}),
        "max_drawdown": max_drawdown,         # v9.5.123: 近1年最大回撤%
        "category_rank": category_rank,       # v9.5.123: 同类排名百分位
        "annual_since_inception": annual_since,  # v9.5.123: 成立以来年化%
        "sharpe_ratio": sharpe,                   # v9.5.123 P3-4: 夏普比率
        "sortino_ratio": sortino,                 # v9.5.123: Sortino(只算下行风险)
        "alpha_pct": alpha_annualized,            # v9.5.123: 年化Alpha%(vs沪深300)
        "manager": manager,
        "purchase": purchase_info,
        "dividend": dividend_info,  # v9.5.39
        "top_holdings": [
            {
                "symbol": h.get("symbol", ""),
                "name": stock_info.get(h.get("symbol", ""), {}).get("name", ""),
                "industry": stock_info.get(h.get("symbol", ""), {}).get("industry", ""),
                "ratio": h.get("stk_mkv_ratio", ""),
            }
            for h in top_holdings
        ],
        "portfolio_changes": portfolio_changes[:6],  # v9.5.123: 季度变动(新增/退出)
        **trend_data,                                  # v9.5.124: 8维走势预估(含维度分解)
        "source": "tushare+天天基金",
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # v9.8.7/v9.9.3: 共享详情永远落共享缓存；只有真实持仓基金才需要单独落用户缓存。
    shared_result = dict(result)
    _set_cached(shared_cache_key, shared_result)

    if userId:
        enriched_result = _enrich_detail_with_holding(dict(shared_result), code, userId)
        if enriched_result.get("holding_relation") == "🔵 已持仓":
            _set_cached(user_cache_key, enriched_result)
            return enriched_result

    return shared_result


# v9.8.7: 持仓增强（内联到 detail 接口，避免前端二次请求）
def _enrich_detail_with_holding(detail: dict, code: str, user_id: str) -> dict:
    """为基金详情补充用户持仓决策数据（轻量版）"""
    try:
        from services.persistence import load_user
        from api.signals import _get_fund_nav_percentile, _fund_timing_label

        user = load_user(user_id)
        if not user:
            return detail

        portfolio = user.get("portfolio") or {}
        holdings = portfolio.get("holdings") or user.get("holdings") or []

        holding = None
        for h in holdings:
            if h.get("code") == code:
                holding = h
                break
        if not holding:
            return detail

        shares = holding.get("shares", 0)
        cost_nav = holding.get("cost_nav", 0)
        amount = holding.get("amount", 0)
        buy_date = holding.get("buyDate", "")

        my_holding = {"shares": shares, "amount": amount}
        if cost_nav > 0:
            my_holding["avg_cost"] = cost_nav
        if buy_date:
            my_holding["buy_date"] = buy_date

        nav_now = detail.get("nav")
        pnl_pct = None
        if nav_now and cost_nav and cost_nav > 0:
            pnl_pct = round((nav_now - cost_nav) / cost_nav * 100, 2)

        detail["my_holding"] = my_holding
        if pnl_pct is not None:
            detail["pnl_pct"] = pnl_pct
        detail["holding_relation"] = "🔵 已持仓"

        try:
            np_info = _get_fund_nav_percentile(code)
            if np_info:
                detail["nav_percentile"] = np_info.get("percentile")
                detail["nav_pct_label"] = np_info.get("label")
        except Exception:
            pass
        try:
            tl = _fund_timing_label(code)
            if tl:
                detail["timing_label"] = tl
        except Exception:
            pass
        try:
            from services.industry_templates import get_fund_industry
            industry = get_fund_industry(code, detail.get("name", ""))
            if industry and industry != "其他":
                detail["industry_tag"] = industry
        except Exception:
            pass
        if not detail.get("scale_billion") and amount:
            detail["scale_billion"] = round(amount / 1e8, 1)

    except Exception as e:
        print(f"[FUND_DETAIL] holding enrich error for {code}: {e}")
    return detail


# ──────────────────────────────────────────────────────────
# v9.5.124: 多模型 AI 评分（异步按需加载，不阻塞详情页）
# ──────────────────────────────────────────────────────────
@router.get("/api/fund/ai-score/{code}")
def fund_ai_score(code: str):
    """三大模型并发评分：DeepSeek Pro + 豆包 Seed 2.0 + 千问 Qwen3.6

    前端在详情弹窗中异步调用，展示各模型打分和综合评价。
    缓存 12h，每天每只基金最多消耗 3 次 LLM 调用。
    """
    # 先拿基金详情数据作为评分输入
    cache_key = f"fund_detail_{code}"
    fund_info = _get_cached(cache_key, allow_stale=True)
    if not fund_info:
        # 没有缓存就实时拉
        from services.fund_rank import get_fund_dynamic_info
        fund_info = get_fund_dynamic_info(code)
        if not fund_info:
            return {"error": "基金数据不可用", "code": code}

    try:
        from services.multi_model_scorer import score_fund_multi_model
        result = score_fund_multi_model(fund_info)
        result["code"] = code
        return result
    except Exception as e:
        return {"error": str(e), "code": code, "scores": []}


# ──────────────────────────────────────────────────────────
# 股票基本信息查询（持仓录入自动补全用）
# ──────────────────────────────────────────────────────────
@router.get("/api/stock-basic/{code}")
def stock_basic_info(code: str):
    """根据股票代码返回名称、行业、最新价格"""
    cache_key = f"stock_basic_{code}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    # 尝试多种 ts_code 格式
    from services.tushare_data import _call_tushare
    info = None
    for suffix in [".SH", ".SZ"]:
        ts_code = code + suffix if "." not in code else code
        rows = _call_tushare("stock_basic", {"ts_code": ts_code}, "ts_code,name,industry,list_date")
        if rows:
            info = rows[0]
            break

    if not info:
        # 按代码模糊匹配
        rows = _call_tushare("stock_basic", {"exchange": "", "list_status": "L"}, "ts_code,name,industry")
        if rows:
            for r in rows:
                if r.get("ts_code", "").startswith(code):
                    info = r
                    break

    if not info:
        return {"available": False, "reason": "未找到该股票"}

    # 获取最新价格
    price = None
    try:
        ts_code = info.get("ts_code", "")
        price_rows = _call_tushare("daily", {"ts_code": ts_code, "limit": "1"}, "ts_code,close,trade_date")
        if price_rows:
            price = float(price_rows[0].get("close", 0))
    except Exception:
        pass

    result = {
        "available": True,
        "code": code,
        "ts_code": info.get("ts_code", ""),
        "name": info.get("name", ""),
        "industry": info.get("industry", ""),
        "price": price,
    }
    _set_cached(cache_key, result)
    return result


# ──────────────────────────────────────────────────────────
# Phase 2: 经理规模-业绩对照（规模诅咒检测）
# ──────────────────────────────────────────────────────────
@router.get("/api/fund/manager-track/{code}")
def fund_manager_track(code: str):
    """基金经理在不同规模阶段的业绩对照"""
    cache_key = f"fund_mgr_track_{code}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    from services.tushare_data import get_fund_manager, _call_tushare

    # 获取经理信息
    mgr_data = get_fund_manager(code)
    if not mgr_data.get("available") or not mgr_data.get("managers"):
        return {"available": False, "reason": "未找到基金经理信息"}

    manager_name = mgr_data["managers"][0].get("name", "未知")
    begin_date = mgr_data["managers"][0].get("begin_date", "")

    # 拉取净值历史（从任期开始到现在，按季度采样）
    ts_code = code if "." in code else f"{code}.OF"
    start = begin_date or (datetime.now() - timedelta(days=365 * 5)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    nav_rows = _call_tushare(
        "fund_nav", {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav"
    )

    # 拉取份额历史（尝试多种 ts_code 格式）
    share_rows = []
    for tc in [ts_code, f"{code}.SZ", f"{code}.SH"]:
        share_rows = _call_tushare(
            "fund_share", {"ts_code": tc, "start_date": start, "end_date": end},
            "ts_code,trade_date,fd_share,total_share"
        )
        if share_rows:
            break

    if not nav_rows or len(nav_rows) < 10:
        return {"available": False, "reason": "净值数据不足", "manager": manager_name}

    # 按季度分组计算
    nav_sorted = sorted(nav_rows, key=lambda r: r.get("nav_date") or r.get("ann_date", ""))
    share_map = {}
    for s in (share_rows or []):
        d = s.get("trade_date", "")
        share_val = float(s.get("fd_share") or s.get("total_share") or 0)
        if d and share_val > 0:
            share_map[d] = share_val

    # 每季度末采样
    track = []
    quarters_seen = set()
    for row in nav_sorted:
        date_str = row.get("nav_date") or row.get("ann_date", "")
        if not date_str or len(date_str) < 8:
            continue
        quarter = date_str[:4] + "Q" + str((int(date_str[4:6]) - 1) // 3 + 1)
        if quarter in quarters_seen:
            continue
        quarters_seen.add(quarter)

        unit_nav = float(row.get("unit_nav") or row.get("accum_nav") or 0)
        if unit_nav <= 0:
            continue

        # 找最近的份额数据
        closest_share = 0
        for sd in sorted(share_map.keys(), key=lambda x: abs(int(x) - int(date_str)))[:1]:
            closest_share = share_map[sd]
            break

        scale_billion = round(closest_share * unit_nav / 1e8 / 10, 2) if closest_share > 0 else None

        track.append({
            "quarter": quarter,
            "date": date_str,
            "nav": round(unit_nav, 4),
            "scale_billion": scale_billion,
        })

    # 计算每季度收益率
    for i in range(1, len(track)):
        prev_nav = track[i - 1]["nav"]
        cur_nav = track[i]["nav"]
        if prev_nav > 0:
            track[i]["quarter_return_pct"] = round((cur_nav - prev_nav) / prev_nav * 100, 2)

    # 过滤掉第一个（没有收益率），保留有收益率的
    track_with_data = [t for t in track[1:] if t.get("quarter_return_pct") is not None]

    # 如果规模数据全缺失，尝试 AKShare 获取当前规模补到最新季度
    has_scale = any(t.get("scale_billion") for t in track_with_data)
    current_scale = None
    if not has_scale and track_with_data:
        try:
            from services.tushare_data import get_fund_extra_info_ak
            ak_extra = get_fund_extra_info_ak(code)
            if ak_extra.get('最新规模'):
                scale_str = str(ak_extra['最新规模']).replace('亿', '').strip()
                current_scale = round(float(scale_str), 2)
                # 给最近几个季度填充当前规模（近似）
                if current_scale and current_scale > 0:
                    for t in track_with_data[-4:]:
                        if not t.get("scale_billion"):
                            t["scale_billion"] = current_scale
        except Exception:
            pass

    # AI 总结（如果有 LLM 可用）
    verdict = ""
    if track_with_data and len(track_with_data) >= 4:
        # 简单规则判断规模诅咒
        early = track_with_data[:len(track_with_data) // 2]
        late = track_with_data[len(track_with_data) // 2:]
        early_median = sorted([t["quarter_return_pct"] for t in early])[len(early) // 2] if early else 0
        late_median = sorted([t["quarter_return_pct"] for t in late])[len(late) // 2] if late else 0
        early_scale = sum(t["scale_billion"] for t in early if t.get("scale_billion")) / max(sum(1 for t in early if t.get("scale_billion")), 1)
        late_scale = sum(t["scale_billion"] for t in late if t.get("scale_billion")) / max(sum(1 for t in late if t.get("scale_billion")), 1)

        # 规模数据充分时给规模诅咒判断
        if early_scale > 0 and late_scale > 0:
            if late_scale > early_scale * 2 and late_median < early_median * 0.5:
                verdict = f"⚠️ 规模诅咒明显：规模从{early_scale:.1f}亿增至{late_scale:.1f}亿后，季度收益中位数从{early_median:.1f}%降至{late_median:.1f}%"
            elif late_scale > early_scale * 1.5 and late_median < early_median:
                verdict = f"🟡 存在规模压力：规模{early_scale:.1f}→{late_scale:.1f}亿，收益略有下降"
            else:
                verdict = f"✅ 规模管理良好：规模{early_scale:.1f}→{late_scale:.1f}亿，收益未明显下滑"
        else:
            # 规模数据不全，只评价收益趋势
            final_scale = current_scale or late_scale or 0
            scale_note = f"（当前规模{final_scale:.1f}亿）" if final_scale > 0 else ""
            if late_median >= early_median:
                verdict = f"✅ 收益表现稳定{scale_note}，后半段季度中位数{late_median:.1f}% ≥ 前半段{early_median:.1f}%"
            else:
                verdict = f"🟡 近期收益有所回落{scale_note}，后半段季度中位数{late_median:.1f}% < 前半段{early_median:.1f}%"

    result = {
        "available": True,
        "manager": manager_name,
        "current_scale_billion": current_scale or (track_with_data[-1]["scale_billion"] if track_with_data else None),
        "track": track_with_data[-12:],  # 最多返回最近 12 个季度
        "verdict": verdict,
        "source": "tushare",
    }
    _set_cached(cache_key, result)
    return result


# ──────────────────────────────────────────────────────────
# Phase 3: 政策 → 受益基金/股票映射
# ──────────────────────────────────────────────────────────
@router.get("/api/policy/beneficiaries")
def policy_beneficiaries(topic: str = "数字基建"):
    """分析某政策主题的受益基金和股票"""
    cache_key = f"policy_benef_{topic}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    # 用 DeepSeek 分析政策 → 受益行业 + 标的
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"available": False, "reason": "LLM 不可用"}

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()

        prompt = f"""你是 A 股行业分析师。用户想了解「{topic}」政策的受益标的。

请按以下 JSON 格式回答（不要 markdown，纯 JSON）：
{{
  "summary": "一段话总结政策规模和核心方向（50字内）",
  "industries": ["受益行业1", "受益行业2", "受益行业3"],
  "funds": [
    {{"name": "基金名称", "code": "6位代码", "reason": "匹配原因（10字内）"}},
    ...最多5只
  ],
  "stocks": [
    {{"name": "公司名称", "code": "6位代码", "reason": "匹配原因（10字内）"}},
    ...最多5只
  ]
}}

要求：
1. 基金优先推 ETF（流动性好、费率低），其次主动基金
2. 股票推行业龙头（市值大、流动性好）
3. 所有代码必须是真实存在的 A 股/场内基金代码
4. 如果不确定代码，宁可不推也不要编造"""

        llm_result = gw.call_sync(
            prompt,
            system="你是 A 股行业分析师，输出纯 JSON，不要 markdown。",
            module="policy_beneficiaries",
            max_tokens=800,
        )

        import json as json_mod
        text = llm_result.get("content", "") if isinstance(llm_result, dict) else str(llm_result)
        # 尝试提取 JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

        data = json_mod.loads(text)
        result = {
            "available": True,
            "topic": topic,
            "summary": data.get("summary", ""),
            "industries": data.get("industries", []),
            "funds": data.get("funds", [])[:5],
            "stocks": data.get("stocks", [])[:5],
            "source": "deepseek",
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        return {"available": False, "reason": f"分析失败: {str(e)[:100]}"}


# ──────────────────────────────────────────────────────────
# Phase 4: 政策标签聚合（选基/选股列表小徽章用）
# ──────────────────────────────────────────────────────────
# 热门政策主题列表（可后续配置化）
_HOT_POLICY_TOPICS = ["数字基建", "AI算力", "新能源", "半导体", "国产替代"]


@router.get("/api/policy/tags")
def policy_tags():
    """聚合所有热门政策主题的受益标的 code→[topic] 映射，供前端列表打标签"""
    cache_key = "policy_tags_all"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    code_tags: dict[str, list[str]] = {}

    for topic in _HOT_POLICY_TOPICS:
        # 复用已有的 policy_beneficiaries 逻辑（带缓存）
        benef = policy_beneficiaries(topic)
        if not benef.get("available"):
            continue
        # 提取基金代码
        for f in benef.get("funds", []):
            c = str(f.get("code", "")).strip()
            if c:
                code_tags.setdefault(c, []).append(topic)
        # 提取股票代码
        for s in benef.get("stocks", []):
            c = str(s.get("code", "")).strip()
            if c:
                code_tags.setdefault(c, []).append(topic)

    # 去重
    for c in code_tags:
        code_tags[c] = list(dict.fromkeys(code_tags[c]))

    result = {
        "available": True,
        "topics": _HOT_POLICY_TOPICS,
        "code_tags": code_tags,
        "total_codes": len(code_tags),
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _set_cached(cache_key, result)
    return result


# ============================================================
# 基金季度持仓接口
# ============================================================

@router.get("/api/fund/portfolio/{code}")
def fund_portfolio_holdings(code: str, userId: str = ""):
    """基金季度前十大持仓 + 与用户持股重叠度分析

    数据源: AKShare fund_portfolio_hold_em (天天基金季报)
    缓存: 7天（季报1季度才更新一次，但为了显示最新季度适度短一点）
    """
    cache_key = f"fund_portfolio_{code}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        import akshare as ak
        from services.utils import ak_call
        from datetime import date

        # 获取当前年份，尝试最近几个季度
        current_year = str(date.today().year)
        df = None
        for year in [current_year, str(date.today().year - 1)]:
            try:
                df = ak_call(ak.fund_portfolio_hold_em, symbol=code, date=year)
                if df is not None and len(df) > 0:
                    break
            except Exception:
                continue

        if df is None or len(df) == 0:
            return {"available": False, "reason": "暂无持仓数据（新基金或数据源暂时不可用）"}

        holdings = []
        for _, row in df.iterrows():
            holdings.append({
                "rank": int(row.get("序号", 0)) if row.get("序号") else len(holdings)+1,
                "code": str(row.get("股票代码", "")).strip(),
                "name": str(row.get("股票名称", "")).strip(),
                "pct": float(row.get("占净值比例", 0)) if row.get("占净值比例") else None,
                "shares": float(row.get("持股数（万股）", 0)) if row.get("持股数（万股）") else None,
                "value": float(row.get("持仓市值（万元）", 0)) if row.get("持仓市值（万元）") else None,
            })

        # 与用户持股重叠分析
        overlap = []
        if userId:
            try:
                from services.stock_monitor import load_stock_holdings
                user_stocks = load_stock_holdings(userId) or []
                user_codes = {str(h.get("code", "")).strip() for h in user_stocks}
                for h in holdings:
                    if h["code"] in user_codes:
                        overlap.append(h["code"])
            except Exception:
                pass

        # 数据期
        report_date = ""
        try:
            report_date = str(df.get("报告期", df.iloc[0].get("报告期", ""))) if hasattr(df, "get") else ""
        except Exception:
            pass

        result = {
            "available": True,
            "code": code,
            "holdings": holdings[:10],
            "total": len(holdings),
            "overlap_codes": overlap,
            "overlap_count": len(overlap),
            "report_date": report_date,
            "data_source": "天天基金季度报告",
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        return {"available": False, "reason": str(e)}


# ============================================================
# IPO 新股日历接口
# ============================================================

@router.get("/api/ipo/upcoming")
def ipo_upcoming():
    """A股近期 IPO 日历 + 热门公司观察列表

    数据源: Tushare new_share（近30天）
    缓存: 24小时
    """
    cache_key = "ipo_upcoming"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    from datetime import date, timedelta
    ipos = []

    # A股新股日历（Tushare）
    try:
        from services.tushare_data import _call_tushare
        today = date.today()
        start = (today - timedelta(days=5)).strftime("%Y%m%d")
        end = (today + timedelta(days=30)).strftime("%Y%m%d")
        rows = _call_tushare(
            "new_share",
            {"start_date": start, "end_date": end},
            "ts_code,name,ipo_date,issue_date,amount,market,issue_price"
        )
        # 按行业推断纳入指数及基金
        INDEX_FUND_MAP = {
            "科创": {"index": "科创50", "funds": ["华夏科创50ETF联接A", "易方达科创板50ETF联接A"]},
            "创业": {"index": "创业板指", "funds": ["易方达创业板ETF联接A", "广发创业板ETF联接A"]},
            "上交所": {"index": "沪深300", "funds": ["易方达沪深300ETF联接A", "华泰柏瑞沪深300ETF联接A"]},
            "深交所": {"index": "沪深300", "funds": ["易方达沪深300ETF联接A"]},
        }
        for r in (rows or [])[:10]:
            name = r.get("name", "")
            ipo_date = r.get("ipo_date", "")
            ts_code = r.get("ts_code", "")
            market = r.get("market", "")

            # market字段经常为空，从ts_code和名称推断
            if not market:
                if ts_code.endswith(".SH"):
                    market = "科创板" if ts_code.startswith("688") else "上交所主板"
                elif ts_code.endswith(".SZ"):
                    market = "创业板" if ts_code.startswith("300") else "深交所主板"
                elif ts_code.endswith(".BJ"):
                    market = "北交所"

            # 按市场推断纳入指数及布局基金
            fund_info = {}
            if "科创" in market or ts_code.startswith("688"):
                fund_info = {"index": "科创50/中证1000", "funds": ["华夏科创50ETF联接A", "国泰中证1000ETF联接A"]}
            elif "创业" in market or ts_code.startswith("300"):
                fund_info = {"index": "创业板指/中证1000", "funds": ["易方达创业板ETF联接A", "国泰中证1000ETF联接A"]}
            elif "北交所" in market:
                fund_info = {"index": "北证50", "funds": ["华夏北交所创新中小企业ETF联接"]}
            else:
                fund_info = {"index": "沪深300", "funds": ["易方达沪深300ETF联接A"]}

            ipos.append({
                "name": name,
                "ts_code": ts_code,
                "ipo_date": ipo_date,
                "market": market,
                "issue_price": r.get("issue_price"),
                "amount": r.get("amount"),
                "index": fund_info.get("index", ""),
                "funds": fund_info.get("funds", []),
            })
    except Exception as e:
        pass

    # v9.5.53: tushare 失败时用 akshare 兜底
    if not ipos:
        try:
            import akshare as ak
            from services.utils import ak_call
            import math
            from datetime import date
            df = ak_call(ak.stock_new_ipo_cninfo)
            if df is not None and not df.empty:
                today = date.today()
                for _, row in df.iterrows():
                    sub_d = row.get("申购日期")
                    list_d = row.get("上市日期")
                    if not sub_d or (isinstance(sub_d, float) and math.isnan(sub_d)):
                        continue
                    try:
                        days = (sub_d - today).days
                        if days < -7 or days > 30:
                            continue
                    except Exception:
                        continue
                    name = str(row.get("证券简称", ""))
                    code = str(row.get("证劵代码", ""))
                    # 推断市场
                    if code.startswith("688"): market, fund_info = "科创板", {"index": "科创50", "funds": ["华夏科创50ETF联接A"]}
                    elif code.startswith("300"): market, fund_info = "创业板", {"index": "创业板指", "funds": ["易方达创业板ETF联接A"]}
                    elif code.startswith("920") or code.startswith("83") or code.startswith("87"): market, fund_info = "北交所", {"index": "北证50", "funds": ["华夏北交所创新中小企业ETF联接"]}
                    elif code.endswith(".SH") or code.startswith("6"): market, fund_info = "上交所主板", {"index": "沪深300", "funds": ["易方达沪深300ETF联接A"]}
                    else: market, fund_info = "深交所主板", {"index": "沪深300", "funds": ["易方达沪深300ETF联接A"]}
                    ipos.append({
                        "name": name,
                        "ts_code": code,
                        "ipo_date": ("" if not list_d or (isinstance(list_d, float) and math.isnan(list_d)) or str(list_d)=="NaT" else str(list_d)[:10]),
                        "subscribe_date": str(sub_d)[:10],
                        "market": market,
                        "issue_price": float(row.get("发行价", 0)) if row.get("发行价") and not (isinstance(row.get("发行价"), float) and math.isnan(row.get("发行价"))) else None,
                        "amount": None,
                        "index": fund_info["index"],
                        "funds": fund_info["funds"],
                        "data_source": "akshare",
                    })
                ipos.sort(key=lambda x: x.get("subscribe_date", "9999"))
                ipos = ipos[:10]
        except Exception:
            pass

    result = {
        "ipos": ipos,
        "total": len(ipos),
        "generated_at": datetime.now().isoformat(),
    }
    _set_cached(cache_key, result)
    return result


# ============================================================
# IPO 观察列表（统一配置，前端+晨报共用）
# ============================================================

# 唯一权威数据源，修改这里即可同步到 IPO 页面和晨报
_IPO_WATCHLIST_DATA = [
    {"name": "长鑫科技", "market": "A股科创板", "status": "进行中",
     "index": ["科创50", "中证1000"],
     "funds": ["华夏科创50ETF联接A", "国泰中证1000ETF联接A"],
     "note": "国内DRAM存储芯片龙头，与三星/海力士竞争HBM，上市进程持续推进",
     "flag": "🇨🇳", "fundType": "index"},
    {"name": "长江存储", "market": "A股科创板", "status": "传闻中",
     "index": ["科创50", "中证半导体"],
     "funds": ["华夏科创50ETF联接A", "国联安中证半导体ETF联接"],
     "note": "国内NAND Flash龙头，3D NAND研发实力强，上市预期持续升温",
     "flag": "🇨🇳", "fundType": "index"},
    {"name": "xAI", "market": "美股纳斯达克", "status": "已取消",
     "index": ["纳斯达克100", "标普500"],
     "funds": ["博时纳斯达克100ETF联接C", "易方达标普500ETF联接C"],
     "note": "2025年被 SpaceX 收购，不再独立IPO。通过持有纳指基金间接覆盖",
     "flag": "🇺🇸", "fundType": "qdii"},
    {"name": "SpaceX", "market": "美股纳斯达克", "status": "✅ 已上市",
     "index": ["纳斯达克100"],
     "funds": ["博时纳斯达克100ETF联接C"],
     "note": "2026-06-12 纳斯达克上市（代码 SPCX），发行价$135，估值约1.75万亿美元，史上最大IPO。已纳入纳指，持有纳指基金即自动持有",
     "flag": "🇺🇸", "fundType": "qdii"},
    {"name": "字节跳动", "market": "港股/美股待定", "status": "传闻中",
     "index": ["恒生科技", "纳斯达克100"],
     "funds": ["华夏恒生科技ETF联接A", "博时纳斯达克100ETF联接C"],
     "note": "TikTok/抖音母公司，若港股上市则利好恒生科技指数",
     "flag": "🌐", "fundType": "qdii"},
    {"name": "英伟达", "market": "美股纳斯达克", "status": "✅ 已上市",
     "index": ["纳斯达克100", "标普500"],
     "funds": ["博时纳斯达克100ETF联接C"],
     "note": "AI算力核心标的，持有纳指基金即自动持有。启示：提前布局指数 > 追涨个股",
     "flag": "🇺🇸", "fundType": "qdii"},
    {"name": "宁德时代", "market": "A股科创板", "status": "✅ 已上市",
     "index": ["科创50", "沪深300"],
     "funds": ["华夏科创50ETF联接A", "易方达沪深300ETF联接A"],
     "note": "2018年上市，新能源龙头。启示：上市前布局科创板基金，可自动获取大部分涨幅",
     "flag": "🇨🇳", "fundType": "index"},
]


@router.get("/api/ipo/watchlist")
def ipo_watchlist():
    """热门IPO观察列表（统一配置源，前端和晨报共用）

    v9.8.8: 优先读 DATA_DIR/_cache/ipo_watchlist_api.json（由 ipo_verify.py 自动生成），
    不存在时 fallback 到硬编码的 _IPO_WATCHLIST_DATA。
    """
    # v9.8.8: 优先读 ipo_verify.py 生成的 API 缓存（含自动验证的状态）
    api_cache_fp = DATA_DIR / "_cache" / "ipo_watchlist_api.json"
    if api_cache_fp.exists():
        try:
            watchlist = json.loads(api_cache_fp.read_text(encoding="utf-8"))
            watchlist = _inject_hot_scores(watchlist)
            return {
                "watchlist": watchlist,
                "total": len(watchlist),
                "generated_at": datetime.now().isoformat(),
                "source": "ipo_watchlist_api.json (auto)",
            }
        except Exception as e:
            print(f"[IPO] 读取 API 缓存失败: {e}")

    # Fallback: 用硬编码数据 + 状态覆盖
    return _ipo_watchlist_fallback()


def _inject_hot_scores(watchlist: list) -> list:
    """为观察列表注入雪球热度分"""
    hot_scores = _get_hot_rank_scores()
    for entry in watchlist:
        name = entry.get("name", "")
        rank = hot_scores.get(name)
        if rank:
            entry["hot_rank"] = rank
            entry["hot_label"] = f"雪球热榜 #{rank}"
    return watchlist


# v9.9.x: 热度榜 5 分钟 TTL 缓存
# 修复背景：原 ak.stock_hot_rank_wc_em() 在当前 akshare(1.18.60) 中已不存在
# （AttributeError），且此前完全没有缓存 —— /api/ipo/watchlist 每次请求都会
# 直接同步打一次必败的网络调用，被 except: pass 静默吞掉，"雪球热度分"功能
# 已经失效了不知道多久，用户看到的 hot_rank 字段永远是空的。
# 改为 stock_hot_rank_em()（实测 0.13s/98 行，字段：当前排名/代码/股票名称），
# 用 ak_call() 加超时保护，并把 _inject_hot_scores / _ipo_watchlist_fallback
# 两处重复逻辑合并成这一个函数 —— 避免同一个 bug 改一处漏一处。
_hot_rank_cache = {"scores": {}, "t": 0.0}
_HOT_RANK_TTL = 300  # 5 分钟：热度榜分钟级波动，不需要每次请求都拉


def _get_hot_rank_scores() -> dict:
    """拉取雪球/东财热度榜，返回 {股票名称: 排名}（5 分钟缓存 + 超时保护）"""
    now = time.time()
    if _hot_rank_cache["scores"] and now - _hot_rank_cache["t"] < _HOT_RANK_TTL:
        return _hot_rank_cache["scores"]

    hot_scores = {}
    try:
        import akshare as ak
        from services.utils import ak_call
        df = ak_call(ak.stock_hot_rank_em, timeout=10)
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                name = str(row.get("股票名称", ""))
                rank = row.get("当前排名")
                if name and rank:
                    hot_scores[name] = int(rank)
    except Exception:
        pass

    if hot_scores:
        _hot_rank_cache["scores"] = hot_scores
        _hot_rank_cache["t"] = now
    return hot_scores or _hot_rank_cache["scores"]  # 拉取失败时沿用旧缓存，而不是清空


def _ipo_watchlist_fallback() -> dict:
    """Fallback: 使用硬编码数据 + 状态覆盖"""
    hot_scores = _get_hot_rank_scores()


    # v9.5.123: 读取周日自动验证的状态覆盖
    _ipo_overrides = {}
    try:
        _override_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "ipo_status.json"
        if _override_fp.exists():
            _ipo_overrides = json.loads(_override_fp.read_text(encoding="utf-8"))
    except Exception:
        pass

    result_list = []
    for item in _IPO_WATCHLIST_DATA:
        entry = dict(item)
        name = item["name"]
        if name in _ipo_overrides:
            verified = _ipo_overrides[name]
            if verified.get("status"):
                entry["status"] = verified["status"]
                entry["verified_at"] = verified.get("verified_at", "")
        rank = hot_scores.get(name)
        if rank:
            entry["hot_rank"] = rank
            entry["hot_label"] = f"雪球热榜 #{rank}"
        result_list.append(entry)

    return {
        "watchlist": result_list,
        "total": len(result_list),
        "generated_at": datetime.now().isoformat(),
        "source": "hardcoded (fallback)",
        "note": "修改 data/ipo_watchlist.json 或运行 ipo_verify.py 即可更新"
    }


# ============================================================
# 买入凭证截图解析接口
# ============================================================

# 基金名称→代码缓存（24小时）
_fund_name_cache: dict = {}
_fund_name_cache_ts: float = 0.0

def _get_fund_code_by_name(name: str) -> str | None:
    """通过基金名称模糊匹配基金代码"""
    global _fund_name_cache, _fund_name_cache_ts
    import time as _time
    now = _time.time()
    # 24小时重建缓存
    if not _fund_name_cache or now - _fund_name_cache_ts > 86400:
        try:
            import akshare as ak
            from services.utils import ak_call
            df = ak_call(ak.fund_name_em)  # 实测全市场 ~2.8万条耗时 7.9s，默认 15s 超时够用
            if df is None:
                raise RuntimeError("ak_call 超时或返回空，沿用旧缓存")
            _fund_name_cache = {
                str(row.get("基金简称", "")): str(row.get("基金代码", ""))
                for _, row in df.iterrows()
                if row.get("基金简称") and row.get("基金代码")
            }
            _fund_name_cache_ts = now
        except Exception as e:
            print(f"[RECEIPT] fund_name_em failed: {e}")
            return None

    # 精确匹配
    if name in _fund_name_cache:
        return _fund_name_cache[name]

    # 模糊匹配策略：
    # 去掉"发起"、"(LOF)"等修饰词后匹配，但保留 A/B/C 后缀区分份额类别
    import re as _re
    # 提取份额后缀（A/B/C/E等）
    suffix_m = _re.search(r'([A-Za-z])$', name.strip())
    name_suffix = suffix_m.group(1).upper() if suffix_m else None
    name_clean = _re.sub(r'(发起|LOF|ETF联接|联接)', '', name).strip()

    best = None
    best_score = 0
    for fund_name, code in _fund_name_cache.items():
        score = 0
        fund_clean = _re.sub(r'(发起|LOF|ETF联接|联接)', '', fund_name).strip()
        # 份额后缀匹配（A/C要对应）
        fund_suffix_m = _re.search(r'([A-Za-z])$', fund_name.strip())
        fund_suffix = fund_suffix_m.group(1).upper() if fund_suffix_m else None
        suffix_match = (name_suffix == fund_suffix) if (name_suffix and fund_suffix) else True

        if (name in fund_name or fund_name in name) and suffix_match:
            score = len(min(name, fund_name, key=len)) + (2 if suffix_match else 0)
        elif name_clean and (name_clean in fund_clean or fund_clean in name_clean) and suffix_match:
            score = len(min(name_clean, fund_clean, key=len))
        if score > best_score:
            best = code
            best_score = score
    return best


def _parse_receipt_text(text: str) -> dict:
    """解析买入凭证文字（支付宝/天天基金/华夏直销等）

    支持字段：基金名、基金代码、确认净值、确认份额、买入金额、确认日期
    """
    import re

    result = {
        "fund_name": None,
        "fund_code": None,
        "nav": None,       # 确认净值（成本净值）
        "shares": None,    # 确认份额
        "amount": None,    # 买入金额
        "date": None,      # 确认日期
        "source": "text_parse",
    }

    # 基金名（常见格式）
    patterns_name = [
        r'买入产品[：:]\s*(.+?)(?:\s|>|$)',
        r'产品名称[：:]\s*(.+?)(?:\s|$)',
        r'基金名称[：:]\s*(.+?)(?:\s|$)',
        r'([\u4e00-\u9fa5A-Za-z0-9（）()]+(?:混合|债券|股票|指数|ETF|LOF|QDII|货币)[A-Za-z0-9]*)',
    ]
    for pat in patterns_name:
        m = re.search(pat, text)
        if m:
            result["fund_name"] = m.group(1).strip().rstrip('>')
            break

    # 确认净值
    for pat in [r'确认净值[：:\s]+(\d+\.\d+)', r'成本净值[：:\s]+(\d+\.\d+)', r'净值[：:\s]+(\d+\.\d+)']:
        m = re.search(pat, text)
        if m:
            result["nav"] = float(m.group(1))
            break

    # 确认份额
    for pat in [r'确认份额[：:\s]+([\d,.]+)份?', r'份额[：:\s]+([\d,.]+)', r'([\d,.]+)\s*份']:
        m = re.search(pat, text)
        if m:
            result["shares"] = float(m.group(1).replace(',', ''))
            break

    # 买入金额
    for pat in [r'买入金额[：:\s]+([\d,.]+)元?', r'确认金额[：:\s]+([\d,.]+)', r'金额[：:\s]+([\d,.]+)']:
        m = re.search(pat, text)
        if m:
            result["amount"] = float(m.group(1).replace(',', ''))
            break

    # 日期
    for pat in [r'确认时间[：:\s]+(\d{4}-\d{2}-\d{2})', r'买入时间[：:\s]+(\d{4}-\d{2}-\d{2})', r'(\d{4}-\d{2}-\d{2})']:
        m = re.search(pat, text)
        if m:
            result["date"] = m.group(1)
            break

    # 基金代码（直接出现在文本里）
    m = re.search(r'\b(\d{6})\b', text)
    if m:
        result["fund_code"] = m.group(1)

    # 若没有代码但有基金名，尝试反查
    if not result["fund_code"] and result["fund_name"]:
        result["fund_code"] = _get_fund_code_by_name(result["fund_name"])

    return result


@router.post("/api/fund/parse-receipt")
async def parse_fund_receipt(request: dict):
    """解析买入凭证，返回结构化数据供前端自动填入

    输入：{"text": "截图文字内容"} 或 {"image_base64": "..."}
    输出：{"fund_name", "fund_code", "nav", "shares", "amount", "date"}
    """
    text = request.get("text", "")
    image_b64 = request.get("image_base64", "")

    if not text and not image_b64:
        return {"ok": False, "reason": "请提供文字内容或图片"}

    # 图片优先用通义千问 qwen-vl-max 识别
    if image_b64:
        try:
            from services.qwen_client import parse_receipt_image, is_qwen_available
            if is_qwen_available():
                # 去掉 data URI 前缀
                if image_b64.startswith("data:"):
                    image_b64 = image_b64.split(",", 1)[1]
                import base64 as _b64
                try:
                    img_bytes = _b64.b64decode(image_b64)
                except Exception as e:
                    return {"ok": False, "reason": f"图片解码失败: {e}"}

                result = parse_receipt_image(img_bytes, image_format="jpeg")
                if result.get("ok"):
                    # 基金名反查代码（如果VL没识别出代码）
                    if not result.get("fund_code") and result.get("fund_name"):
                        result["fund_code"] = _get_fund_code_by_name(result["fund_name"])
                    return result
                else:
                    print(f"[RECEIPT] qwen-vl failed: {result.get('reason')}")
                    # 降级到文字解析（如果有 text）
                    if not text:
                        return result
        except Exception as e:
            print(f"[RECEIPT] vision exception: {e}")

    # 文字解析（主路径或图片识别失败的降级）
    if not text:
        return {"ok": False, "reason": "图片识别失败，请改用文字粘贴"}

    result = _parse_receipt_text(text)

    if not result["fund_name"] and not result["fund_code"]:
        return {"ok": False, "reason": "未识别到基金信息，请检查文字格式"}

    return {"ok": True, **result}


# ============================================================
# F10 v9.5.47: 基金净值历史（K线）接口
# 前端 insight.js _showFundKline() 调用
# ============================================================

_NAV_HISTORY_CACHE: dict = {}
_NAV_HISTORY_TTL = 3600  # 1小时缓存（净值日更）


@router.get("/api/fund/nav-history/{code}")
def fund_nav_history(code: str, days: int = 90):
    """F10 基金净值历史，供前端 K 线展示

    参数：
      code: 6位基金代码（如 110019）
      days: 历史天数，默认 90，最多 365

    返回：
      {ok, code, name, data: [{date, nav, cumNav}], benchmark: {name, data}}

    data 按日期升序排列，方便前端直接传入 Chart.js
    """
    days = min(max(days, 30), 365)
    cache_key = f"nav_hist_{code}_{days}"
    cached = _get_cached(cache_key, allow_stale=True)
    if cached:
        return {**cached, "cached": True}

    try:
        import akshare as ak
        from services.utils import ak_call
        from datetime import date, timedelta

        # 直接用 akshare fund_open_fund_info_em（列名：净值日期/单位净值/日增长率）
        df = ak_call(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势")
        if df is None or df.empty:
            return {"ok": False, "code": code, "reason": "无法获取净值数据"}

        # 时间过滤
        cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        df["_date"] = df["净值日期"].astype(str).str[:10]
        df = df[df["_date"] >= cutoff].sort_values("_date")

        data = [
            {"date": row["_date"], "nav": round(float(row.get("单位净值", 0) or 0), 4)}
            for _, row in df.iterrows()
        ]

        if not data:
            return {"ok": False, "code": code, "reason": "近期无净值数据"}

        result = {"ok": True, "code": code, "data": data, "count": len(data)}
        _set_cached(cache_key, result)
        return {**result, "cached": False}

    except Exception as e:
        stale = _get_cached(cache_key, allow_stale=True)
        if stale:
            return {**stale, "cached": True, "stale": True}
        return {"ok": False, "code": code, "reason": str(e)[:100]}


# ============================================================
# F11 v9.5.48: 实时汇率接口（QDII 计价切换用）
# ============================================================

_FX_CACHE: dict = {}
_FX_TTL = 7200  # 2小时缓存（汇率非高频）

# 单位换算：akshare currency_boc_sina 返回的是"100 外币 = N 人民币 × 100"
# 例如美元 677.43 → 实际 1 USD = 6.7743 CNY
_CURRENCY_SYMBOLS = {
    "USD": "美元", "HKD": "港币", "EUR": "欧元",
    "JPY": "日元", "GBP": "英镑", "AUD": "澳大利亚元",
}


@router.get("/api/fx/rate")
def get_fx_rate(currency: str = "USD"):
    """F11 实时汇率（人民币对外币）

    参数：currency = USD/HKD/EUR/JPY/GBP/AUD
    返回：{ok, currency, rate, date, source}
      rate: 1 外币 = N 人民币
    """
    cur = currency.upper()
    if cur not in _CURRENCY_SYMBOLS:
        return {"ok": False, "currency": cur, "reason": f"不支持的币种，可用：{list(_CURRENCY_SYMBOLS)}"}

    cache_key = f"fx_{cur}"
    now = time.time()
    if cache_key in _FX_CACHE:
        ts, d = _FX_CACHE[cache_key]
        if now - ts < _FX_TTL:
            return {**d, "cached": True}

    try:
        import akshare as ak
        from services.utils import ak_call
        import math
        from datetime import date, timedelta
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
        df = ak_call(ak.currency_boc_sina, symbol=_CURRENCY_SYMBOLS[cur], start_date=start, end_date=end)
        if df is None or df.empty:
            return {"ok": False, "currency": cur, "reason": "汇率数据为空"}

        # 过滤掉央行中间价为 NaN 的行（节假日只有买卖价无中间价）
        if "央行中间价" in df.columns:
            df = df.dropna(subset=["央行中间价"])
        if df.empty:
            return {"ok": False, "currency": cur, "reason": "近期无有效中间价"}

        latest = df.iloc[-1]
        raw = latest.get("央行中间价", 0)
        raw = float(raw) if raw is not None and not (isinstance(raw,float) and math.isnan(raw)) else 0
        if raw <= 0:
            raw2 = latest.get("中行折算价", 0)
            raw = float(raw2) if raw2 is not None and not (isinstance(raw2,float) and math.isnan(raw2)) else 0
        if raw <= 0 or math.isnan(raw):
            return {"ok": False, "currency": cur, "reason": "汇率数值无效"}

        # 单位转换（除 100）
        rate = round(raw / 100, 4)
        result = {
            "ok": True, "currency": cur, "rate": rate,
            "date": str(latest.get("日期", ""))[:10],
            "source": "中国银行（央行中间价）"
        }
        _FX_CACHE[cache_key] = (now, result)
        return {**result, "cached": False}
    except Exception as e:
        return {"ok": False, "currency": cur, "reason": str(e)[:100]}


# ============================================================
# v9.5.53: IPO 动态拉取接口（A股+港股，海外保留硬编码）
# ============================================================

_IPO_CACHE: dict = {}
_IPO_TTL = 14400  # 4小时缓存


@router.get("/api/ipo/upcoming-live")
def get_ipo_upcoming_live(market: str = "hs"):
    """v9.5.53 实时拉取即将上市/申购的新股

    参数：market = hs (沪深A股) / hk (港股)
    返回：{ok, market, items: [{code, name, subscribe_date, list_date, price, pe, source}], count, cached}
    """
    cache_key = f"ipo_{market}"
    now = time.time()
    if cache_key in _IPO_CACHE:
        ts, d = _IPO_CACHE[cache_key]
        if now - ts < _IPO_TTL:
            return {**d, "cached": True}

    try:
        import akshare as ak
        from services.utils import ak_call
        import math
        from datetime import date

        items = []
        if market == "hs":
            df = ak_call(ak.stock_new_ipo_cninfo)
            if df is None or df.empty:
                return {"ok": False, "market": market, "reason": "无数据"}
            today = date.today()
            for _, row in df.iterrows():
                sub_d = row.get("申购日期")
                list_d = row.get("上市日期")
                # 时间过滤：保留近 60 天内有申购或上市的
                try:
                    if sub_d and not (isinstance(sub_d, float) and math.isnan(sub_d)):
                        days = (sub_d - today).days if hasattr(sub_d, 'days') is False else -999
                        if days < -7 or days > 60:
                            continue
                except Exception:
                    continue
                price = row.get("发行价", 0)
                pe = row.get("发行市盈率", 0)
                items.append({
                    "code": str(row.get("证劵代码", "")),
                    "name": str(row.get("证券简称", "")),
                    "subscribe_date": str(sub_d)[:10] if sub_d else "",
                    "list_date": ("" if not list_d or (isinstance(list_d, float) and math.isnan(list_d)) or str(list_d)=="NaT" else str(list_d)[:10]),
                    "price": float(price) if price and not (isinstance(price, float) and math.isnan(price)) else None,
                    "pe": float(pe) if pe and not (isinstance(pe, float) and math.isnan(pe)) else None,
                })
            # 按申购日期升序
            items.sort(key=lambda x: x.get("subscribe_date", "9999"))
            items = items[:20]

        elif market == "hk":
            df = ak_call(ak.stock_ipo_hk_ths)
            if df is None or df.empty:
                return {"ok": False, "market": market, "reason": "无数据"}
            for _, row in df.iterrows():
                price_raw = row.get("发行价格", "")
                try:
                    price = float(str(price_raw).replace(",", "")) if price_raw and str(price_raw) != "-" else None
                except Exception:
                    price = None
                items.append({
                    "code": str(row.get("股票代码", "")),
                    "name": str(row.get("股票简称", "")),
                    "subscribe_date": str(row.get("申购日期", ""))[:10],
                    "list_date": str(row.get("上市日期", ""))[:10] if str(row.get("上市日期", "")) != "-" else "",
                    "price": price,
                    "pe": None,
                })
            items = items[:15]
        else:
            return {"ok": False, "market": market, "reason": f"不支持的市场: {market}"}

        result = {"ok": True, "market": market, "items": items, "count": len(items)}
        _IPO_CACHE[cache_key] = (now, result)
        return {**result, "cached": False}
    except Exception as e:
        return {"ok": False, "market": market, "reason": str(e)[:100]}


# ---- P1-8: 基金历史收益率（Tushare 优先）----
from services.fund_history_returns import get_fund_history_returns

@router.get("/api/fund/history-returns/{code}")
async def fund_history_returns(code: str):
    """
    获取基金历史收益率（1个月、3个月、6个月、1年、3年）
    优先使用 Tushare，失败降级到 AKShare
    """
    try:
        result = get_fund_history_returns(code)
        
        if result:
            return {
                "ok": True,
                "code": code,
                "data": result
            }
        else:
            return {
                "ok": False,
                "code": code,
                "reason": "无法获取历史净值数据"
            }
    except Exception as e:
        return {
            "ok": False,
            "code": code,
            "reason": str(e)[:200]
        }
