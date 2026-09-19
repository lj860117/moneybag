"""
基金持仓 & 盯盘引擎 — 独立 service
职责：
  1. 基金持仓 CRUD（后端 JSON 持久化）
  2. 实时估值 / 净值 / 持仓明细
  3. 风控指标（回撤、波动率、估算偏差）
  4. 异动检测 & 预警信号
  5. 全持仓扫描（供 cron 脚本调用）
"""
import config
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
from services.holdings_store import (
    HoldingsList,
    corrupt_write_refusal,
    load_holdings,
    save_holdings,
)

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
_DATA_DIR = Path(config.DATA_DIR)
_MONITOR_DIR = _DATA_DIR / "monitor"

# ---- 缓存 ----
_est_cache = MemoryCache(default_ttl=3600)  # {"fund_estimate": {"data": estimate, "ts": float}}
_EST_TTL = 300  # 估值全量缓存 5 分钟
_nav_cache = MemoryCache(default_ttl=3600)
_NAV_TTL = 3600  # 净值历史缓存 1 小时

# 陈旧缓存降级的上限（秒）。数据源全挂时 get_fund_nav_history 会降级用上一批
# 旧净值，但旧净值不能无限期地当新数据用 —— 超过这个时长就判定为数据缺失。
#
# 取 48 小时的理由：净值**每个交易日**才更新一次，遇到周末就是周五收盘 → 周一
# 早上的空档（约 60h 才是极限，但周末通常还有估值/补录），更常见的是周五晚 →
# 周日（约 48h）。取 48h 可以稳稳跨过周末和短假期，不会因为"周末本来就没新净值"
# 而误判成数据源挂了。
#
# 超过 48h 说明数据源已持续不可用（akshare / Tushare / 天天基金三源同时挂了两天），
# 此时宁可明确报「数据缺失」，也不要继续把旧数字当用户净值看 ——
# 否则等于把「静默返回空」换成「静默返回旧值」，回撤/预警数字会一直停在旧值上，
# 用户同样被误导，而且更难察觉（有数字反而比没数字更像真的）。
_STALE_MAX_AGE = 48 * 3600

# 每个 nav cache_key 最后一次**成功写入**的时刻（time.time()）。
# _nav_cache 里存的是裸 list，没有时间维度，而 MemoryCache 的 ttl 语义是
# "多长之后不再作为新数据使用"，不是"这份数据有多旧"，两者不能混用。
# 这里单独记一个写入时刻，只为判断降级时的陈旧程度，不另起一套缓存。
_nav_written_at: dict = {}


def _mark_nav_written(cache_key: str) -> None:
    """记录 nav 缓存最后一次成功写入的时刻。

    必须在 `_nav_cache.set(...)` 的**同一处**调用，保证两份状态同步 ——
    只更新其中一个会让降级判断读到错误的数据年龄。

    Args:
        cache_key: `f"{code}_{days}"` 形式的缓存键。
    """
    _nav_written_at[cache_key] = time.time()


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

def _warn_if_unknown_user(user_id: str) -> None:
    """user_id 在 users 目录下查无此人时打一条告警（不抛异常、不改变返回值）。

    踩坑背景（v9.9.12）：用户文件在 config.USERS_DIR 下按
    **sha256(user_id)[:16]** 命名（见 services/persistence.py:248 `_user_file`），
    而本模块的 `_fund_file()` 是按**用户名原文**拼 `fund_holdings_{user_id}.json`。
    一旦调用方把哈希串当 user_id 传进来，_fund_file 指向不存在的文件 →
    existing=[] → _sync_from_transactions 里 load_user(哈希) 也取不到 →
    **整个 load_fund_holdings 静默返回 []**，没有任何提示，排查时极易误判成
    "数据被误删了"。这里只加一条日志，让下次踩坑的人一眼看到。

    Args:
        user_id: 传入的用户标识（可能是用户名，也可能是哈希串）。
    """
    if not user_id or user_id == "default":
        return
    try:
        from pathlib import Path as _P
        import hashlib

        users_dir = _P(getattr(config, "USERS_DIR", _DATA_DIR / "users"))
        direct = users_dir / f"{user_id}.json"
        hashed = users_dir / f"{hashlib.sha256(user_id.encode()).hexdigest()[:16]}.json"
        # 哈希串本身也可能被当成"用户名"再哈希一次，两种都查一遍
        hashed_of_hashed = None
        if direct.exists() or hashed.exists():
            return
        # 也可能是先按用户名哈希后、又拿哈希去当 user_id 用 —— 那种情况下
        # 磁盘上存在的是 sha256(哈希)[:16].json，同样接受
        hashed_of_hashed = users_dir / (
            f"{hashlib.sha256(hashed.stem.encode()).hexdigest()[:16]}.json")
        if hashed_of_hashed.exists():
            return
        print(f"[FUND_MONITOR] ⚠️ load_fund_holdings: 未知 user_id {user_id!r}，"
              f"用户文件不存在（已查 {direct.name} / {hashed.name}），将返回空持仓")
    except Exception:
        # 告警本身绝不能影响主流程
        pass


