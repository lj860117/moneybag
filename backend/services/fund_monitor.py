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

# ---- 持仓数据路径 ----
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
_MONITOR_DIR = _DATA_DIR / "monitor"

# ---- 缓存 ----
_est_cache = {"data": None, "ts": 0}
_EST_TTL = 300  # 估值全量缓存 5 分钟
_nav_cache = {}
_NAV_TTL = 3600  # 净值历史缓存 1 小时
_name_cache = {"data": None, "ts": 0}
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
    """加载基金持仓列表"""
    f = _fund_file(user_id)
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


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
    now = time.time()
    if _name_cache["data"] is not None and now - _name_cache["ts"] < _NAME_TTL:
        return _name_cache["data"]
    try:
        import akshare as ak
        df = ak.fund_name_em()
        _name_cache["data"] = df
        _name_cache["ts"] = now
        return df
    except Exception:
        return _name_cache.get("data")


# ============================================================
# 3. 实时估值数据
# ============================================================

def _load_estimation_all():
    """加载全市场基金估值（缓存 5min）"""
    now = time.time()
    if _est_cache["data"] is not None and now - _est_cache["ts"] < _EST_TTL:
        return _est_cache["data"]
    try:
        import akshare as ak
        df = ak.fund_value_estimation_em()
        _est_cache["data"] = df
        _est_cache["ts"] = now
        return df
    except Exception:
        return _est_cache.get("data")


def get_fund_realtime(code: str) -> Optional[dict]:
    """获取单只基金实时估值"""
    df = _load_estimation_all()
    if df is None:
        return None
    row = df[df["基金代码"] == code]
    if len(row) == 0:
        return None
    r = row.iloc[0]
    cols = r.index.tolist()

    # 动态列名（包含日期）
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

    return {
        "code": code,
        "estNav": est_val,
        "estRate": est_rate,
        "nav": nav_val,
        "navRate": nav_rate,
        "prevNav": prev_nav,
        "estDeviation": est_dev,
    }


# ============================================================
# 4. 净值历史 + 回撤/波动率
# ============================================================

def get_fund_nav_history(code: str, days: int = 60) -> list:
    """获取净值历史"""
    now = time.time()
    cache_key = f"{code}_{days}"
    if cache_key in _nav_cache and now - _nav_cache[cache_key]["ts"] < _NAV_TTL:
        return _nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is None or df.empty:
            return []
        df = df.tail(days)
        result = []
        for _, row in df.iterrows():
            result.append({
                "date": str(row.get("净值日期", "")),
                "nav": _safe_float(row.get("单位净值")),
                "rate": _safe_float(row.get("日增长率")),
            })
        _nav_cache[cache_key] = {"data": result, "ts": now}
        return result
    except Exception:
        return _nav_cache.get(cache_key, {}).get("data", [])


def calc_risk_metrics(nav_list: list) -> dict:
    """计算风控指标：最大回撤、波动率、连续下跌天数"""
    if len(nav_list) < 5:
        return {"maxDrawdown": None, "volatility": None, "downDays": 0}

    navs = [n["nav"] for n in nav_list if n["nav"] is not None]
    rates = [n["rate"] for n in nav_list if n["rate"] is not None]

    # 最大回撤
    max_dd = 0
    peak = navs[0] if navs else 0
    for n in navs:
        if n > peak:
            peak = n
        dd = (peak - n) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

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

    return {
        "maxDrawdown": round(max_dd, 4),
        "volatility": vol,
        "downDays": down_days,
        "weekReturn": round(sum(rates[-5:]), 2) if len(rates) >= 5 else None,
    }


# ============================================================
# 5. 异动检测 & 预警信号
# ============================================================

def detect_fund_alerts(code: str, realtime: dict, risk: dict) -> list:
    """检测基金异动，返回预警信号列表"""
    alerts = []

    est_rate = realtime.get("estRate")
    est_dev = realtime.get("estDeviation")
    max_dd = risk.get("maxDrawdown")
    week_ret = risk.get("weekReturn")
    down_days = risk.get("downDays", 0)

    # 规则 1：单日估算涨幅 > 2%
    if est_rate is not None and est_rate > 2:
        alerts.append({
            "type": "surge",
            "level": "info",
            "msg": f"📈 估算涨幅 +{est_rate:.2f}%，关注获利了结时机",
        })

    # 规则 2：单日估算跌幅 > 1.5%
    if est_rate is not None and est_rate < -1.5:
        alerts.append({
            "type": "drop",
            "level": "warning",
            "msg": f"📉 估算跌幅 {est_rate:.2f}%，关注止损线",
        })

    # 规则 3：估算偏差 > 0.5%
    if est_dev is not None and abs(est_dev) > 0.5:
        alerts.append({
            "type": "deviation",
            "level": "info",
            "msg": f"⚠️ 估算偏差 {est_dev:+.2f}%（实际净值可能与估算差距较大）",
        })

    # 规则 4：近一周最大回撤 > 3%
    if max_dd is not None and max_dd > 0.03:
        alerts.append({
            "type": "drawdown",
            "level": "warning",
            "msg": f"🔻 近期最大回撤 {max_dd*100:.1f}%，注意风险",
        })

    # 规则 5：连续下跌 >= 3 天
    if down_days >= 3:
        alerts.append({
            "type": "consecutive_drop",
            "level": "warning",
            "msg": f"📉 连续下跌 {down_days} 天，关注是否需要减仓",
        })

    # 规则 6：近一周收益 > 5%（热门异动）
    if week_ret is not None and week_ret > 5:
        alerts.append({
            "type": "hot",
            "level": "info",
            "msg": f"🔥 近一周涨幅 +{week_ret:.1f}%，可能存在短期过热",
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
                all_alerts.append(a)

    # 按代码排序
    results.sort(key=lambda x: x["code"])

    scan_result = {
        "holdings": results,
        "alerts": all_alerts,
        "scannedAt": datetime.now().isoformat(),
    }

    # 保存结果文件
    _save_scan_result(scan_result)
    return scan_result


def _save_scan_result(result: dict):
    """保存扫描结果到 monitor 目录"""
    _MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    out = _MONITOR_DIR / "fund_latest.json"
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
