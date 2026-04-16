"""
钱袋子 — 操作日志
Phase 0 任务 1.7 | 记录关键操作（交易、设置变更、AI 调用等）

存储：data/audit/YYYY-MM-DD.jsonl（每天一个文件，JSONL 追加写入）
查看：cat data/audit/$(date +%F).jsonl | python3 -m json.tool --json-lines
"""
import json
import os
from datetime import datetime, date
from pathlib import Path
from config import DATA_DIR

MODULE_META = {
    "name": "audit_log",
    "scope": "private",
    "input": ["action", "user_id"],
    "output": "log_entry",
    "cost": "cpu",
    "tags": ["审计", "日志", "追踪"],
    "description": "关键操作审计日志（JSONL 追加写入）",
    "layer": "infrastructure",
    "priority": 0,
}

AUDIT_DIR = DATA_DIR / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def audit_log(action: str, user_id: str = "", detail: dict = None, level: str = "info"):
    """记录一条审计日志
    
    Args:
        action: 操作类型（如 "transaction_add", "preference_update", "llm_call"）
        user_id: 操作用户
        detail: 附加信息（字典）
        level: 日志级别（info / warn / error）
    """
    entry = {
        "ts": datetime.now().isoformat(),
        "action": action,
        "user_id": user_id,
        "level": level,
    }
    if detail:
        entry["detail"] = detail

    log_file = AUDIT_DIR / f"{date.today()}.jsonl"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[AUDIT] ⚠️ 写入失败: {e}")


def get_today_logs(limit: int = 100) -> list:
    """获取今日审计日志"""
    log_file = AUDIT_DIR / f"{date.today()}.jsonl"
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    logs = []
    for line in lines[-limit:]:
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return logs


def cleanup_old_logs(keep_days: int = 30):
    """清理超过 N 天的旧日志"""
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=keep_days)
    count = 0
    for f in AUDIT_DIR.glob("*.jsonl"):
        try:
            file_date = date.fromisoformat(f.stem)
            if file_date < cutoff:
                f.unlink()
                count += 1
        except ValueError:
            pass
    if count:
        print(f"[AUDIT] 🧹 清理 {count} 个过期日志文件")
