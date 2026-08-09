"""
基金持仓 & 盯盘引擎 — 独立 service
职责：
  1. 基金持仓 CRUD（后端 JSON 持久化）
  2. 实时估值 / 净值 / 持仓明细
  3. 风控指标（回撤、波动率、估算偏差）
  4. 异动检测 & 预警信号
  5. 全持仓扫描（供 cron 脚本调用）
"""
import os
import json
import time
import math
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from infra.cache import MemoryCache

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "fund_monitor",
    "scope": "private",
    "input": ["user_id"],
    "output": "fund_scan",
    "cost": "cpu",
    "tags": ["基金盯盘", "估值", "风控", "异动"],
    "description": "基金持仓CRUD+实时估值+风控指标+异动检测+全持仓扫描",
    "layer": "data",
    "priority": 1,
}

# ---- 持仓数据路径 ----
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
_MONITOR_DIR = _DATA_DIR / "monitor"

# ---- 缓存 ----
_est_cache = MemoryCache(default_ttl=3600)  # {"fund_estimate": {"data": estimate, "ts": float}}
_EST_TTL = 300  # 估值全量缓存 5 分钟
_nav_cache = MemoryCache(default_ttl=3600)
_NAV_TTL = 3600  # 净值历史缓存 1 小时
_name_cache = MemoryCache(default_ttl=3600)  # {"fund_name": {"data": name, "ts": float}}
_NAME_TTL = 86400  # 名称表缓存 24 小时


def _fund_file(user_id: str = "default") -> Path:
    """按 userId 隔离基金持仓文件"""
    if user_id == "default":
        return _DATA_DIR / "fund_holdings.json"  # 向后兼容
    return _DATA_DIR / f"fund_holdings_{user_id}.json"


# ============================================================
# 1. 基金持仓 CRUD（支持多用户）
# ============================================================

def load_fund_holdings(user_id: str = "default") -> list:
    """加载基金持仓列表（v9.5.122: 自动从 V4 transactions 补全缺失基金）"""
    f = _fund_file(user_id)
    existing = []
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            existing = data if isinstance(data, list) else []
        except Exception:
            existing = []
    
    # v9.5.122: 以 V4 transactions 为 source of truth，补全盯盘系统缺失的基金
    try:
        existing = _sync_from_transactions(existing, user_id)
    except Exception:
        pass
    
    return existing


