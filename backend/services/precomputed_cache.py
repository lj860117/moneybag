"""
钱袋子 — 凌晨预计算持久化缓存
night_worker.py 凌晨算完后调用 save_precomputed() 写入磁盘
白天 API 调用时优先读这个缓存，过期才实时计算

缓存目录: DATA_DIR/precomputed/
文件格式: {key}_{date}.json
"""

import json
import time
from datetime import datetime, date
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
    "factors": 3600,            # 1小时（北向/融资等变化快）
    "macro": 14400,             # 4小时
    "fear_greed": 3600,         # 1小时
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
    """读取预计算缓存（如果有效）"""
    suffix = f"_{user_id}" if user_id else ""
    filename = f"{key}{suffix}_{date.today()}.json"
    filepath = PRECOMPUTED_DIR / filename

    if not filepath.exists():
        return None

    try:
        record = json.loads(filepath.read_text(encoding="utf-8"))
        ts = record.get("ts", 0)
        ttl = _PRECOMPUTED_TTL.get(key, 7200)

        if time.time() - ts > ttl:
            return None  # 过期

        return record.get("data")
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
