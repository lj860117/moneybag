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
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from config import DATA_DIR
from services.persistence import load_user, save_user

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
    
    # 幂等：已存在则跳过
    snapshots = user_data.get("monthly_snapshots", {})
    if month_key in snapshots:
        return snapshots[month_key]
    
    # 获取当前净资产
    try:
        from services.portfolio_overview import get_unified_networth
        nw = get_unified_networth(user_id)
    except Exception as e:
        print(f"[SNAPSHOT] get_unified_networth failed for {user_id}: {e}")
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
    
    # 存储快照
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


def save_all_users_snapshots() -> int:
    """
    为所有用户保存月度快照（供定时任务月初调用）。
    
    Returns:
        成功保存快照的用户数
    """
    users_dir = DATA_DIR / "users"
    if not users_dir.exists():
        return 0
    
    count = 0
    for user_file in users_dir.glob("*.json"):
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
            user_id = data.get("userId")
            if user_id:
                result = save_monthly_snapshot(user_id)
                if result:
                    count += 1
        except Exception as e:
            print(f"[SNAPSHOT] Error processing {user_file}: {e}")
            continue
    
    print(f"[SNAPSHOT] ✓ 为 {count} 个用户保存了月度快照")
    return count


__all__ = [
    "save_monthly_snapshot",
    "get_monthly_snapshots",
    "get_monthly_trend",
    "get_snapshot_by_month",
    "get_snapshot_latest",
    "save_all_users_snapshots",
]
