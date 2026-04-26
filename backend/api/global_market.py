"""
全球市场 API
=============
美股指数、外汇、美联储利率、全球 PE、市场快照、全球→A股影响、决策数据包。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

from services.global_market import (
    get_us_indices, get_forex_data, get_fed_rate,
    get_global_pe, get_global_snapshot,
    analyze_global_impact_on_a_shares, get_decision_data_pack,
)

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
    """全球市场综合快照"""
    return get_global_snapshot()


@router.get("/api/global/impact")
def global_impact():
    """DeepSeek 分析全球→A股影响"""
    return analyze_global_impact_on_a_shares()


@router.get("/api/decision-data")
def decision_data(userId: str = "default"):
    """全量决策数据包（供 Claude 决策用）"""
    return get_decision_data_pack(userId)
