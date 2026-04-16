"""
钱袋子 — 决策日志（Phase 0 新增）

Phase 0：记录聊天中的投资建议（轻量版）
V7：升级为完整的 DecisionMaker 输出（含 schema 校验、Pydantic 模型）

日志格式（JSONL，每行一条记录）：
{
    "decision_id": "uuid",
    "timestamp": "ISO8601",
    "user_id": "LeiJiang",
    "source": "chat|night_worker|manual",
    "intent": "timing|take_profit|general|...",
    "question": "用户的原始问题",
    "advice": "AI 给出的建议",
    "model": "deepseek-chat",
    "confidence": null,         # V7 才有
    "action": null,             # V7: buy/sell/hold
    "stock_code": null,         # V7: 涉及的股票代码
    "executed": false,          # V8: 是否已执行
    "review_status": null,      # V8: pending/verified/wrong
}
"""

import json
import uuid
import os
from datetime import datetime, date
from pathlib import Path

# 日志目录
LOG_DIR = Path(os.environ.get("DATA_DIR", "./data")) / "decision_logs"

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "decision_log",
    "scope": "private",
    "input": ["user_id", "question", "advice"],
    "output": "decision_log_entry",
    "cost": "cpu",
    "tags": ["决策", "日志", "审计"],
    "description": "记录 AI 投资建议（Phase 0 轻量版，V7 升级为完整 DecisionMaker）",
    "layer": "data",
    "priority": 2,
}


def log_decision(
    user_id: str,
    question: str,
    advice: str,
    source: str = "chat",
    intent: str = "general",
    model: str = "deepseek-chat",
    **extra
) -> dict:
    """记录一条决策日志（JSONL 追加）"""
    entry = {
        "decision_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now().isoformat(),
        "user_id": user_id,
        "source": source,
        "intent": intent,
        "question": question[:500],  # 截断防止过大
        "advice": advice[:2000],
        "model": model,
        # V7 字段（Phase 0 预留）
        "confidence": extra.get("confidence"),
        "action": extra.get("action"),
        "stock_code": extra.get("stock_code"),
        "executed": False,
        "review_status": None,
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{date.today()}.jsonl"

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[DECISION_LOG] ⚠️ 写入失败: {e}")

    return entry


def get_decisions(user_id: str = None, days: int = 7) -> list:
    """读取最近 N 天的决策日志"""
    from datetime import timedelta
    entries = []
    for i in range(days):
        d = date.today() - timedelta(days=i)
        log_file = LOG_DIR / f"{d}.jsonl"
        if log_file.exists():
            for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
                if line:
                    try:
                        entry = json.loads(line)
                        if user_id and entry.get("user_id") != user_id:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
    return sorted(entries, key=lambda x: x.get("timestamp", ""), reverse=True)


def get_decision_stats(user_id: str = None, days: int = 30) -> dict:
    """统计决策日志（V8 复盘用）"""
    decisions = get_decisions(user_id, days)
    return {
        "total": len(decisions),
        "by_intent": _count_by(decisions, "intent"),
        "by_source": _count_by(decisions, "source"),
        "recent_5": decisions[:5],
    }


def _count_by(entries: list, key: str) -> dict:
    counts = {}
    for e in entries:
        v = e.get(key, "unknown")
        counts[v] = counts.get(v, 0) + 1
    return counts
