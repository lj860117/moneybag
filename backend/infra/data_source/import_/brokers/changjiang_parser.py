"""
长江证券交割单解析器
====================
基于模拟样本文件的列名定义别名映射。
当用户提供真实长江证券导出文件后，只需微调 column_alias_map() 中的别名。

样本列名：成交日期、成交时间、证券代码、证券名称、操作、成交数量、
          成交价格、成交金额、手续费、印花税、过户费、佣金、
          发生金额、成交编号、股东代码、交易市场、备注

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §3
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

import pandas as pd  # type: ignore[import-untyped]

from domain.models.transaction import (
    DataSource,
    TradeDirection,
    Transaction,
    TransactionType,
)
from infra.data_source.import_.brokers.base_broker_parser import BaseBrokerParser, ParseError
from infra.data_source.import_.brokers.registry import register_broker


@register_broker
class ChangjiangParser(BaseBrokerParser):
    """长江证券交割单解析器"""

    @property
    def broker_name(self) -> str:
        return "changjiang"

    @property
    def sheet_name(self) -> Union[str, int]:
        """长江证券交割单 Sheet 名"""
        return "交割单"

    def column_alias_map(self) -> dict[str, list[str]]:
        """
        标准字段名 → 长江证券可能使用的别名列表。
        当拿到真实文件后，只需在对应列表里追加新别名即可。
        """
        return {
            "成交日期": ["交易日期", "日期", "trade_date", "Trade Date"],
            "证券代码": ["股票代码", "代码", "code", "Code", "股票代号"],
            "证券名称": ["股票名称", "名称", "name", "Name", "简称"],
            "操作": ["买卖方向", "方向", "交易方向", "买卖", "direction", "Direction", "操作类型"],
            "成交数量": ["交易数量", "数量", "quantity", "Quantity", "成交量"],
            "成交价格": ["交易价格", "价格", "price", "Price", "成交均价"],
            "成交金额": ["交易金额", "金额", "amount", "Amount", "成交额"],
            "手续费": ["总手续费", "费用", "commission", "Commission"],
            "印花税": ["stamp_tax", "StampTax"],
            "过户费": ["transfer_fee", "TransferFee"],
            "佣金": ["交易佣金", "brokerage"],
            "发生金额": ["实际金额", "净金额", "net_amount"],
            "成交编号": ["交易编号", "编号", "trade_id"],
            "股东代码": ["账户代码", "account"],
            "交易市场": ["市场", "market", "Market"],
        }

    def parse_rows(self, df: pd.DataFrame) -> list[Transaction]:
        """解析已清洗的 DataFrame 为 Transaction 列表"""
        transactions: list[Transaction] = []

        # 校验必须存在的列
        required_cols = ["成交日期", "证券代码", "证券名称", "操作", "成交金额"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ParseError(f"缺少必要列: {missing}。当前列: {list(df.columns)}")

        for idx, row in df.iterrows():
            try:
                txn = self._parse_single_row(row, idx)
                if txn is not None:
                    transactions.append(txn)
            except (ValueError, InvalidOperation, KeyError):
                # 单行解析失败，跳过（校验层会处理）
                continue

        return transactions

    def _parse_single_row(self, row: pd.Series, idx: int) -> Transaction | None:
        """解析单行数据为 Transaction，返回 None 表示跳过（如汇总行）"""
        # 跳过空行或汇总行
        raw_date = row.get("成交日期")
        if pd.isna(raw_date) or str(raw_date).strip() == "":
            return None

        # 解析日期
        trade_date = self._parse_date(raw_date)
        if trade_date is None:
            return None

        # 证券代码（Excel 中数字列可能被读为 float，如 512690.0 → "512690"）
        raw_code = row.get("证券代码", "")
        if pd.isna(raw_code):
            return None
        code = str(raw_code).strip()
        # 去掉 float 尾巴（"512690.0" → "512690"）
        if code.endswith(".0"):
            code = code[:-2]
        # 补零（如 "1" → "000001"）
        if code.isdigit() and len(code) < 6:
            code = code.zfill(6)
        if not code or code == "nan":
            return None

        # 证券名称
        name = str(row.get("证券名称", "")).strip()

        # 交易方向
        direction = self._parse_direction(row.get("操作", ""))
        if direction is None:
            return None

        # 成交金额（取绝对值，方向由 direction 字段决定）
        amount = self._parse_decimal(row.get("成交金额", 0))
        if amount is None or amount <= Decimal("0"):
            # 尝试用 发生金额 的绝对值
            net = self._parse_decimal(row.get("发生金额", 0))
            if net is not None and net != Decimal("0"):
                amount = abs(net)
            else:
                return None

        amount = abs(amount)

        # 数量
        quantity = self._parse_int(row.get("成交数量"))

        # 手续费（合并各项费用）
        fee_sum = Decimal("0")
        for fee_col in ("手续费", "印花税", "过户费", "佣金"):
            fee_val = self._parse_decimal(row.get(fee_col, 0))
            if fee_val is not None:
                fee_sum += abs(fee_val)
        fee: Optional[Decimal] = fee_sum if fee_sum != Decimal("0") else None

        # 构造原始行数据（用于调试）
        raw_row = {k: (str(v) if pd.notna(v) else None) for k, v in row.items()}

        return Transaction(
            trade_date=trade_date,
            code=code,
            name=name,
            direction=direction,
            amount=amount,
            quantity=quantity,
            fee=fee,
            source=DataSource.AUTO,
            transaction_type=TransactionType.MANUAL,
            broker=self.broker_name,
            raw_row=raw_row,
        )

    # ---- 辅助方法 ----

    @staticmethod
    def _parse_date(value) -> date | None:
        """解析日期，支持多种格式"""
        if isinstance(value, (date, datetime)):
            return value if isinstance(value, date) else value.date()
        s = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_direction(value) -> TradeDirection | None:
        """解析交易方向"""
        s = str(value).strip().lower()
        buy_keywords = ("买入", "买", "buy", "b")
        sell_keywords = ("卖出", "卖", "sell", "s")
        if s in buy_keywords:
            return TradeDirection.BUY
        if s in sell_keywords:
            return TradeDirection.SELL
        return None

    @staticmethod
    def _parse_decimal(value) -> Decimal | None:
        """安全解析为 Decimal"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        try:
            return Decimal(str(value).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_int(value) -> int | None:
        """安全解析为正整数"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        try:
            v = int(float(str(value).strip().replace(",", "")))
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None
