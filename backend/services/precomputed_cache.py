"""
钱袋子 — 凌晨预计算持久化缓存
night_worker.py 凌晨算完后调用 save_precomputed() 写入磁盘
白天 API 调用时优先读这个缓存，过期才实时计算

缓存目录: DATA_DIR/precomputed/
文件格式: {key}_{date}.json
"""

import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from config import DATA_DIR

PRECOMPUTED_DIR = DATA_DIR / "precomputed"
PRECOMPUTED_DIR.mkdir(parents=True, exist_ok=True)

# 缓存有效期（秒）：白天打开 App 时，如果凌晨已预算就直接用
_PRECOMPUTED_TTL = {
    "recommendations": 14400,   # 4小时（推荐不需要实时）
    "decisions": 14400,         # 4小时
    "daily_signal": 7200,       # 2小时（信号需要相对新）
    "sector_rotation": 7200,    # 2小时
    "broker_consensus": 14400,  # 4小时
    "scenarios": 28800,         # 8小时（情景分析变化慢）
    "factors": 7200,            # 2小时（P0.4a: 从1h→2h，盘中cache_warmer每30分刷新兜底）
    "macro": 14400,             # 4小时
    "fear_greed": 7200,         # 2小时（P0.4a: 从1h→2h）
    "valuation": 7200,          # 2小时
}


def save_precomputed(key: str, data: dict, user_id: str = ""):
    """保存预计算结果到磁盘"""
    suffix = f"_{user_id}" if user_id else ""
    filename = f"{key}{suffix}_{date.today()}.json"
    filepath = PRECOMPUTED_DIR / filename

    record = {
        "key": key,
        "user_id": user_id,
        "data": data,
        "computed_at": datetime.now().isoformat(),
        "ts": time.time(),
    }

    filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PRECOMPUTED] 保存: {filename}")


def get_precomputed(key: str, user_id: str = "") -> dict:
    """读取预计算缓存（如果有效）

    非交易日（周末/节假日）自动延长 TTL——周五的数据周末一直可用。
    """
    suffix = f"_{user_id}" if user_id else ""

    # 先找今天的缓存
    filename = f"{key}{suffix}_{date.today()}.json"
    filepath = PRECOMPUTED_DIR / filename

    # 如果今天没有，找最近 3 天的（覆盖周末）
    if not filepath.exists():
        for days_ago in range(1, 4):
            d = date.today() - timedelta(days=days_ago)
            alt = PRECOMPUTED_DIR / f"{key}{suffix}_{d}.json"
            if alt.exists():
                filepath = alt
                break

    if not filepath.exists():
        return None

    try:
        record = json.loads(filepath.read_text(encoding="utf-8"))
        ts = record.get("ts", 0)
        ttl = _PRECOMPUTED_TTL.get(key, 7200)

        # 非交易日（周末）：TTL 延长到 72 小时
        from datetime import datetime as dt
        if dt.now().weekday() >= 5:  # 周六=5, 周日=6
            ttl = max(ttl, 259200)  # 72小时

        if time.time() - ts > ttl:
            return None  # 过期

        data = record.get("data")
        if data and isinstance(data, dict):
            # 标注数据来源时间
            cached_at = record.get("computed_at", "")
            if cached_at and dt.now().weekday() >= 5:
                data["_cache_note"] = f"数据截至 {cached_at[:16]}（非交易日使用缓存）"
        return data
    except Exception:
        return None


def cleanup_precomputed(max_days: int = 3):
    """清理过期的预计算缓存"""
    cutoff = time.time() - max_days * 86400
    deleted = 0
    for f in PRECOMPUTED_DIR.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    if deleted:
        print(f"[PRECOMPUTED] 清理 {deleted} 个过期文件")