def _sync_from_transactions(existing: list, user_id: str) -> list:
    """从 V4 transactions 补全盯盘系统中缺失的基金（单向同步）"""
    from services.persistence import load_user
    user = load_user(user_id)
    portfolio = user.get("portfolio") or {}
    txns = portfolio.get("transactions") or []
    if not txns:
        return existing
    
    # 聚合 transactions 得到当前持仓
    holdings_map = {}
    for t in txns:
        code = t.get("code", "")
        if not code or len(code) != 6 or not code.isdigit():
            continue
        if code not in holdings_map:
            holdings_map[code] = {"code": code, "name": t.get("name", ""), "shares": 0, "totalCost": 0}
        holdings_map[code]["shares"] += t.get("shares", 0)
        holdings_map[code]["totalCost"] += t.get("amount", 0)
    
    # 过滤掉已清仓的（shares<=0）
    active = {code: h for code, h in holdings_map.items() if h["shares"] > 0}
    if not active:
        return existing
    
    # 检查盯盘系统中缺失的
    existing_codes = set(h.get("code", "") for h in existing)
    added = False
    for code, h in active.items():
        if code not in existing_codes:
            avg_cost = h["totalCost"] / h["shares"] if h["shares"] > 0 else 0
            existing.append({
                "code": code,
                "name": h["name"],
                "shares": round(h["shares"], 4),
                "costNav": round(avg_cost, 6),
                "source": "v4_sync",
            })
            added = True
    
    # 如果有新增，写回文件
    if added:
        f = _fund_file(user_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return existing


def save_fund_holdings(holdings: list, user_id: str = "default"):
    """保存基金持仓列表"""
    f = _fund_file(user_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(holdings, ensure_ascii=False, indent=2), encoding="utf-8")


def add_fund_holding(code: str, name: str = "", cost_nav: float = 0,
                     shares: float = 0, note: str = "", user_id: str = "default") -> dict:
    """添加一只持仓基金"""
    holdings = load_fund_holdings(user_id)
    if any(h["code"] == code for h in holdings):
        return {"error": f"{code} 已在持仓中"}

    if not name:
        name = _get_fund_name(code)

    holding = {
        "code": code,
        "name": name,
        "costNav": cost_nav,
        "shares": shares,
        "note": note,
        "addedAt": datetime.now().isoformat(),
    }
    holdings.append(holding)
    save_fund_holdings(holdings, user_id)
    return {"ok": True, "holding": holding}


def remove_fund_holding(code: str, user_id: str = "default") -> dict:
    """删除一只持仓基金"""
    holdings = load_fund_holdings(user_id)
    before = len(holdings)
    holdings = [h for h in holdings if h["code"] != code]
    if len(holdings) == before:
        return {"error": f"{code} 不在持仓中"}
    save_fund_holdings(holdings, user_id)
    return {"ok": True}


def update_fund_holding(code: str, user_id: str = "default", **kwargs) -> dict:
    """更新持仓信息"""
    holdings = load_fund_holdings(user_id)
    for h in holdings:
        if h["code"] == code:
            for k, v in kwargs.items():
                if k in ("costNav", "shares", "note", "name"):
                    h[k] = v
            save_fund_holdings(holdings, user_id)
            return {"ok": True, "holding": h}
    return {"error": f"{code} 不在持仓中"}


# ============================================================
# 2. 基金名称自动补全
# ============================================================

def _get_fund_name(code: str) -> str:
    """通过 AKShare 查基金名称"""
    try:
        names = _load_fund_names()
        if names is not None:
            row = names[names["基金代码"] == code]
            if len(row):
                return row.iloc[0]["基金简称"]
    except Exception:
        pass
    return code


def _load_fund_names():
    """加载基金名称表（缓存 24h）"""
    cached = _name_cache.get("data")
    if cached is not None:
        return cached
    try:
        from infra.data_source.market.stocks import get_fund_name_list
        df = get_fund_name_list()
        _name_cache.set("data", df, ttl=_NAME_TTL)
        return df
    except Exception:
        return None


# ============================================================
# 3. 实时估值数据
# ============================================================

def _load_estimation_all():
    """加载全市场基金估值（缓存 5min）"""
    cached = _est_cache.get("data")
    if cached is not None:
        return cached
    try:
        from infra.data_source.market.stocks import get_fund_estimated_nav
        df = get_fund_estimated_nav()
        _est_cache.set("data", df, ttl=_EST_TTL)
        return df
    except Exception:
        return None


def _fallback_fund_nav(code: str) -> Optional[dict]:
    """v9.5.122: 当全市场估值表不可用时，用 fundgz 单只查询做 fallback"""
    try:
        from services.market_data import get_fund_nav
        nav_data = get_fund_nav(code)
        if nav_data and nav_data.get("nav") and nav_data["nav"] != "N/A":
            nav_val = float(nav_data["nav"])
            change = nav_data.get("change", "0")
            return {
                "code": code,
                "estNav": nav_val if nav_data.get("is_estimate") else None,
                "estRate": float(change) if change and change != "0" else None,
                "nav": nav_val if not nav_data.get("is_estimate") else float(nav_data.get("official_nav") or nav_val),
                "navRate": float(change) if change and change != "0" else None,
                "prevNav": None,
                "estDev": None,
                "source": "fundgz_fallback",
            }
    except Exception:
        pass
    return None


# v9.5.122: realtime 文件缓存目录
_RT_CACHE_DIR = os.path.join(os.environ.get("DATA_DIR", "data"), "_cache", "fund_rt")
os.makedirs(_RT_CACHE_DIR, exist_ok=True)


def _read_rt_file_cache(code: str, max_age: int = 7200) -> Optional[dict]:
    """读 realtime 文件缓存（默认2h有效，stale 72h内先返回旧数据）"""
    try:
        fp = os.path.join(_RT_CACHE_DIR, f"{code}.json")
        if os.path.exists(fp):
            import json as _j
            with open(fp, "r", encoding="utf-8") as f:
                rec = _j.load(f)
            age = time.time() - rec.get("t", 0)
            if age < max_age:
                return rec.get("v")
            # stale: 72h 内先返旧数据（避免用户看到空/0）
            if age < 259200 and rec.get("v"):
                return rec.get("v")
    except Exception:
        pass
    return None


def _write_rt_file_cache(code: str, data: dict):
    """写 realtime 文件缓存"""
    try:
        import json as _j
        fp = os.path.join(_RT_CACHE_DIR, f"{code}.json")
        with open(fp, "w", encoding="utf-8") as f:
            _j.dump({"v": data, "t": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def get_fund_realtime(code: str) -> Optional[dict]:
    """获取单只基金实时估值
    
    v9.5.122: 三层架构 — 文件缓存(2h/stale72h) → 全市场表 → fundgz 单只查询
    确保任何时候（重启/周末/盘后）都有数据返回，用户永远不看到空。
    """
    # ★ 1. 文件缓存优先（秒回）
    cached = _read_rt_file_cache(code)
    if cached:
        return cached
    
    # ★ 2. 全市场估值表（盘中数据全，一次请求覆盖所有基金）
    result = None
    df = _load_estimation_all()
    if df is not None and not df.empty:
        row = df[df["基金代码"] == code]
        if len(row) > 0:
            r = row.iloc[0]
            cols = r.index.tolist()
            est_val = None
            est_rate = None
            nav_val = None
            nav_rate = None
            prev_nav = None
            est_dev = None
            for c in cols:
                v = r[c]
                if "估算值" in str(c):
                    est_val = _safe_float(v)
                elif "估算增长率" in str(c):
                    est_rate = _safe_pct(v)
                elif "单位净值" in str(c) and "公布" in str(c):
                    nav_val = _safe_float(v)
                elif "日增长率" in str(c) and "公布" in str(c):
                    nav_rate = _safe_pct(v)
                elif c == "估算偏差":
                    est_dev = _safe_pct(v)
                elif "单位净值" in str(c) and "公布" not in str(c) and "估算" not in str(c):
                    prev_nav = _safe_float(v)
            result = {
                "code": code,
                "estNav": est_val,
                "estRate": est_rate,
                "nav": nav_val,
                "navRate": nav_rate,
                "prevNav": prev_nav,
                "estDeviation": est_dev,
            }
    
    # ★ 3. fallback: fundgz 单只查询
    if result is None or (not result.get("nav") and not result.get("estNav")):
        fb = _fallback_fund_nav(code)
        if fb:
            result = fb
    
    # 写文件缓存（下次秒回）
    if result and (result.get("nav") or result.get("estNav")):
        _write_rt_file_cache(code, result)
    
    return result


# ============================================================
# 4. 净值历史 + 回撤/波动率
# ============================================================

def get_fund_nav_history(code: str, days: int = 60) -> list:
    """获取净值历史"""
    now = time.time()
    cache_key = f"{code}_{days}"
    cached = _nav_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from infra.data_source.market.stocks import get_fund_nav_history as _get_fund_nav_hist
        # 用累计净值走势（含分红再投资），避免分红后单位净值下降导致盈亏计算失真
        df = _get_fund_nav_hist(code=code, indicator="累计净值走势")
        if df is None or df.empty:
            # 降级到单位净值
            df = _get_fund_nav_hist(code=code, indicator="单位净值走势")
        if df is None or df.empty:
            raise ValueError("AKShare 空数据")
        df = df.tail(days)
        result = []
        for _, row in df.iterrows():
            # 累计净值走势字段名是"累计净值"，单位净值走势是"单位净值"
            nav_val = _safe_float(row.get("累计净值") or row.get("单位净值"))
            result.append({
                "date": str(row.get("净值日期", "")),
                "nav": nav_val,
                "rate": _safe_float(row.get("日增长率")),
            })
        _nav_cache.set(cache_key, result, ttl=_NAV_TTL)
        return result
    except Exception as e:
        # 2026-04-19 A+: Tushare 降级
        try:
            from services.tushare_data import is_configured, get_fund_nav as ts_nav
            if is_configured():
                ts = ts_nav(code, days=days)
                if ts.get("available") and ts.get("navs"):
                    rows = ts["navs"][-days:]
                    result = []
                    prev_nav = None
                    for r in rows:
                        nav = _safe_float(r.get("unit_nav"))
                        rate = None
                        if prev_nav is not None and prev_nav > 0:
                            rate = round((nav - prev_nav) / prev_nav * 100, 4) if nav is not None else None
                        prev_nav = nav
                        result.append({
                            "date": r.get("nav_date", ""),
                            "nav": nav,
                            "rate": rate if rate is not None else 0,
                        })
                    print(f"[FUND_MONITOR] {code} Tushare 降级: {len(result)} 天")
                    _nav_cache.set(cache_key, result)
                    return result
        except Exception as te:
            print(f"[FUND_MONITOR] {code} Tushare 也失败: {te}")

        # v9.5.79: L3 — 天天基金 EM API（凌晨也能用，不依赖 akshare/tushare）
        try:
            import requests, re, json as _json
            url = "https://api.fund.eastmoney.com/f10/lsjz"
            headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
            pages_needed = max(1, (days + 19) // 20)  # 每页20条
            pages_needed = min(pages_needed, 15)       # 最多15页（300条）
            all_items = []
            for page in range(1, pages_needed + 1):
                try:
                    r = requests.get(url, params={"callback": "x", "fundCode": code,
                                                  "pageIndex": page, "pageSize": 20},
                                     headers=headers, timeout=10)
                    body = re.sub(r"^x\(", "", r.text.strip()).rstrip(")")
                    items = _json.loads(body).get("Data", {}).get("LSJZList", [])
                    if not items:
                        break
                    all_items.extend(items)
                except Exception:
                    break
            if all_items:
                result = []
                prev_nav = None
                for item in reversed(all_items):  # EM 返回的是倒序（最新在前），reversed 后时间升序
                    nav = _safe_float(item.get("LJJZ") or item.get("DWJZ"))
                    date = item.get("FSRQ", "")
                    # 日增长率：先用字段，再自算
                    rate_raw = _safe_float(item.get("JZZZL"))
                    if rate_raw is None and prev_nav is not None and prev_nav > 0 and nav is not None:
                        rate_raw = round((nav - prev_nav) / prev_nav * 100, 4)
                    prev_nav = nav
                    result.append({"date": date, "nav": nav, "rate": rate_raw or 0})
                result = result[-days:]  # 截取最近 days 条
                print(f"[FUND_MONITOR] {code} EM API 降级: {len(result)} 天")
                _nav_cache.set(cache_key, result, ttl=_NAV_TTL)
                return result
        except Exception as em_err:
            print(f"[FUND_MONITOR] {code} EM API 也失败: {em_err}")

        cached_fallback = _nav_cache.get(cache_key)
        return cached_fallback.get("data", []) if isinstance(cached_fallback, dict) else []


def calc_risk_metrics(nav_list: list) -> dict:
    """计算风控指标：最大回撤、波动率、连续下跌天数

    v9.5.72: 加上回撤窗口的具体日期 + 当前距高点位置
    """
    if len(nav_list) < 5:
        return {"maxDrawdown": None, "volatility": None, "downDays": 0}

    # 完整保留 (date, nav) 对，方便定位回撤窗口
    pairs = [(n.get("date", ""), n.get("nav")) for n in nav_list if n.get("nav") is not None]
    navs = [p[1] for p in pairs]
    dates = [p[0] for p in pairs]
    rates = [n["rate"] for n in nav_list if n["rate"] is not None]

    # 最大回撤（同时记录峰值/谷值的索引）
    max_dd = 0
    peak = navs[0] if navs else 0
    peak_idx = 0
    cur_peak_idx = 0
    trough_idx = 0
    for i, n in enumerate(navs):
        if n > peak:
            peak = n
            cur_peak_idx = i
        dd = (peak - n) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            peak_idx = cur_peak_idx
            trough_idx = i

    # 当前距 60 天最高点的距离 + 从谷底反弹幅度
    cur_nav = navs[-1] if navs else 0
    period_peak = max(navs) if navs else 0
    period_trough = min(navs) if navs else 0
    dist_from_peak = (cur_nav - period_peak) / period_peak if period_peak > 0 else 0
    rebound_from_trough = (cur_nav - period_trough) / period_trough if period_trough > 0 else 0

    # 波动率（年化）
    vol = None
    if len(rates) >= 5:
        avg = sum(rates) / len(rates)
        var = sum((r - avg) ** 2 for r in rates) / len(rates)
        vol = round(math.sqrt(var) * math.sqrt(252), 4)

    # 连续下跌天数
    down_days = 0
    for r in reversed(rates):
        if r < 0:
            down_days += 1
        else:
            break

    # 回撤窗口的可读日期（mm/dd 格式）
    def _fmt_md(s: str) -> str:
        # 输入格式可能是 "2026-05-12 00:00:00" 或 "2026-05-12"
        try:
            return s[5:10].replace("-", "/")
        except Exception:
            return s[:10]

    return {
        "maxDrawdown": round(max_dd, 4),
        "ddPeakDate": _fmt_md(dates[peak_idx]) if dates and peak_idx < len(dates) else "",
        "ddTroughDate": _fmt_md(dates[trough_idx]) if dates and trough_idx < len(dates) else "",
        "distFromPeak": round(dist_from_peak, 4),       # 当前距高点（负数=低于高点）
        "reboundFromTrough": round(rebound_from_trough, 4),  # 从谷底反弹幅度
        "volatility": vol,
        "downDays": down_days,
        "weekReturn": round(sum(rates[-5:]), 2) if len(rates) >= 5 else None,
        "navWindowDays": len(navs),  # 实际拉到的天数
    }


# ============================================================
# 5. 异动检测 & 预警信号
# ============================================================

def detect_fund_alerts(code: str, realtime: dict, risk: dict) -> list:
    """检测基金异动，返回预警信号列表（每个 alert 含 code 字段用于去重）
    
    v9.5.124: 统一字段名为 message（与 wxwork_push.py 的 send_stock_alert_to 对齐）
    """
    alerts = []

    est_rate = realtime.get("estRate")
    est_dev = realtime.get("estDeviation")
    max_dd = risk.get("maxDrawdown")
    week_ret = risk.get("weekReturn")
    down_days = risk.get("downDays", 0)

    # 规则 1：单日估算涨幅 > 2%
    if est_rate is not None and est_rate > 2:
        alerts.append({
            "type": "surge", "code": code,
            "level": "info",
            "message": f"📈 估算涨幅 +{est_rate:.2f}%，关注获利了结时机",
        })

    # 规则 2：单日估算跌幅 > 1.5%
    if est_rate is not None and est_rate < -1.5:
        alerts.append({
            "type": "drop", "code": code,
            "level": "warning",
            "message": f"📉 今日估算跌幅 {est_rate:.2f}%，关注止损线",
        })

    # 规则 3：估算偏差 > 0.5%
    if est_dev is not None and abs(est_dev) > 0.5:
        alerts.append({
            "type": "deviation", "code": code,
            "level": "info",
            "message": f"⚠️ 估算偏差 {est_dev:+.2f}%（实际净值可能与估算差距较大）",
        })

    # 规则 4：近期最大回撤 > 5%（提高门槛，减少噪音）
    # v9.5.72: 文案信息量大幅增加 — 时间窗口 + 回撤区间日期 + 当前位置
    if max_dd is not None and max_dd > 0.05:
        window_days = risk.get("navWindowDays", 60)
        peak_date = risk.get("ddPeakDate", "")
        trough_date = risk.get("ddTroughDate", "")
        dist_peak = risk.get("distFromPeak")  # 负数=当前低于高点
        rebound = risk.get("reboundFromTrough")  # 正数=已从谷底反弹

        # 构建主信息：明确多少天 + 回撤区间
        if peak_date and trough_date:
            main = f"🔻 {window_days}日最大回撤 {max_dd*100:.1f}%（{peak_date}→{trough_date}）"
        else:
            main = f"🔻 {window_days}日最大回撤 {max_dd*100:.1f}%"

        # 构建当前位置：分 3 种状态
        sub = ""
        if dist_peak is not None and rebound is not None:
            if abs(dist_peak) < 0.005:  # 已回到高点附近
                sub = f"，当前已回到高点附近 ✅"
            elif rebound > 0.02:  # 已从谷底反弹超过 2%
                sub = f"，距高点{dist_peak*100:+.1f}%，已从谷底反弹 +{rebound*100:.1f}%"
            else:  # 仍在低位附近
                sub = f"，当前仍在低位（距高点 {dist_peak*100:+.1f}%）"

        alerts.append({
            "type": "drawdown", "code": code,
            "level": "warning",
            "message": main + sub,
        })

    # 规则 5：连续下跌 >= 4 天（提高门槛，3天波动太正常）
    if down_days >= 4:
        alerts.append({
            "type": "consecutive_drop", "code": code,
            "level": "warning",
            "message": f"📉 连续下跌 {down_days} 天，关注是否需要减仓",
        })

    # 规则 6：近一周收益 > 5%（热门异动）
    if week_ret is not None and week_ret > 5:
        alerts.append({
            "type": "hot", "code": code,
            "level": "info",
            "message": f"🔥 近一周涨幅 +{week_ret:.1f}%，可能存在短期过热",
        })

    return alerts


# ============================================================
# 6. 全持仓扫描
# ============================================================

def scan_all_fund_holdings(user_id: str = "default") -> dict:
    """扫描全部基金持仓，返回汇总结果"""
    holdings = load_fund_holdings(user_id)
    if not holdings:
        return {"holdings": [], "alerts": [], "scannedAt": datetime.now().isoformat()}

    # 预加载全市场估值（一次调用覆盖所有基金）
    _load_estimation_all()

    results = []
    all_alerts = []

    def _scan_one(h):
        code = h["code"]
        try:
            rt = get_fund_realtime(code)
            nav_hist = get_fund_nav_history(code, days=30)
            risk = calc_risk_metrics(nav_hist)
            alerts = detect_fund_alerts(code, rt or {}, risk)

            # 计算盈亏
            pnl = None
            pnl_pct = None
            cost = h.get("costNav", 0)
            shares = h.get("shares", 0)
            current_nav = (rt or {}).get("estNav") or (rt or {}).get("nav")
            if cost > 0 and shares > 0 and current_nav:
                pnl = round((current_nav - cost) * shares, 2)
                pnl_pct = round((current_nav - cost) / cost * 100, 2)

            return {
                "code": code,
                "name": h.get("name", code),
                "costNav": cost,
                "shares": shares,
                "realtime": rt,
                "risk": risk,
                "alerts": alerts,
                "pnl": pnl,
                "pnlPct": pnl_pct,
            }
        except Exception as e:
            return {
                "code": code,
                "name": h.get("name", code),
                "error": str(e),
                "alerts": [],
            }

    # 并发扫描（净值历史请求较慢）
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_scan_one, h): h for h in holdings}
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            for a in r.get("alerts", []):
                a["fund"] = f"{r['name']}({r['code']})"
                a["name"] = r["name"]   # 补全 name 字段供推送用
                all_alerts.append(a)

    # 按代码排序
    results.sort(key=lambda x: x["code"])

    scan_result = {
        "holdings": results,
        "alerts": all_alerts,
        "scannedAt": datetime.now().isoformat(),
    }

    # 保存结果文件（按用户隔离）
    _save_scan_result(scan_result, user_id)
    return scan_result


def _save_scan_result(result: dict, user_id: str = "default"):
    """保存扫描结果到 monitor 目录（按用户隔离）"""
    if user_id and user_id != "default":
        d = _DATA_DIR / user_id / "monitor"
    else:
        d = _MONITOR_DIR
    d.mkdir(parents=True, exist_ok=True)
    out = d / "fund_latest.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 7. 工具函数
# ============================================================

def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except (ValueError, TypeError):
        return None


def _safe_pct(v) -> Optional[float]:
    """解析百分比字符串，如 '3.78%' → 3.78"""
    if v is None:
        return None
    s = str(v).replace("%", "").replace("---", "").strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None
