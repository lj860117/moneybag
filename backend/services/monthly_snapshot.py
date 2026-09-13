"""
钱袋子 — 月度快照管理（Phase 3 Batch 1）
=========================================
管理用户月度资产快照。支持两种存储模式：
1. 内嵌在 user JSON 中（monthly_snapshots 字段）
2. 文件系统中（用于历史兼容性）

功能：
- save_monthly_snapshot: 保存月度快照
- get_monthly_snapshots: 获取快照历史
- get_monthly_trend: 获取趋势数据
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from config import DATA_DIR
from services.persistence import load_user, save_user, user_write_lock

# ---- MODULE_META ----
MODULE_META = {
    "name": "monthly_snapshot",
    "scope": "private",
    "input": ["user_id"],
    "output": "monthly_snapshots",
    "cost": "io",
    "tags": ["月度快照", "资产趋势", "Phase3"],
    "description": "保存和查询用户月度资产快照",
    "layer": "service",
    "priority": 2,
}


def save_monthly_snapshot(user_id: str) -> Optional[dict]:
    """
    保存当月净资产快照到用户数据中。
    
    幂等操作：同一个月重复调用只写入一次（已有则跳过）。
    
    Args:
        user_id: 用户ID
    
    Returns:
        快照数据，如果失败返回 None
    """
    user_data = load_user(user_id)
    month_key = datetime.now().strftime("%Y-%m")
    
    # 幂等：已存在则跳过（锁外快速预检，避免白算一遍昂贵的净资产）
    # 真正的幂等判定在下面的锁内重做一次，防止并发下两个线程都通过预检
    snapshots = user_data.get("monthly_snapshots", {})
    if month_key in snapshots:
        return snapshots[month_key]
    
    # 获取当前净资产
    #
    # FIX 2026-09-13（假成功事故）：这里原本写的是
    #     from services.portfolio_overview import get_unified_networth
    # 但 `get_unified_networth` 这个符号**在任何模块都不存在**
    # （`portfolio_overview.py` 只有 `get_portfolio_overview`；
    #  `git log -S get_unified_networth -- backend/services/portfolio_overview.py`
    #  为空，即它从未在该文件出现过）。真实现是
    # `services/unified_networth.py:66 calc_unified_networth`，返回结构
    # `{"netWorth": float, "breakdown": {...}, ...}` 与下方 :63-70 消费的键名一致。
    #
    # 因为 import 写在 try/except 里，ImportError 被静默吞掉 → nw=None →
    # 本函数永远返回 None → 月度快照一个都没存下来，而 night_worker 还打绿勾
    # "✅ 快照完成: 0 个用户"（同一错位 cfo_dashboard 早在 bf01e97 修过）。
    try:
        from services.unified_networth import calc_unified_networth
        nw = calc_unified_networth(user_id)
    except Exception as e:
        # 写 stderr 并带 ERROR 前缀：原来这条是普通 stdout，跟成功日志混在一起，
        # 是"失败被伪装成完成"的一半原因。
        print(f"[SNAPSHOT] ❌ ERROR calc_unified_networth 失败 user={user_id}: {e}",
              file=sys.stderr, flush=True)
        nw = None
    
    if not nw or not nw.get("netWorth"):
        return None
    
    breakdown = nw.get("breakdown", {})
    snapshot = {
        "net_worth": nw.get("netWorth", 0),
        "allocation": {
            "stock": 0.0,
            "bond": 0.0,
            "cash": 0.0,
        },
        "holdings": {},
        "returns": 0.0,
        "recorded_at": datetime.now().isoformat(),
    }
    
    # 获取配置数据
    try:
        from services.portfolio import get_allocation_advice
        allocation_data = get_allocation_advice(user_id)
        if allocation_data and allocation_data.get("current"):
            snapshot["allocation"] = allocation_data["current"]
    except Exception:
        pass
    
    # ── 存储快照：RMW 临界区 ──
    # FIX 2026-08-30（并发丢更新）：
    # 上面的 calc_unified_networth / get_allocation_advice 都是**昂贵计算**
    # （可能走网络取净值），刻意留在锁外，锁内只做 load → 写 → save。
    # 注意锁内**必须重新 load 并重做幂等判定**：锁外那次 load 到现在可能已经
    # 过了几秒，期间别的进程（如 cron）可能已经写入了本月快照，
    # 直接用锁外的 user_data 会把它覆盖掉。
    with user_write_lock(user_id) as acquired:
        if not acquired:
            print(f"[SNAPSHOT] ⚠️ 抢锁超时，放弃保存快照: user={user_id}")
            return None

        user_data = load_user(user_id)
        existing = user_data.get("monthly_snapshots", {})
        if month_key in existing:
            # 并发下别人已经写好了 → 直接返回他写的那份，保持幂等
            return existing[month_key]

        if "monthly_snapshots" not in user_data:
            user_data["monthly_snapshots"] = {}

        user_data["monthly_snapshots"][month_key] = snapshot
        save_user(user_data)
    
    print(f"[SNAPSHOT] 保存 {user_id} {month_key} 快照: ¥{snapshot['net_worth']:,.0f}")
    return snapshot


def get_monthly_snapshots(
    user_id: str,
    months: int = 12,
) -> list[dict]:
    """
    获取最近 N 个月的快照。
    
    Args:
        user_id: 用户ID
        months: 获取最近多少个月（默认 12）
    
    Returns:
        快照列表（按月份正序）
    """
    user_data = load_user(user_id)
    snapshots = user_data.get("monthly_snapshots", {})
    
    # 按月份排序（最新在后）
    sorted_months = sorted(snapshots.keys(), reverse=True)[:months]
    sorted_months.reverse()
    
    result = []
    for month in sorted_months:
        snapshot = snapshots[month].copy()
        snapshot["month"] = month
        result.append(snapshot)
    
    return result


def get_monthly_trend(
    user_id: str,
    months: int = 12,
) -> list[dict]:
    """
    获取月度净资产趋势数据。
    
    Args:
        user_id: 用户ID
        months: 获取最近多少个月
    
    Returns:
        {month, net_worth, returns, ...} 列表
    """
    snapshots = get_monthly_snapshots(user_id, months)
    
    trend = []
    prev_nw = None
    
    for snapshot in snapshots:
        item = {
            "month": snapshot.get("month"),
            "net_worth": snapshot.get("net_worth", 0),
            "allocation": snapshot.get("allocation", {}),
        }
        
        # 计算环比增长
        if prev_nw and prev_nw > 0:
            item["returns"] = (snapshot.get("net_worth", 0) - prev_nw) / prev_nw
        else:
            item["returns"] = 0.0
        
        trend.append(item)
        prev_nw = snapshot.get("net_worth", 0)
    
    return trend


def get_snapshot_by_month(user_id: str, month: str) -> Optional[dict]:
    """
    按月份获取单个快照（month 格式：YYYY-MM）。
    
    Args:
        user_id: 用户ID
        month: 月份（如 "2026-05"）
    
    Returns:
        快照数据，不存在则返回 None
    """
    user_data = load_user(user_id)
    snapshots = user_data.get("monthly_snapshots", {})
    
    if month in snapshots:
        snapshot = snapshots[month].copy()
        snapshot["month"] = month
        return snapshot
    
    return None


def get_snapshot_latest(user_id: str) -> Optional[dict]:
    """
    获取最新的快照。
    
    Returns:
        最新快照，不存在则返回 None
    """
    user_data = load_user(user_id)
    snapshots = user_data.get("monthly_snapshots", {})
    
    if not snapshots:
        return None
    
    latest_month = max(snapshots.keys())
    snapshot = snapshots[latest_month].copy()
    snapshot["month"] = latest_month
    return snapshot


def save_all_users_snapshots() -> dict:
    """
    为所有用户保存月度快照（供定时任务月初调用）。

    Returns:
        {
          "scanned": 扫描到的用户文件数,
          "saved":   成功保存（含同月幂等命中已有快照）的用户数,
          "failed":  [{"userId": ..., "error": ...}, ...]，
                     # 读文件失败 / 缺 userId / 保存抛异常 / 返回 None 都算失败
        }

    FIX 2026-09-13（假成功事故）：原来只返回一个整数（成功数），并且对每个
    失败用户静默 `continue` —— 调用方（night_worker）于是把"扫描到 N 个用户、
    成功 0 个"打成绿勾"✅ 快照完成: 0 个用户"，看起来是正常完成。
    现在返回结构化报告，让调用方能区分"没有用户"（scanned == 0）和
    "用户全都在失败"（scanned > 0 且 saved == 0）。
    调用方 `scripts/monthly_close.py` 与 `scripts/night_worker.py` 已同步改签名。
    """
    users_dir = DATA_DIR / "users"
    report: dict = {"scanned": 0, "saved": 0, "failed": []}
    if not users_dir.exists():
        return report

    for user_file in users_dir.glob("*.json"):
        report["scanned"] += 1

        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SNAPSHOT] ❌ ERROR 读取用户文件失败 {user_file}: {e}",
                  file=sys.stderr, flush=True)
            report["failed"].append(
                {"userId": None, "file": str(user_file), "error": f"读取失败: {e}"})
            continue

        user_id = data.get("userId")
        if not user_id:
            print(f"[SNAPSHOT] ❌ ERROR 用户文件缺少 userId: {user_file}",
                  file=sys.stderr, flush=True)
            report["failed"].append(
                {"userId": None, "file": str(user_file), "error": "缺少 userId 字段"})
            continue

        try:
            result = save_monthly_snapshot(user_id)
        except Exception as e:
            print(f"[SNAPSHOT] ❌ ERROR 保存快照异常 user={user_id}: {e}",
                  file=sys.stderr, flush=True)
            report["failed"].append({"userId": user_id, "error": f"保存异常: {e}"})
            continue

        if result:
            report["saved"] += 1
        else:
            report["failed"].append({
                "userId": user_id,
                "error": "save_monthly_snapshot 返回 None（净资产取不到或抢锁超时）",
            })

    print(f"[SNAPSHOT] 月度快照：扫描 {report['scanned']} 个用户，"
          f"成功 {report['saved']}，失败 {len(report['failed'])}")
    return report


__all__ = [
    "save_monthly_snapshot",
    "get_monthly_snapshots",
    "get_monthly_trend",
    "get_snapshot_by_month",
    "get_snapshot_latest",
    "save_all_users_snapshots",
]