def load_fund_holdings(user_id: str = "default") -> list:
    """加载基金持仓列表（v9.5.122: 自动从 V4 transactions 补全缺失基金）

    返回 `HoldingsList`（list 子类）。`.load_state` 区分：
      - `"ok"` / `"missing"` : 正常路径，之后允许 transactions 单向补全并写回
      - `"corrupt"`          : 文件损坏 —— 直接返回，**不补全、不写回**，
                               损坏文件已备份为 `<原名>.corrupt-<ts>`。
                               （若在 corrupt 时仍走补全写回，等于用一个来自
                               transactions 的子集覆盖坏文件，可能丢掉文件里
                               那些 transactions 没有的持仓。）
    """
    _warn_if_unknown_user(user_id)
    f = _fund_file(user_id)
    existing = load_holdings(f, label=f"基金持仓[{user_id}]")

    if existing.corrupt:
        return existing

    # v9.5.122: 以 V4 transactions 为 source of truth，补全盯盘系统缺失的基金
    try:
        synced = _sync_from_transactions(list(existing), user_id)
        return HoldingsList(synced, load_state=existing.load_state)
    except Exception as e:
        # 补全是加法、失败不影响已读到的持仓；但错误必须可见
        print(f"[FUND_MONITOR] transactions 补全失败（不影响已读持仓）: {e}")
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
    
    # 如果有新增，写回文件（原子写：tmp+fsync+rename）
    if added:
        save_holdings(_fund_file(user_id), existing)

    return existing


def save_fund_holdings(holdings: list, user_id: str = "default"):
    """保存基金持仓列表（原子写：tmp + fsync + rename，禁止裸 write_text）。

    旧实现是 `f.write_text(json.dumps(...))` —— 写到一半进程被杀会留下半个
    JSON，进而被 load 静默读成「没有持仓」。
    """
    save_holdings(_fund_file(user_id), holdings)


def add_fund_holding(code: str, name: str = "", cost_nav: float = 0,
                     shares: float = 0, note: str = "", user_id: str = "default") -> dict:
    """添加一只持仓基金"""
    holdings = load_fund_holdings(user_id)
    refusal = corrupt_write_refusal(holdings, _fund_file(user_id))
    if refusal:
        return refusal
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
    refusal = corrupt_write_refusal(holdings, _fund_file(user_id))
    if refusal:
        return refusal
    before = len(holdings)
    holdings = [h for h in holdings if h["code"] != code]
    if len(holdings) == before:
        return {"error": f"{code} 不在持仓中"}
    save_fund_holdings(holdings, user_id)
    return {"ok": True}


def update_fund_holding(code: str, user_id: str = "default", **kwargs) -> dict:
    """更新持仓信息"""
    holdings = load_fund_holdings(user_id)
    refusal = corrupt_write_refusal(holdings, _fund_file(user_id))
    if refusal:
        return refusal
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
_RT_CACHE_DIR = os.path.join(config.DATA_DIR, "_cache", "fund_rt")
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

