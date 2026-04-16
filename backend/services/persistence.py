"""
钱袋子 — 数据持久化
用户数据文件读写（Phase 0 升级：原子写 + 损坏恢复）
"""
import json
import hashlib
import os
import tempfile
from pathlib import Path
from datetime import datetime
from config import USERS_DIR

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "persistence",
    "scope": "private",
    "input": ["user_id"],
    "output": "user_data",
    "cost": "cpu",
    "tags": ["持久化", "用户IO", "SHA256"],
    "description": "用户数据文件读写（SHA256路径隔离 + 原子写）",
    "layer": "data",
    "priority": 1,
}

# ---- 持久化工具 ----
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def load_user(user_id: str) -> dict:
    """安全读取用户 JSON（损坏时尝试从 .bak 恢复）"""
    f = _user_file(user_id)
    backup = f.with_suffix(".json.bak")

    # 1. 尝试读主文件
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[PERSISTENCE] ⚠️ 用户文件损坏: {f}, error: {e}")

    # 2. 主文件不存在或损坏 → 尝试从备份恢复
    if backup.exists():
        try:
            data = json.loads(backup.read_text(encoding="utf-8"))
            print(f"[PERSISTENCE] 🔄 从备份恢复: {backup}")
            atomic_write_json(f, data)  # 恢复主文件
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[PERSISTENCE] 🔴 备份也损坏: {backup}")

    # 3. 全新用户
    return {
        "userId": user_id,
        "portfolio": None,
        "ledger": [],
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }

def save_user(data: dict):
    """原子写用户 JSON（tmp + fsync + rename，防断电损坏）"""
    data["updatedAt"] = datetime.now().isoformat()
    f = _user_file(data["userId"])
    atomic_write_json(f, data)

def atomic_write_json(filepath: Path, data: dict):
    """原子写 JSON：先写 tmp，再 rename（POSIX rename 是原子操作）
    
    Phase 0 新增 — 三方 AI 审查共识：
    直接 write_text() 不是原子操作，写到一半断电/崩溃会导致 JSON 损坏。
    即使已改为 uvicorn ×1，night_worker 仍是独立进程，存在并发写可能。
    """
    filepath = Path(filepath)
    dir_path = filepath.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    # 1. 写入同目录临时文件（同分区才能 rename）
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # 确保数据落盘
        os.replace(tmp_path, str(filepath))  # 原子替换
    except Exception:
        # 写入失败 → 清理临时文件，原文件不受影响
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

def backup_user_files():
    """备份所有用户 JSON（凌晨 06:00 维护任务调用）"""
    import shutil
    count = 0
    for f in USERS_DIR.glob("*.json"):
        if not f.name.endswith(".bak"):
            shutil.copy2(f, f.with_suffix(".json.bak"))
            count += 1
    if count:
        print(f"[PERSISTENCE] 📦 备份 {count} 个用户文件")


