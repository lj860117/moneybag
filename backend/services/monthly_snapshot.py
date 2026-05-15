"""
钱袋子 — 月度净资产快照
========================
每月 1 号凌晨自动保存一次净资产快照，用于趋势展示。

存储路径: data/{user_hash}/snapshots/YYYYMM.json
内容: {month, net_worth, breakdown, timestamp}
"""
import json
from datetime import datetime
from pathlib import Path
from config import DATA_DIR


def save_monthly_snapshot(user_id: str) -> dict | None:
    """保存当月净资产快照

    幂等操作：同一个月重复调用只写入一次（已有则跳过）。
    """
    from hashlib import sha256
    user_hash = sha256(user_id.encode()).hexdigest()
    month_key = datetime.now().strftime("%Y%m")

    snapshot_dir = DATA_DIR / "users" / user_hash / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = snapshot_dir / f"{month_key}.json"

    # 幂等：已存在则跳过
    if snapshot_file.exists():
        return json.loads(snapshot_file.read_text(encoding="utf-8"))

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
        "month": month_key,
        "month_label": datetime.now().strftime("%Y年%m月"),
        "net_worth": nw["netWorth"],
        "investment": (breakdown.get("investment") or {}).get("total", 0),
        "cash": (breakdown.get("cash") or {}).get("total", 0),
        "property": (breakdown.get("property") or {}).get("total", 0),
        "liability": (breakdown.get("liability") or {}).get("total", 0),
        "health_score": nw.get("healthScore", 0),
        "timestamp": datetime.now().isoformat(),
    }

    snapshot_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SNAPSHOT] 保存 {user_id} {month_key} 净资产快照: ¥{snapshot['net_worth']:,.0f}")
    return snapshot


def get_monthly_trend(user_id: str, months: int = 12) -> list:
    """获取最近 N 个月的净资产趋势

    Returns:
        [{"month": "202501", "month_label": "2025年01月", "net_worth": 123456, ...}, ...]
    """
    from hashlib import sha256
    user_hash = sha256(user_id.encode()).hexdigest()
    snapshot_dir = DATA_DIR / "users" / user_hash / "snapshots"

    if not snapshot_dir.exists():
        return []

    files = sorted(snapshot_dir.glob("*.json"), reverse=True)[:months]
    trend = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            trend.append(data)
        except Exception:
            continue

    # 按月份正序返回
    trend.sort(key=lambda x: x.get("month", ""))
    return trend


def save_all_users_snapshot():
    """为所有用户保存月度快照（供 night_worker 月初调用）"""
    users_dir = DATA_DIR / "users"
    if not users_dir.exists():
        return

    count = 0
    for user_file in users_dir.glob("*.json"):
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
            user_id = data.get("userId") or data.get("id", "")
            if user_id:
                result = save_monthly_snapshot(user_id)
                if result:
                    count += 1
        except Exception:
            continue

    print(f"[SNAPSHOT] 月度快照完成: {count} 个用户")
    return count