def get_fund_nav_history(code: str, days: int = 60, force_refresh: bool = False) -> list:
    """获取净值历史

    Args:
        code: 基金代码。
        days: 取最近多少个交易日。
        force_refresh: True 时**跳过读缓存**（仍照常写缓存），用于必须拿到
            最新净值的场景。默认 False，保持既有行为。

    v9.9.12 FIX-C (2026-09-07 事故)：收盘复盘链路必须能绕过缓存。
    run_close_review() 开头先跑 run_scan()，几分钟后第 4 段「持仓预警」又调
    本函数 —— 两者相隔几分钟，而 _NAV_TTL = 3600（1 小时），必然命中 scan 刚
    写入的快照。基金净值通常 20:00-23:00 陆续公布，21:02 复盘时应该拿最新值。

    口径（v9.9.57 定标，勿再"顺手统一"）
    -----------------------------------
    本函数服务的是「**最大回撤 / 波动率 / 回测**」（见 :func:`calc_risk_metrics`
    与 ``scripts/stock_monitor_cron.py`` 的持仓预警），权威口径是 **累计净值**
    （``accum``）：累计净值在分红除权日不跳空，回撤才不会被"分红除权"误判成
    暴跌。用**单位净值**算回撤，分红日会出现一个纯属记账口径的假跳空。

    三条降级路径**必须同口径**，否则同一只基金昨天的 nav 与今天的 nav 相差
    accum/unit 倍（002163 实测：单位 2.9119 / 累计 4.1558 = 1.427×），
    而 cache_key 里**没有口径维度**，调用方无从分辨自己拿到的是哪一套。

    每条记录额外带一个 ``caliber`` 字段（``"accum"`` / ``"unit"``）供调用方
    自证；数值口径本身由本函数保证三层一致。
    """
    now = time.time()
    cache_key = f"{code}_{days}"

    # v9.9.13 FIX-D2：先留一份「过期也算」的降级底牌，再读缓存。
    # 顺序很关键：MemoryCache.get() 一发现过期就 **删除** 条目，如果先 get()
    # 再 get_stale()，走到降级分支时键已经被删了，get_stale() 恒为 None ——
    # 降级分支依旧是死代码（只修 get_stale 不修顺序，等于没修）。
    stale_snapshot = _nav_cache.get_stale(cache_key)

    if not force_refresh:
        cached = _nav_cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        from infra.data_source.market.stocks import get_fund_nav_history as _get_fund_nav_hist
        # 用累计净值走势（含分红再投资），避免分红后单位净值下降导致盈亏计算失真
        df = _get_fund_nav_hist(code=code, indicator="累计净值走势")
        indicator_used = "累计净值走势"
        if df is None or df.empty:
            # 降级到单位净值
            df = _get_fund_nav_hist(code=code, indicator="单位净值走势")
            indicator_used = "单位净值走势"
        if df is None or df.empty:
            raise ValueError("AKShare 空数据")
        # v9.9.57：如实标注本帧的口径。走满累计走势 = accum；降级到单位走势 = unit。
        l1_caliber = "accum" if indicator_used == "累计净值走势" else "unit"
        df = df.tail(days)
        result = []
        for _, row in df.iterrows():
            # 累计净值走势字段名是"累计净值"，单位净值走势是"单位净值"
            nav_val = _safe_float(row.get("累计净值") or row.get("单位净值"))
            result.append({
                "date": str(row.get("净值日期", "")),
                "nav": nav_val,
                "rate": _safe_float(row.get("日增长率")),
                "caliber": l1_caliber,
            })
        _nav_cache.set(cache_key, result, ttl=_NAV_TTL)
        _mark_nav_written(cache_key)
        return result
    except Exception as e:
        # 2026-04-19 A+: Tushare 降级
        try:
            from services.tushare_data import is_configured, get_fund_nav as ts_nav
            if is_configured():
                ts = ts_nav(code, days=days)
                if ts.get("available") and ts.get("navs"):
                    rows = ts["navs"][-days:]
                    # ---------------------------------------------------------
                    # v9.9.57 口径统一（P1）：这里原本无条件取 ``unit_nav``，是三条
                    # 降级路径里**唯一**的口径偏离层 —— L1/L3 正常返回累计净值，
                    # 只有 L1 挂掉、本层顶上时才返回单位净值，同一只基金的 nav
                    # 会因"那天哪个数据源活着"而相差 accum/unit 倍。
                    #
                    # ⚠️ 只认 ``accum_nav``（累计净值），**绝不认 ``adj_nav``**：
                    # adj_nav 是复权净值（分红再投资复利），与 L1/L3 的「累计净值」
                    # 不是一个量纲 —— 002163 实测 unit 2.9119 / accum 4.1558 /
                    # adj 6.7802。取 adj 会把现在的 1.427× 跳变放大成 2.35×。
                    #
                    # ⚠️ 必须**整段同口径**，不能逐行 ``accum_nav or unit_nav``：
                    # 逐行回退会在同一条序列里混进两个量纲，回撤/波动率会被自己
                    # 造出来的假跳空污染，比"整段都用单位"更糟。故仅当**每一行**
                    # 都有正的 accum_nav 时才整段升为累计口径，否则整段退回单位。
                    # ---------------------------------------------------------
                    accum_series = [_safe_float(r.get("accum_nav")) for r in rows]
                    unit_series = [_safe_float(r.get("unit_nav")) for r in rows]
                    use_accum = bool(rows) and all(
                        v is not None and v > 0 for v in accum_series)
                    l2_caliber = "accum" if use_accum else "unit"
                    nav_series = accum_series if use_accum else unit_series

                    result = []
                    prev_nav = None
                    for r, nav in zip(rows, nav_series):
                        rate = None
                        if prev_nav is not None and prev_nav > 0:
                            rate = round((nav - prev_nav) / prev_nav * 100, 4) if nav is not None else None
                        prev_nav = nav
                        result.append({
                            "date": r.get("nav_date", ""),
                            "nav": nav,
                            "rate": rate if rate is not None else 0,
                            "caliber": l2_caliber,
                        })
                    print(f"[FUND_MONITOR] {code} Tushare 降级: {len(result)} 天"
                          f"（口径 {l2_caliber}）")
                    _nav_cache.set(cache_key, result)
                    _mark_nav_written(cache_key)
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
                # ---------------------------------------------------------
                # v9.9.57 口径统一（P1，与 L2 同一条铁律）：**绝不逐行**
                # ``LJJZ or DWJZ``。
                #
                # LJJZ = 累计净值，DWJZ = 单位净值（2026-09-22 实测 002163：
                # DWJZ 2.9119 / LJJZ 4.1558，与 L1 的累计口径一致）。只认
                # LJJZ，**绝不认 adj_nav**（复权净值是第三个量纲）。
                #
                # 逐行回退会在同一条序列里混进两个量纲（前面 4.1558、缺 LJJZ
                # 的那行 2.9119），下游 ``_get_nav_series`` / ``calc_risk_metrics``
                # 对相邻项做差求日收益，这个人造跳空会被当成一次 -30% 的真实
                # 日收益，污染相关系数、波动率、回撤 —— 比"整段都用单位"更糟。
                #
                # 故：**每条**都有正的 LJJZ → 整段升为累计口径；只要缺一条，
                # 整段退回 DWJZ 单位口径。二选一，不混。
                #
                # ⚠️ 预扫必须在**完整的 ``all_items``** 上做、不能只扫截取后的
                # ``result[-days:]``：「要不要升累计」这个决策本身覆盖的是整个
                # 拉取窗口，只按截取后的片段判定，会让"窗口外某条缺 LJJZ"的
                # 情况整段误升累计。
                # ---------------------------------------------------------
                accum_series = [_safe_float(it.get("LJJZ")) for it in all_items]
                unit_series = [_safe_float(it.get("DWJZ")) for it in all_items]
                use_accum = bool(all_items) and all(
                    v is not None and v > 0 for v in accum_series)
                l3_caliber = "accum" if use_accum else "unit"
                nav_series = accum_series if use_accum else unit_series

                result = []
                prev_nav = None
                for item, nav in zip(reversed(all_items), reversed(nav_series)):  # EM 返回的是倒序（最新在前），reversed 后时间升序
                    date = item.get("FSRQ", "")
                    # 日增长率：先用字段，再自算
                    rate_raw = _safe_float(item.get("JZZZL"))
                    if rate_raw is None and prev_nav is not None and prev_nav > 0 and nav is not None:
                        rate_raw = round((nav - prev_nav) / prev_nav * 100, 4)
                    prev_nav = nav
                    result.append({"date": date, "nav": nav,
                                   "rate": rate_raw or 0, "caliber": l3_caliber})
                result = result[-days:]  # 截取最近 days 条
                print(f"[FUND_MONITOR] {code} EM API 降级: {len(result)} 天"
                      f"（口径 {l3_caliber}）")
                _nav_cache.set(cache_key, result, ttl=_NAV_TTL)
                _mark_nav_written(cache_key)
                return result
        except Exception as em_err:
            print(f"[FUND_MONITOR] {code} EM API 也失败: {em_err}")

        # v9.9.12 FIX-D (2026-09-07 事故)：三源全失败时降级用陈旧缓存。
        #
        # 旧实现两处都错，导致这个降级 **100% 从未生效过**：
        #   1) 用了 `_nav_cache.get()` —— MemoryCache.get() 在发现过期时会先
        #      `del self._data[key]` 再 return None，所以"缓存未过期"这个唯一
        #      能取到值的场景，函数早就走上面的正常 return 了，永远到不了这里；
        #      而真到了这里，键要么不存在、要么已被 get() 删掉，必然是 None。
        #   2) 判了 `isinstance(cached_fallback, dict)` —— 但 MemoryCache 存的是
        #      **裸值**（这里是 list），从不包 {"data":..., "ts":...} 外壳，
        #      所以这个 isinstance 恒为 False，恒返回 []。
        #
        # 结果：数据源全挂时静默返回 []，下游 calc_risk_metrics 直接返回
        # {"maxDrawdown": None...}，回撤/预警整段从推送里消失，用户无从判断
        # 是"没有异动"还是"数据挂了"。
        #
        # 现在改用 get_stale() —— 即使已过期也返回上次的值且不删除。
        # 宁可给用户上一批旧净值（并打日志），也不要静默返回空。
        # 优先用函数开头留的快照（force_refresh=True 时那里没取，这里兜底再取一次）
        stale = stale_snapshot if isinstance(stale_snapshot, list) and stale_snapshot \
            else _nav_cache.get_stale(cache_key)

        if isinstance(stale, list) and stale:
            # v9.9.13 项目7：陈旧缓存降级**有上限**。
            # 只判"有没有"不判"多旧"是不够的 —— 数据源持续挂掉时，每次都会
            # 返回同一批旧净值，回撤/预警数字一直停在旧值上，用户看到的仍是被
            # 误导的数字，等于把「静默返回空」换成了「静默返回旧值」。
            written_at = _nav_written_at.get(cache_key)
            if written_at is None:
                # 有数据却不知道写入时刻（例如进程内先写后丢、或旧版本残留）
                # —— 保守起见不降级，按数据缺失处理并说明原因。
                print(f"[FUND_MONITOR] {code} 数据源全失败，虽有陈旧缓存"
                      f"({len(stale)}天)但无写入时间戳，无法判断陈旧程度，按缺失处理")
                return []

            age = now - written_at
            if age > _STALE_MAX_AGE:
                print(f"[FUND_MONITOR] {code} 数据源全失败，陈旧缓存已陈旧 "
                      f"{age / 3600:.1f}h，超过上限 {_STALE_MAX_AGE / 3600:.1f}h，"
                      f"判定为数据缺失（不再用旧净值顶替）")
                return []

            print(f"[FUND_MONITOR] {code} 数据源全失败，降级用陈旧缓存"
                  f"({len(stale)}天，已陈旧 {age / 3600:.1f}h)")
            return stale

        print(f"[FUND_MONITOR] {code} 数据源全失败且无陈旧缓存可用")
        return []


