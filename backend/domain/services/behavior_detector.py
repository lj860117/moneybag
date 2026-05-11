"""
Behavior Detector -- 行为归因检测主入口
========================================
检测用户交易中的行为偏差模式，用具体数据描述交易习惯。

核心规则：
  - ⚠️ 只检测 transaction_type="manual" 的交易
  - 定投/再平衡/分红不计入偏差统计
  - 不输出投资建议，只描述行为模式
  - 不假设有实时市场数据（RSI、PE 分位为收盘后数据）

7 种偏差检测实现见 behavior_checks.py。

设计文档：docs/design/m7-plus/05-batch-m8-dynamic-threshold.md
不变式 #8：domain/services 之间禁止互相 import
不变式 #9：domain/ 不 import infra/
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Protocol


# ============================================================
# 协议（市场数据依赖注入）
# ============================================================

class MarketDataProtocol(Protocol):
    """市场数据接口（RSI、PE 分位、沪深 300 日涨幅）"""

    def get_rsi(self, code: str, trade_date: date) -> Optional[float]:
        """获取指定证券在交易日的 RSI（0-100）"""
        ...

    def get_recent_gain(self, code: str, trade_date: date, days: int = 20) -> Optional[float]:
        """获取近 N 日涨幅（0-1 范围，如 0.15=15%）"""
        ...

    def get_csi300_daily_return(self, trade_date: date) -> Optional[float]:
        """获取沪深 300 当日收益率（-1~1）"""
        ...

    def get_pe_percentile(self, code: str, trade_date: date) -> Optional[float]:
        """获取 PE 历史分位（0-1，0=最便宜）"""
        ...

    def get_unrealized_pnl(self, code: str, trade_date: date, avg_cost: float) -> Optional[float]:
        """获取浮动盈亏比例（负数=浮亏）"""
        ...


# ============================================================
# 常量区（行为归因阈值）
# ============================================================

CHASING_RSI_THRESHOLD: float = 70.0    # 追高 RSI 阈值
CHASING_GAIN_THRESHOLD: float = 0.15   # 追高近 20 日涨幅阈值
FOMO_MARKET_GAIN: float = 0.02         # FOMO 大涨日阈值（沪深300 单日>2%）
OVER_TRADING_HOLDING_DAYS: int = 30    # 过度交易持仓周期阈值
OVER_TRADING_MONTHLY_COUNT: int = 5    # 过度交易月交易次数阈值（预留）
HIGH_PE_PERCENTILE: float = 0.70       # 高位加仓 PE 分位阈值
ANCHORING_MONTHS: int = 3              # 锚定效应月数阈值


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BehaviorPattern:
    """行为偏差模式"""
    pattern_type: str           # "chasing_high" | "stop_loss_inconsistent" | ...
    severity: str               # "mild" | "moderate" | "severe"
    evidence_count: int         # 符合该模式的交易次数
    total_relevant: int         # 相关交易总次数
    ratio: float                # evidence_count / total_relevant
    description: str            # 人类可读的结论
    supporting_trades: list[str] = field(default_factory=list)  # 支撑证据的交易 ID
    market_context: Optional[str] = None  # 市场背景（波动率档位）


@dataclass
class SimpleTransaction:
    """检测用的简化交易记录（从 Transaction 映射）"""
    trade_id: str               # 交易唯一标识
    trade_date: date            # 交易日期
    code: str                   # 证券代码
    name: str                   # 证券名称
    direction: str              # "buy" | "sell"
    amount: float               # 交易金额
    transaction_type: str       # "manual" | "auto_invest" | "rebalance" | "dividend"
    industry: str = ""          # 行业分类（可为空）
    avg_cost: float = 0.0       # 平均成本（用于锚定效应检测）


# ============================================================
# 前置过滤
# ============================================================

def _filter_manual_only(transactions: list[SimpleTransaction]) -> list[SimpleTransaction]:
    """只保留手动交易（行为归因唯一检测目标）。

    过滤逻辑在函数入口处执行，确保定投/再平衡/分红不计入任何偏差统计。
    """
    return [t for t in transactions if t.transaction_type == "manual"]


# ============================================================
# 主入口
# ============================================================

def detect_patterns(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol] = None,
    dynamic_threshold: Optional[float] = None,
    lookback_months: int = 3,
) -> list[BehaviorPattern]:
    """行为偏差检测主入口。

    ⚠️ 前置过滤：只检测 transaction_type="manual" 的交易。

    Args:
        transactions: 交易记录列表（含所有类型）
        market_data: 市场数据接口（RSI/PE/涨幅）
        dynamic_threshold: 动态阈值（来自 deviation_thresholds.get_tolerance()）
        lookback_months: 回溯月数（默认 3）

    Returns:
        list[BehaviorPattern] — 检测到的行为偏差列表（按严重程度排序）
    """
    # 延迟导入避免循环依赖（behavior_checks 导入本模块的数据结构）
    from domain.services.behavior_checks import (
        detect_chasing_high,
        detect_stop_loss_inconsistent,
        detect_confirmation_bias,
        detect_fomo,
        detect_over_trading,
        detect_high_pe_adding,
        detect_anchoring,
    )

    # 前置过滤：只保留手动交易
    manual_trades = _filter_manual_only(transactions)

    if not manual_trades:
        return []

    # 按回溯月数过滤
    cutoff_date = date.today() - timedelta(days=lookback_months * 30)
    manual_trades = [t for t in manual_trades if t.trade_date >= cutoff_date]

    if len(manual_trades) < 3:
        return []  # 交易数据不足，不产出结论

    # 市场波动率背景（可选增强）
    market_context: Optional[str] = None
    if dynamic_threshold is not None:
        if dynamic_threshold <= 0.05:
            market_context = "低波动率环境（容忍 5%）"
        elif dynamic_threshold <= 0.07:
            market_context = "中等波动率环境（容忍 7%）"
        else:
            market_context = "高波动率环境（容忍 10%）"

    # 运行 7 种检测
    detectors = [
        detect_chasing_high,
        detect_stop_loss_inconsistent,
        detect_confirmation_bias,
        detect_fomo,
        detect_over_trading,
        detect_high_pe_adding,
        detect_anchoring,
    ]

    patterns: list[BehaviorPattern] = []
    for detector in detectors:
        result = detector(manual_trades, market_data)
        if result:
            # 注入市场背景
            if market_context:
                result.market_context = market_context
            patterns.append(result)

    # 按严重程度排序
    severity_order = {"severe": 0, "moderate": 1, "mild": 2}
    patterns.sort(key=lambda p: severity_order.get(p.severity, 9))

    return patterns


# ============================================================
# 导出
# ============================================================

__all__ = [
    "BehaviorPattern",
    "SimpleTransaction",
    "MarketDataProtocol",
    "detect_patterns",
    "_filter_manual_only",
]
