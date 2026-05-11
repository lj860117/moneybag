"""
交易记录校验服务
================
解析产出的 List[Transaction] 在写入主库前，通过统一校验管道。
校验不通过的记录隔离存储，不污染主库。

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §7
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from domain.models.transaction import (
    FailedTransaction,
    Transaction,
    ValidationResult,
    WarningTransaction,
)


# 合法证券代码正则：6位数字（A股）或6位数字前缀
_CODE_PATTERN = re.compile(r"^\d{6}$")


def validate_transactions(
    transactions: list[Transaction],
    account_open_date: Optional[date] = None,
) -> ValidationResult:
    """
    校验交易记录列表。

    规则：
      1. 交易日期 ≤ 当前日期（未来日期排除）
      2. 交易日期 ≥ 账户开户日（若已知，否则仅警告）
      3. 交易金额 > 0（金额为 0 或负排除）
      4. 证券代码非空且格式合法（6位数字）
      5. 去重：同券商 + 同日期 + 同代码 + 同方向 + 同金额
      6. 股票数量为正整数（基金可为小数，此处仅警告）

    返回:
        ValidationResult(valid, failed, warnings)
    """
    result = ValidationResult()
    seen_keys: set[tuple] = set()  # 去重集合
    today = date.today()

    for txn in transactions:
        failed = False

        # 规则 1：未来日期
        if txn.trade_date > today:
            result.failed.append(FailedTransaction(
                transaction=txn,
                reason=f"交易日期 {txn.trade_date} 在未来，不合法",
                rule="future_date",
            ))
            failed = True
            continue

        # 规则 3：金额 ≤ 0
        if txn.amount <= Decimal("0"):
            result.failed.append(FailedTransaction(
                transaction=txn,
                reason=f"交易金额 {txn.amount} ≤ 0，不合法",
                rule="non_positive_amount",
            ))
            failed = True
            continue

        # 规则 4：证券代码非空且格式合法
        if not txn.code or not _CODE_PATTERN.match(txn.code.strip()):
            result.failed.append(FailedTransaction(
                transaction=txn,
                reason=f"证券代码 '{txn.code}' 为空或格式非法（需6位数字）",
                rule="invalid_code",
            ))
            failed = True
            continue

        # 规则 5：去重
        dedup_key = (
            txn.broker or "",
            txn.trade_date,
            txn.code,
            txn.direction.value,
            txn.amount,
        )
        if dedup_key in seen_keys:
            result.failed.append(FailedTransaction(
                transaction=txn,
                reason="重复记录（同券商+同日期+同代码+同方向+同金额）",
                rule="duplicate",
            ))
            failed = True
            continue
        seen_keys.add(dedup_key)

        # 校验通过，检查警告
        warnings_for_txn: list[tuple[str, str]] = []

        # 规则 2：开户日前
        if account_open_date and txn.trade_date < account_open_date:
            warnings_for_txn.append((
                f"交易日期 {txn.trade_date} 早于账户开户日 {account_open_date}",
                "before_open_date",
            ))

        # 规则 6：数量非正整数
        if txn.quantity is not None and txn.quantity <= 0:
            warnings_for_txn.append((
                f"交易数量 {txn.quantity} ≤ 0，可能为拆分/合并场景",
                "non_positive_quantity",
            ))

        # 记录通过（带或不带警告）
        result.valid.append(txn)
        for warning_msg, rule in warnings_for_txn:
            result.warnings.append(WarningTransaction(
                transaction=txn,
                warning=warning_msg,
                rule=rule,
            ))

    return result