# ---------------------------------------------------------------------------
# 净值口径标注（v9.9.59）
# ---------------------------------------------------------------------------
# ``get_fund_nav_history`` 自 v9.9.57 起给每条记录带 ``caliber`` 字段
# （``"accum"`` / ``"unit"``），但**一直没人消费**：口径降级到单位净值时，
# 分红除权日会在序列里留一个纯记账的假跳空，回撤/连续下跌被凭空放大，而
# 调用方和最终用户都看不出来——字段埋了等于没埋。
#
# 这里把口径**如实标注**到 metrics 上，供下游（detect_fund_alerts 的推送文案、
# scan_all_fund_holdings 的 risk 字段）直接消费。
#
# ⚠️ 只标注，**绝不换算**：unit → accum 需要逐笔分红明细（除权日 + 每份分红
# 额 + 当日净值），我们没有这份数据；硬凑出来的"累计净值"比如实报单位口径更
# 危险，因为它看起来是对的。宁可标注"这个数可能含除权"，也不给一个假的数。

#: 权威口径：累计净值，分红除权日不跳空。
NAV_CALIBER_ACCUM = "accum"
#: 降级口径：单位净值，分红除权日会跳空。
NAV_CALIBER_UNIT = "unit"
#: 同一条序列里混了两种口径 —— 比整段 unit 更糟，必须显式暴露。
NAV_CALIBER_MIXED = "mixed"
#: 序列里没有 caliber 字段（老缓存 / 第三方构造），无从判定。
NAV_CALIBER_UNKNOWN = "unknown"

