"""
同步券商流水用例
================
编排：解析文件 → 校验 → 去重 → 写入用户数据。

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §sync_broker_statement
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Union

from domain.models.transaction import (
    FailedTransaction,
    Transaction,
    ValidationResult,
)
from domain.services.transaction_validator import validate_transactions
from infra.data_source.import_.brokers.registry import parse_broker_file, get_parser


@dataclass
class SyncResult:
    """同步结果"""
    imported_count: int = 0        # 成功导入的记录数
    duplicate_count: int = 0       # 去重排除的记录数
    failed_count: int = 0          # 校验失败的记录数
    warning_count: int = 0         # 有警告的记录数
    failed_details: list[FailedTransaction] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)  # 成功导入的交易记录


class SyncBrokerStatementUseCase:
    """
    同步券商流水到用户数据。

    流程：
      1. 解析文件（parse_broker_file）
      2. 校验（validate_transactions）
      3. 返回结果（实际写入由调用方决定）
    """

    def execute(
        self,
        broker_name: str,
        file_path: Union[str, Path],
        user_id: str,
        account_open_date: Optional[date] = None,
    ) -> SyncResult:
        """
        执行同步：解析 → 校验 → 返回结果。

        参数:
            broker_name: 券商标识（如 "changjiang"）
            file_path: 文件路径
            user_id: 用户 ID
            account_open_date: 账户开户日（可选，用于校验）
        返回:
            SyncResult
        """
        # 1. 解析
        transactions = parse_broker_file(broker_name, file_path)

        # 2. 校验
        validation: ValidationResult = validate_transactions(
            transactions, account_open_date=account_open_date
        )

        # 3. 组装结果
        duplicate_count = sum(
            1 for f in validation.failed if f.rule == "duplicate"
        )
        failed_non_dup = [
            f for f in validation.failed if f.rule != "duplicate"
        ]

        return SyncResult(
            imported_count=len(validation.valid),
            duplicate_count=duplicate_count,
            failed_count=len(failed_non_dup),
            warning_count=len(validation.warnings),
            failed_details=validation.failed,
            transactions=validation.valid,
        )

    def execute_from_bytes(
        self,
        broker_name: str,
        file_bytes: bytes,
        filename: str,
        user_id: str,
        account_open_date: Optional[date] = None,
    ) -> SyncResult:
        """
        从字节流执行同步（API 上传场景）。

        参数:
            broker_name: 券商标识
            file_bytes: 文件字节内容
            filename: 原始文件名（用于判断格式）
            user_id: 用户 ID
            account_open_date: 账户开户日（可选）
        返回:
            SyncResult
        """
        # 1. 解析
        parser = get_parser(broker_name)
        transactions = parser.parse_bytes(file_bytes, filename)

        # 2. 校验
        validation: ValidationResult = validate_transactions(
            transactions, account_open_date=account_open_date
        )

        # 3. 组装结果
        duplicate_count = sum(
            1 for f in validation.failed if f.rule == "duplicate"
        )
        failed_non_dup = [
            f for f in validation.failed if f.rule != "duplicate"
        ]

        return SyncResult(
            imported_count=len(validation.valid),
            duplicate_count=duplicate_count,
            failed_count=len(failed_non_dup),
            warning_count=len(validation.warnings),
            failed_details=validation.failed,
            transactions=validation.valid,
        )
