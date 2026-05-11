"""
自检报告 API
============
GET  /api/audit/latest     — 返回最新审计报告（frontend banner 用）
GET  /api/audit/history    — 返回历史审计日期列表
POST /api/audit/mark-read  — 将当前报告标记为已读
POST /api/audit/run        — 手动触发一次审计（需要 API_KEY 头保护）
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Header

from config import DATA_DIR

router = APIRouter(tags=["自检审计"])

_AUDIT_DIR = Path(DATA_DIR) / "audit"  # data/audit/ (与 self_audit.py 保持一致)
_LATEST    = _AUDIT_DIR / "latest.json"
_HISTORY   = _AUDIT_DIR / "history"


def _read_latest() -> dict | None:
    if _LATEST.exists():
        try:
            return json.loads(_LATEST.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


@router.get("/api/audit/latest")
def get_audit_latest():
    """返回最新审计报告；若不存在则返回空占位"""
    data = _read_latest()
    if data:
        return data
    return {
        "overall_status": "healthy",
        "banner_title": "",
        "banner_message": "",
        "read": True,
        "timestamp": None,
        "source": "none",
    }


@router.get("/api/audit/history")
def get_audit_history():
    """返回历史审计文件列表（日期 + 状态摘要）"""
    _HISTORY.mkdir(parents=True, exist_ok=True)
    files = sorted(_HISTORY.glob("*.json"), reverse=True)
    items = []
    for fp in files[:30]:  # 最多返回最近 30 条
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            items.append({
                "date": fp.stem,
                "overall_status": d.get("overall_status", "unknown"),
                "health_score": d.get("stats", {}).get("health_score", None),
                "banner_title": d.get("banner_title", ""),
            })
        except Exception:
            items.append({"date": fp.stem, "overall_status": "unknown"})
    return {"history": items, "count": len(items)}


@router.post("/api/audit/mark-read")
def mark_audit_read():
    """将最新报告标记为已读（前端关闭 banner 时调用）"""
    if not _LATEST.exists():
        return {"ok": True, "message": "no report"}
    try:
        data = json.loads(_LATEST.read_text(encoding="utf-8"))
        data["read"] = True
        _LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"标记失败: {e}")


@router.post("/api/audit/run")
async def trigger_audit(x_audit_key: str = Header(default="")):
    """手动触发审计（需要环境变量 AUDIT_RUN_KEY 匹配，或留空则本地调试模式）"""
    expected = os.environ.get("AUDIT_RUN_KEY", "")
    if expected and x_audit_key != expected:
        raise HTTPException(403, "Invalid audit key")

    try:
        from use_cases.self_audit import run_weekly_audit
        report = run_weekly_audit()
        return {"ok": True, "overall_status": report.get("overall_status"), "report": report}
    except Exception as e:
        raise HTTPException(500, f"审计失败: {e}")