_CALIBER_WARNING_UNIT = (
    "单位净值口径：分红除权日会出现纯记账的净值跳空，"
    "回撤/跌幅可能含除权因素，不代表真实亏损"
)
_CALIBER_WARNING_MIXED = (
    "净值序列口径混用：同一段里同时存在累计净值与单位净值，"
    "回撤/跌幅不可信"
)


def resolve_nav_caliber(nav_list: list) -> tuple:
    """从净值序列里解析口径，返回 ``(caliber, warning)``。

    Args:
        nav_list: ``get_fund_nav_history`` 形态的序列
            ``[{"date", "nav", "rate", "caliber"}, ...]``。

    Returns:
        tuple:
            - ``caliber`` (str): :data:`NAV_CALIBER_ACCUM` / ``_UNIT`` /
              ``_MIXED`` / ``_UNKNOWN`。
            - ``warning`` (str | None): ``None`` 表示**无需告警**；否则是可直接
              展示给用户的告警文案。

    判定规则（只看**有 nav 的**那些行，与 ``calc_risk_metrics`` 里 ``pairs``
    的过滤保持一致 —— 没有 nav 的行不参与回撤计算，也不该参与口径判定）:

        - 全是 accum                → ``("accum", None)``，权威口径，不告警
        - 全是 unit                 → ``("unit", 告警)``
        - 两种都有                  → ``("mixed", 告警)``
        - 一条 caliber 都没有       → ``("unknown", None)``

    ⚠️ 为什么 ``unknown`` **不**告警：没有标注 ≠ 是单位口径。凭空告警会改掉
    既有的推送文案（老调用方/老缓存构造的序列都没有 caliber 字段），属于无
    事实依据的行为变更。需要"必须确认是 accum"的调用方请自行判
    ``risk["navCaliber"] != NAV_CALIBER_ACCUM``。
    """
    calibers = {
        str(n.get("caliber"))
        for n in (nav_list or [])
        if isinstance(n, dict) and n.get("nav") is not None and n.get("caliber")
    }
    if not calibers:
        return NAV_CALIBER_UNKNOWN, None
    if calibers == {NAV_CALIBER_ACCUM}:
        return NAV_CALIBER_ACCUM, None
    if calibers == {NAV_CALIBER_UNIT}:
        return NAV_CALIBER_UNIT, _CALIBER_WARNING_UNIT
    return NAV_CALIBER_MIXED, _CALIBER_WARNING_MIXED


