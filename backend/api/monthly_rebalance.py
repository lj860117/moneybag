"""
月度再平衡 API（Phase 3 Batch 2）
================================
获取月度快照和再平衡建议
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from services.monthly_snapshot import (
    get_monthly_snapshots, get_monthly_trend, get_snapshot_latest,
    get_snapshot_by_month,
)

router = APIRouter(tags=["月度再平衡"])


@router.get("/api/monthly/snapshots")
async def list_monthly_snapshots(
    userId: str = "default",
    months: int = 12,
):
    """
    获取月度快照列表。
    
    Query Parameters:
    - userId: 用户ID
    - months: 获取最近多少个月（默认 12）
    """
    snapshots = get_monthly_snapshots(userId, months=months)
    
    return {
        "total": len(snapshots),
        "snapshots": snapshots,
    }


@router.get("/api/monthly/trend")
async def get_trend_data(
    userId: str = "default",
    months: int = 12,
):
    """
    获取月度净资产趋势。
    
    用于绘制趋势图表。返回每月的净资产和环比增长率。
    """
    trend = get_monthly_trend(userId, months=months)
    
    if not trend:
        return {
            "trend": [],
            "message": "还没有月度数据",
        }
    
    # 计算统计数据
    net_worths = [t["net_worth"] for t in trend]
    min_nw = min(net_worths) if net_worths else 0
    max_nw = max(net_worths) if net_worths else 0
    latest_nw = net_worths[-1] if net_worths else 0
    
    return {
        "trend": trend,
        "stats": {
            "min_net_worth": min_nw,
            "max_net_worth": max_nw,
            "latest_net_worth": latest_nw,
            "total_months": len(trend),
        },
    }


@router.get("/api/monthly/latest")
async def get_latest_snapshot(userId: str = "default"):
    """获取最新月度快照"""
    snapshot = get_snapshot_latest(userId)
    
    if not snapshot:
        return {
            "snapshot": None,
            "message": "还没有月度快照",
        }
    
    return {
        "snapshot": snapshot,
    }


@router.get("/api/monthly/snapshot/{month}")
async def get_snapshot_by_month_api(
    month: str,
    userId: str = "default",
):
    """
    按月份获取快照（month 格式：YYYY-MM）。
    
    Path Parameters:
    - month: 月份（如 "2026-05"）
    """
    snapshot = get_snapshot_by_month(userId, month)
    
    if not snapshot:
        raise HTTPException(404, f"月份 {month} 没有快照数据")
    
    return {
        "snapshot": snapshot,
    }


@router.get("/api/monthly/suggestions")
async def get_rebalance_suggestions(userId: str = "default"):
    """
    获取月度再平衡建议。
    
    基于当前配置偏离度、市场估值和恐贪指数生成建议。
    """
    try:
        from services.portfolio import get_allocation_advice
        from services.market_data import get_fear_greed_index
        
        allocation_data = get_allocation_advice(userId) or {}
        fgi_data = get_fear_greed_index() or {}
        
        suggestions = []
        
        # 建议 1: 配置偏离
        if allocation_data.get("deviation"):
            deviation = allocation_data["deviation"]
            for category, pct in deviation.items():
                if abs(pct) > 10:
                    label = {"stock": "股票", "bond": "债券", "cash": "现金"}.get(category, category)
                    if pct > 0:
                        suggestions.append({
                            "category": category,
                            "type": "overweight",
                            "message": f"{label}仓位超出目标 {abs(pct):.0f}%，建议适当减仓。",
                            "deviation_pct": pct,
                        })
                    else:
                        suggestions.append({
                            "category": category,
                            "type": "underweight",
                            "message": f"{label}配置低于目标 {abs(pct):.0f}%，建议适当补仓。",
                            "deviation_pct": pct,
                        })
        
        # 建议 2: 市场估值
        fgi = fgi_data.get("score", 50)
        if fgi <= 30:
            suggestions.append({
                "category": "market_signal",
                "type": "buy_signal",
                "message": f"恐贪指数 {fgi}，市场处于低估阶段，可考虑加大权益配置。",
                "fgi": fgi,
            })
        elif fgi >= 75:
            suggestions.append({
                "category": "market_signal",
                "type": "sell_signal",
                "message": f"恐贪指数 {fgi}，市场处于高估阶段，建议谨慎加仓。",
                "fgi": fgi,
            })
        
        return {
            "suggestions": suggestions,
            "total": len(suggestions),
            "current_allocation": allocation_data.get("current", {}),
            "target_allocation": allocation_data.get("target", {}),
        }
    except Exception as e:
        return {
            "suggestions": [],
            "error": str(e),
        }


__all__ = ["router"]
