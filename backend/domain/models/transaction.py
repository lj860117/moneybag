"""
Transaction 领域模型
====================
券商/记账软件导入的单笔交易记录。

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §接口契约
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class TradeDirection(str, Enum):
    """交易方向"""
    BUY = "buy"
    SELL = "sell"


class TransactionType(str, Enum):
    """交易类型（行为归因用）"""
    MANUAL = "manual"           # 手动交易（行为归因检测的唯一目标）
    AUTO_INVEST = "auto_invest" # 定投（不计入行为偏差统计）
    REBALANCE = "rebalance"     # 再平衡（不受任何行为风控限制）
    DIVIDEND = "dividend"       # 分红（不计入交易统计）


class DataSource(str, Enum):
    """数据来源"""
    AUTO = "auto"     # 自动同步
    MANUAL = "manual" # 手动录入


@dataclass
class Transaction:
    """券商/记账软件导入的单笔交易记录"""
    trade_date: date                              # 交易日期
    code: str                                     # 证券代码（如 "510300"）
    name: str                                     # 证券名称（如 "沪深300ETF"）
    direction: TradeDirection                     # 交易方向
    amount: Decimal                               # 交易金额（正数，方向由 direction 决定）
    quantity: Optional[int] = None                # 交易数量（份额），可为空
    fee: Optional[Decimal] = None                 # 手续费，可为空
    source: DataSource = DataSource.AUTO          # 数据来源标记
    transaction_type: TransactionType = TransactionType.MANUAL  # 交易类型
    broker: Optional[str] = None                  # 券商名称（如 "changjiang"），手动录入时为 None
    raw_row: Optional[dict] = field(default=None, repr=False)  # 原始行数据（调试用）


@dataclass
class FailedTransaction:
    """校验失败的交易记录"""
    transaction: Transaction     # 原始记录
    reason: str                  # 失败原因（人类可读）
    rule: str                    # 触发的规则标识


@dataclass
class WarningTransaction:
    """校验通过但有警告的交易记录"""
    transaction: Transaction     # 原始记录
    warning: str                 # 警告内容（人类可读）
    rule: str                    # 触发的规则标识


@dataclass
class ValidationResult:
    """交易记录校验结果"""
    valid: list[Transaction] = field(default_factory=list)           # 通过校验的记录
    failed: list[FailedTransaction] = field(default_factory=list)    # 未通过的记录
    warnings: list[WarningTransaction] = field(default_factory=list) # 通过但有警告的记录