def calc_risk_metrics(nav_list: list) -> dict:
    """计算风控指标：最大回撤、波动率、连续下跌天数

    v9.5.72: 加上回撤窗口的具体日期 + 当前距高点位置

    v9.9.59: 额外返回 ``navCaliber`` / ``caliberWarning`` 两个**纯新增**字段
    —— 老调用方不读它们时行为完全不变（向后兼容）。口径降级到单位净值时，
    ``caliberWarning`` 非 None，下游 :func:`detect_fund_alerts` 会把它带进
    推送文案，避免用户把"分红除权"读成"亏了"。详见 :func:`resolve_nav_caliber`。
    """
    caliber, caliber_warning = resolve_nav_caliber(nav_list)

    if len(nav_list) < 5:
        return {"maxDrawdown": None, "volatility": None, "downDays": 0,
                "navCaliber": caliber, "caliberWarning": caliber_warning}

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

    # 当前距窗口最高点的距离 + 从回撤谷底反弹幅度
    #
    # v9.9.11 FIX (2026-09-07 事故)：旧实现用 min(navs)（窗口内**全局**最低点）当谷底。
    # 全局最低点经常出现在回撤峰值**之前**（先涨后跌形态），此时"距高点 -X%"与
    # "已从谷底反弹 +Y%"参照的是两个互不相关的时点，推送文案直接自相矛盾
    # —— 例：华夏先进制造 013107 报「最大回撤 12.0%（08/17→09/04），距高点-12.0%，
    # 已从谷底反弹 +2.9%」，而 09/04 就是当前净值所在位置，反弹幅度应为 0。
    #
    # 正确的谷底必须与 max_dd 严格同一对时点，即上面 474-482 行算出的 trough_idx。
    cur_nav = navs[-1] if navs else 0
    dd_peak_nav = navs[peak_idx] if navs else 0        # 回撤区间的峰（与 max_dd 同对）
    dd_trough_nav = navs[trough_idx] if navs else 0    # 回撤区间的谷（与 max_dd 同对）
    period_peak = max(navs) if navs else 0             # 窗口内全局最高（用于"距高点"）
    dist_from_peak = (cur_nav - period_peak) / period_peak if period_peak > 0 else 0

    # 谷底必须**严格晚于**峰值（trough_idx > peak_idx）才算一次真实回撤 ——
    # 用严格大于而非 >=：序列无回撤时 max_dd 保持 0，peak_idx 与 trough_idx
    # 都停在初始值 0，用 >= 会误判成"从 navs[0] 反弹"，单调上涨序列会报出
    # 「最大回撤 0.0%…已从谷底反弹 +20%」这种荒谬文案（回归测试已锁死）。
    # 数学上 trough_idx > peak_idx ⟺ max_dd > 0，因为 dd>0 必然要求
    # cur_peak_idx < i。另：当前净值低于谷底说明回撤还在扩大，也不是"反弹"。
    # 不满足时返回 None —— 上游 detect_fund_alerts 据此不输出反弹文案，
    # 绝不用别处的低点冒充。
    rebound_from_trough = None
    if navs and trough_idx > peak_idx and dd_trough_nav > 0 and cur_nav >= dd_trough_nav:
        rebound_from_trough = (cur_nav - dd_trough_nav) / dd_trough_nav

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
        # 从「回撤谷底」反弹幅度；谷底不合法时为 None（v9.9.11）
        "reboundFromTrough": (round(rebound_from_trough, 4)
                              if rebound_from_trough is not None else None),
        "ddPeakNav": round(dd_peak_nav, 4),     # 回撤区间峰值净值
        "ddTroughNav": round(dd_trough_nav, 4),  # 回撤区间谷值净值
        "volatility": vol,
        "downDays": down_days,
        "weekReturn": round(sum(rates[-5:]), 2) if len(rates) >= 5 else None,
        "navWindowDays": len(navs),  # 实际拉到的天数（交易日条数，非自然日跨度）
        # v9.9.11: 真实统计区间起止日 —— 供文案展示，避免"30日"被误读成 30 个自然日
        "navStartDate": _fmt_md(dates[0]) if dates else "",
        "navEndDate": _fmt_md(dates[-1]) if dates else "",
        # v9.9.59: 口径自证 —— 老调用方不读这两个键时行为完全不变
        "navCaliber": caliber,
        "caliberWarning": caliber_warning,
    }


