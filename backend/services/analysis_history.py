"""
钱袋子 — V6 Phase 5: 分析历史系统（模块 G）
统一存档所有来源的分析记录（DeepSeek/Claude/机构），支持查询/对比/清理

存档目录: DATA_DIR/users/{userId_hash}/analysis_history/
格式: {timestamp}_{source}_{type}.json
"""

import os
import json
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from config import DATA_DIR


def _get_history_dir(user_id: str) -> Path:
    """获取用户分析历史目录"""
    uid_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    d = DATA_DIR / "users" / uid_hash / "analysis_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _take_market_snapshot() -> dict:
    """存档时自动拍摄当前市场快照"""
    snapshot = {}
    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        fgi = get_fear_greed_index()
        snapshot["fear_greed"] = fgi.get("score", 50)
        val = get_valuation_percentile()
        snapshot["valuation_pct"] = val.get("percentile", 50)
        snapshot["pe"] = val.get("current_pe", 0)
    except Exception:
        pass
    try:
        from services.market_factors import get_crude_oil_price
        oil = get_crude_oil_price()
        snapshot["oil_sc"] = oil.get("sc_price", 0)
    except Exception:
        pass
    try:
        from services.factor_data import get_northbound_flow
        north = get_northbound_flow()
        snapshot["north_5d"] = north.get("net_flow_5d", 0)
    except Exception:
        pass
    return snapshot


# ============================================================
# 1. 存档
# ============================================================

def save_analysis(user_id: str, source: str, source_label: str,
                  analysis_type: str, analysis_text: str,
                  direction: str = "unknown", confidence: int = 0,
                  metadata: dict = None) -> dict:
    """保存一条分析记录

    Args:
        user_id: 用户 ID
        source: 数据源标识（deepseek/claude/broker/custom）
        source_label: 显示名称（DeepSeek V3/Claude/机构共识）
        analysis_type: 分析类型（stock/fund/full/scenario）
        analysis_text: 分析正文
        direction: 方向（看多/看空/中性/谨慎/unknown）
        confidence: 置信度（0-100）
        metadata: 额外元数据
    """
    now = datetime.now()
    record_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{source}_{analysis_type}"

    record = {
        "id": record_id,
        "source": source,
        "source_label": source_label,
        "type": analysis_type,
        "analysis": analysis_text,
        "direction": direction,
        "confidence": confidence,
        "market_snapshot": _take_market_snapshot(),
        "created_at": now.isoformat(),
        "userId": user_id,
        "metadata": metadata or {},
    }

    # 写文件（原子写）
    history_dir = _get_history_dir(user_id)
    filepath = history_dir / f"{record_id}.json"
    tmp_path = filepath.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        tmp_path.rename(filepath)
        print(f"[HISTORY] 存档: {record_id}")
        return {"ok": True, "id": record_id}
    except Exception as e:
        print(f"[HISTORY] 存档失败: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return {"ok": False, "error": str(e)}


# ============================================================
# 2. 查询历史列表
# ============================================================

def get_analysis_history(user_id: str, source: str = "", analysis_type: str = "",
                         days: int = 30, limit: int = 50) -> list:
    """查询分析历史列表（按时间倒序）"""
    history_dir = _get_history_dir(user_id)
    cutoff = datetime.now() - timedelta(days=days)

    records = []
    for f in sorted(history_dir.glob("*.json"), reverse=True):
        if len(records) >= limit:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 筛选
            if source and data.get("source") != source:
                continue
            if analysis_type and data.get("type") != analysis_type:
                continue
            # 时间筛选
            created = datetime.fromisoformat(data.get("created_at", "2000-01-01"))
            if created < cutoff:
                continue
            # 列表只返回摘要
            records.append({
                "id": data["id"],
                "source": data.get("source"),
                "source_label": data.get("source_label"),
                "type": data.get("type"),
                "direction": data.get("direction"),
                "confidence": data.get("confidence", 0),
                "created_at": data.get("created_at"),
                "preview": data.get("analysis", "")[:120] + "..." if len(data.get("analysis", "")) > 120 else data.get("analysis", ""),
                "market_snapshot": data.get("market_snapshot", {}),
            })
        except Exception as e:
            print(f"[HISTORY] 读取 {f.name} 失败: {e}")

    return records


# ============================================================
# 3. 查询单条详情
# ============================================================

def get_analysis_detail(user_id: str, record_id: str) -> dict:
    """获取单条分析完整内容"""
    history_dir = _get_history_dir(user_id)
    filepath = history_dir / f"{record_id}.json"
    if not filepath.exists():
        return {"error": f"记录不存在: {record_id}"}
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 4. 各来源最新分析（对比视图）
# ============================================================

def get_latest_by_source(user_id: str) -> dict:
    """取各来源的最新一条记录（对比视图用）"""
    history_dir = _get_history_dir(user_id)
    latest = {}  # source → record

    for f in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            src = data.get("source", "unknown")
            if src not in latest:
                latest[src] = {
                    "id": data["id"],
                    "source": src,
                    "source_label": data.get("source_label", src),
                    "type": data.get("type"),
                    "direction": data.get("direction"),
                    "confidence": data.get("confidence", 0),
                    "created_at": data.get("created_at"),
                    "preview": data.get("analysis", "")[:200],
                    "market_snapshot": data.get("market_snapshot", {}),
                }
        except Exception:
            continue

    return {"sources": latest, "count": len(latest)}


# ============================================================
# 5. 清理过期记录
# ============================================================

def cleanup_old_records(user_id: str = "", max_days: int = 90) -> dict:
    """清理过期分析记录（默认90天）

    如果 user_id 为空，清理所有用户的。
    """
    cutoff = datetime.now() - timedelta(days=max_days)
    deleted = 0

    if user_id:
        dirs = [_get_history_dir(user_id)]
    else:
        # 遍历所有用户
        users_dir = DATA_DIR / "users"
        if users_dir.exists():
            dirs = [d / "analysis_history" for d in users_dir.iterdir()
                    if d.is_dir() and (d / "analysis_history").exists()]
        else:
            dirs = []

    for history_dir in dirs:
        for f in history_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(data.get("created_at", "2000-01-01"))
                if created < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception:
                continue

    if deleted > 0:
        print(f"[HISTORY] 清理过期记录: {deleted} 条")
    return {"deleted": deleted, "cutoff_days": max_days}
