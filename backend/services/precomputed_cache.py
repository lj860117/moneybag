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

# v9.5.123: TTL延长 — 凌晨预算的数据白天全天可用(收盘后才更新)
# 之前2h过短导致AI对话读不到预热数据
_PRECOMPUTED_TTL = {
    "recommendations": 43200,   # 12小时(凌晨算→晚上过期)
    "decisions": 43200,         # 12小时
    "daily_signal": 43200,      # 12小时(v9.5.123: 2h→12h,盘中不会变)
    "sector_rotation": 14400,   # 4小时(盘中midday会刷新)
    "broker_consensus": 43200,  # 12小时
    "scenarios": 43200,         # 12小时
    "factors": 14400,           # 4小时(盘中midday刷新兜底)
    "macro": 43200,             # 12小时
    "fear_greed": 14400,        # 4小时(盘中变化大)
    "valuation": 43200,         # 12小时(盘中不变)
}


def save_precomputed(key: str, data: dict, user_id: str = ""):
    """保存预计算结果到磁盘（原子写：tmp + rename，防断电损坏）

    FIX 2026-08-09: 排查代码漂移时发现 v9.5.123 调整 TTL 时误删了服务器
    版本一直保留的原子写保护（fsync+rename），改成了简单 write_text()，
    有写入过程中断电/进程被杀导致 json 文件半写损坏的风险。合并回来，
    TTL 调整保留不变。
    """
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

    # 原子写：先写临时文件，再 rename（与 persistence.atomic_write_json 同模式）
    import os
    import tempfile
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(PRECOMPUTED_DIR), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(filepath))
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

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