# ============================================================
# 5. 异动检测 & 预警信号
# ============================================================

def _caliber_note(risk: dict) -> str:
    """口径降级时的如实标注后缀；权威累计口径返回空串（文案逐字不变）。

    v9.9.59：``calc_risk_metrics`` 已经把口径写进 ``risk["caliberWarning"]``，
    本函数只是把它拼成可追加到推送文案里的形态。正常路径（accum）恒返回 ""，
    所以既有的文案断言不受影响。
    """
    warning = (risk or {}).get("caliberWarning")
    return f"（{warning}）" if warning else ""


def detect_fund_alerts(code: str, realtime: dict, risk: dict) -> list:
    """检测基金异动，返回预警信号列表（每个 alert 含 code 字段用于去重）
    
    v9.5.124: 统一字段名为 message（与 wxwork_push.py 的 send_stock_alert_to 对齐）

    v9.9.59: 回撤类预警在口径降级时会追加一段标注（见 :func:`_caliber_note`）。
    只加在**回撤**这一条上 —— 其余规则的失真要么不可能、要么不显著：
      · 规则 1/2/3 用的是 realtime 的 estRate/estDeviation（估值，与净值序列无关）；
      · 规则 6「近一周涨幅 >5%」：除权跳空只会让涨幅**变小**，不可能凭空造出
        一个"过热"信号；
      · 规则 5「连续下跌 ≥4 天」：一次除权只制造**一天**的假跌，凑不满 4 天，
        触发不了该阈值。
    而规则 4 的 maxDrawdown 是**单点极大值** —— 一个除权跳空就足以把它从 1%
    抬到 42%（分红基金实测），直接越过 5% 门槛变成一条红色预警。口径标注
    必须落在这里。
    """
    alerts = []
    caliber_note = _caliber_note(risk)

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
        rebound = risk.get("reboundFromTrough")  # 正数=已从**回撤谷底**反弹；None=无可信谷底

        # v9.9.11 FIX-B (2026-09-07 事故)：旧文案写「{window_days}日最大回撤」，
        # 而 window_days 实际是 navWindowDays = len(navs) = **交易日条数**。
        # 30 个交易日 ≈ 42 个自然日，于是推送里出现「30日最大回撤 …（07/27→07/29）」
        # 这种"窗口越界"的观感（相对 09/07，07/27 是 42 天前）。
        # 改为直接展示真实统计区间起止日，用户不用再猜是交易日还是自然日。
        win_start = risk.get("navStartDate", "")
        win_end = risk.get("navEndDate", "")
        if win_start and win_end:
            span = f"{win_start}~{win_end}"
        else:
            span = f"近{window_days}个交易日"

        # 构建主信息：回撤区间 + 统计区间
        if peak_date and trough_date:
            main = (f"🔻 最大回撤 {max_dd*100:.1f}%"
                    f"（{peak_date}→{trough_date}，统计区间 {span}）")
        else:
            main = f"🔻 最大回撤 {max_dd*100:.1f}%（统计区间 {span}）"

        # 构建当前位置：分 4 种状态
        # v9.9.11 FIX-A2：旧实现同一句话里出现两个不同的"高点" ——
        # 句首「最大回撤」说的是回撤起始峰（07/27），句尾「距高点」说的是窗口内
        # 全局最高点（可能是回撤之后又创新高的 8 月高点）。统一改成"窗口高点"，
        # 并显式带上谷底日期，让"高/低"各有明确指代。
        sub = ""
        if dist_peak is not None:
            if abs(dist_peak) < 0.005:  # 已回到高点附近
                sub = "，当前已回到高点附近 ✅"
            elif rebound is not None and rebound > 0.02:  # 已从回撤谷底反弹超过 2%
                sub = (f"，距窗口高点{dist_peak*100:+.1f}%"
                       f"，较{trough_date}谷底反弹 +{rebound*100:.1f}%")
            elif rebound is not None:  # 仍在回撤谷底附近
                sub = f"，距窗口高点{dist_peak*100:+.1f}%，仍贴近{trough_date}谷底"
            else:  # 谷底参照不合法，只报距高点，不提反弹
                sub = f"，距窗口高点{dist_peak*100:+.1f}%"

        alerts.append({
            "type": "drawdown", "code": code,
            "level": "warning",
            # v9.9.59：口径降级时把"可能含除权"如实带进推送文案。accum 口径下
            # caliber_note 为空串，文案与改动前逐字一致（向后兼容）。
            "message": main + sub + caliber_note,
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
