"""
全球市场 API
=============
美股指数、外汇、美联储利率、全球 PE、市场快照、全球→A股影响、决策数据包。

Design doc: docs/design/12-framework-refactor.md §二
"""
import config
import json
import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

from services.global_market import (
    get_us_indices, get_forex_data, get_fed_rate,
    get_global_pe, get_global_snapshot, is_snapshot_degraded,
    analyze_global_impact_on_a_shares, get_decision_data_pack,
)
from services.global_market import _GLOBAL_TTL_DEGRADED

router = APIRouter(tags=["全球市场"])


@router.get("/api/global/indices")
def global_indices():
    """美股三大指数（道琼斯/标普/纳斯达克）"""
    return get_us_indices()


@router.get("/api/global/forex")
def global_forex():
    """外汇数据（美元/人民币）"""
    return get_forex_data()


@router.get("/api/global/fed-rate")
def global_fed_rate():
    """美联储利率"""
    return get_fed_rate()


@router.get("/api/global/pe")
def global_pe():
    """全球 PE 估值对比"""
    return get_global_pe()


@router.get("/api/global/snapshot")
def global_snapshot():
    """全球市场综合快照（4小时文件缓存）"""
    _cache_fp = Path(config.DATA_DIR) / "_cache" / "global_snapshot.json"
    try:
        if _cache_fp.exists():
            payload = json.loads(_cache_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                return data
    except Exception as e:
        print(f"[GLOBAL_SNAPSHOT] 读文件缓存失败: {e}")
    result = get_global_snapshot()
    # 降级态（外汇主源挂了、走离岸 USD/CNH 兜底、或汇率整体拿不到）必须用短 TTL。
    # 2026-09-11 生产证据：08:45 那轮落到离岸兜底，被 4h 文件缓存钉到 12:45；
    # 而 11:21 直连 /api/global/forex 主源已完全正常（source=akshare、
    # degraded=false）——主源恢复了，这条读文件缓存的路径却还在吐 08:45 的
    # 离岸价。P3-6 已经把【内存缓存】的降级 TTL 压到 _GLOBAL_TTL_DEGRADED，
    # 这里必须让【文件缓存】用同一个判据，否则修复会被文件层架空。
    _degraded = is_snapshot_degraded(result)
    _ttl = _GLOBAL_TTL_DEGRADED if _degraded else 14400
    try:
        _cache_fp.parent.mkdir(parents=True, exist_ok=True)
        _now = time.time()
        _cache_fp.write_text(
            json.dumps(
                {
                    "data": result,
                    # ISO 字符串，与 scripts/cache_warmer.py 的 _save_cache()
                    # 保持一致（同一个文件有两个写手，格式不一致会让排查时
                    # 看不出是谁写的）。已确认全仓没有代码读 snapshot 的
                    # cached_at，改格式是安全的。
                    "cached_at": datetime.now().isoformat(),
                    "expires_at": _now + _ttl,
                    # 追加两个字段：排查时 cat 一眼就能看出这份缓存是降级短命的，
                    # 不用再去 data.forex 里翻 degraded。
                    "degraded": _degraded,
                    "ttl": _ttl,
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[GLOBAL_SNAPSHOT] 写文件缓存失败: {e}")
    return result


@router.get("/api/global/impact")
def global_impact():
    """DeepSeek 分析全球→A股影响"""
    return analyze_global_impact_on_a_shares()


@router.get("/api/decision-data")
def decision_data(userId: str = "default"):
    """全量决策数据包（供 Claude 决策用）"""
    return get_decision_data_pack(userId)
