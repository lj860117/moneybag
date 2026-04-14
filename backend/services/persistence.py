"""
钱袋子 — 数据持久化
用户数据文件读写
"""
import json
import hashlib
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
    "description": "用户数据文件读写（SHA256路径隔离）",
    "layer": "data",
    "priority": 1,
}

# ---- 持久化工具 ----
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def load_user(user_id: str) -> dict:
    f = _user_file(user_id)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"userId": user_id, "portfolio": None, "ledger": [], "createdAt": datetime.now().isoformat(), "updatedAt": datetime.now().isoformat()}

def save_user(data: dict):
    data["updatedAt"] = datetime.now().isoformat()
    f = _user_file(data["userId"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


