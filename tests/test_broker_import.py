"""
M7 Batch 1 — 券商流水导入测试
================================
测试覆盖:
  1. 长江证券模拟 Excel 全流程解析
  2. 错误数据测试（日期错乱、金额为0、重复行）
  3. 校验层正确隔离
  4. API 端到端（需要后端运行）
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

# 确保能导入 backend 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from domain.models.transaction import (
    DataSource,
    FailedTransaction,
    TradeDirection,
    Transaction,
    TransactionType,
    ValidationResult,
)
from domain.services.transaction_validator import validate_transactions
from infra.data_source.import_.brokers.base_broker_parser import (
    BaseBrokerParser,
    ParseError,
    UnsupportedBrokerError,
    _normalize_column_name,
)
from infra.data_source.import_.brokers.changjiang_parser import ChangjiangParser
from infra.data_source.import_.brokers.registry import (
    get_parser,
    get_supported_brokers,
    parse_broker_file,
)
from use_cases.sync_broker_statement import SyncBrokerStatementUseCase, SyncResult


# ---- 固定路径 ----
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "broker_statements"
MOCK_XLSX = FIXTURES_DIR / "changjiang_delivery_mock.xlsx"


# ============================================================
# 测试 1：列名清洗
# ============================================================

class TestColumnNameNormalization:
    """列名清洗函数测试"""

    def test_fullwidth_to_halfwidth(self):
        """全角字符转半角"""
        assert _normalize_column_name("成交金额") == "成交金额"
        assert _normalize_column_name("ＡＢＣ") == "ABC"

    def test_chinese_space(self):
        """中文全角空格处理"""
        assert _normalize_column_name("成交　金额") == "成交 金额"

    def test_strip_whitespace(self):
        """去除首尾空白"""
        assert _normalize_column_name("  成交金额  ") == "成交金额"

    def test_multiple_spaces(self):
        """连续空格合并"""
        assert _normalize_column_name("成交  金额") == "成交 金额"


# ============================================================
# 测试 2：长江证券 parser 注册和基本功能
# ============================================================

class TestChangjiangParserRegistration:
    """parser 注册机制"""

    def test_changjiang_registered(self):
        """长江证券 parser 已注册"""
        brokers = get_supported_brokers()
        assert "changjiang" in brokers

    def test_get_parser(self):
        """获取 parser 实例"""
        parser = get_parser("changjiang")
        assert isinstance(parser, ChangjiangParser)
        assert parser.broker_name == "changjiang"

    def test_unsupported_broker_raises(self):
        """不支持的券商抛异常"""
        with pytest.raises(UnsupportedBrokerError):
            get_parser("nonexistent_broker")


# ============================================================
# 测试 3：模拟 Excel 全流程解析
# ============================================================

class TestChangjiangParseMockExcel:
    """解析模拟 Excel 文件"""

    @pytest.fixture
    def parsed_transactions(self) -> list[Transaction]:
        """解析模拟文件"""
        return parse_broker_file("changjiang", MOCK_XLSX)

    def test_parse_returns_list(self, parsed_transactions):
        """返回 Transaction 列表"""
        assert isinstance(parsed_transactions, list)
        assert len(parsed_transactions) > 0

    def test_all_are_transaction_type(self, parsed_transactions):
        """所有元素都是 Transaction"""
        for txn in parsed_transactions:
            assert isinstance(txn, Transaction)

    def test_trade_date_valid(self, parsed_transactions):
        """日期在合理范围内"""
        for txn in parsed_transactions:
            assert isinstance(txn.trade_date, date)
            assert txn.trade_date.year >= 2026

    def test_code_format(self, parsed_transactions):
        """证券代码为 6 位数字"""
        import re
        for txn in parsed_transactions:
            assert re.match(r"^\d{6}$", txn.code), f"非法代码: {txn.code}"

    def test_direction_enum(self, parsed_transactions):
        """方向为 buy 或 sell"""
        for txn in parsed_transactions:
            assert txn.direction in (TradeDirection.BUY, TradeDirection.SELL)

    def test_amount_positive(self, parsed_transactions):
        """金额为正数"""
        for txn in parsed_transactions:
            assert txn.amount > Decimal("0"), f"金额非正: {txn.amount}"

    def test_source_is_auto(self, parsed_transactions):
        """来源标记为 auto"""
        for txn in parsed_transactions:
            assert txn.source == DataSource.AUTO

    def test_broker_is_changjiang(self, parsed_transactions):
        """券商标记为 changjiang"""
        for txn in parsed_transactions:
            assert txn.broker == "changjiang"

    def test_raw_row_preserved(self, parsed_transactions):
        """原始行数据保留"""
        for txn in parsed_transactions:
            assert txn.raw_row is not None
            assert isinstance(txn.raw_row, dict)

    def test_quantity_is_positive(self, parsed_transactions):
        """数量为正整数"""
        for txn in parsed_transactions:
            if txn.quantity is not None:
                assert txn.quantity > 0


# ============================================================
# 测试 4：校验层
# ============================================================

class TestTransactionValidator:
    """交易记录校验"""

    def _make_txn(self, **kwargs) -> Transaction:
        """构造测试 Transaction"""
        defaults = {
            "trade_date": date(2026, 3, 15),
            "code": "510300",
            "name": "沪深300ETF",
            "direction": TradeDirection.BUY,
            "amount": Decimal("5000.00"),
            "quantity": 1000,
            "source": DataSource.AUTO,
            "transaction_type": TransactionType.MANUAL,
            "broker": "changjiang",
        }
        defaults.update(kwargs)
        return Transaction(**defaults)

    def test_valid_transaction_passes(self):
        """合法记录通过"""
        txn = self._make_txn()
        result = validate_transactions([txn])
        assert len(result.valid) == 1
        assert len(result.failed) == 0

    def test_future_date_fails(self):
        """未来日期不通过"""
        txn = self._make_txn(trade_date=date(2030, 12, 31))
        result = validate_transactions([txn])
        assert len(result.failed) == 1
        assert result.failed[0].rule == "future_date"
        assert len(result.valid) == 0

    def test_zero_amount_fails(self):
        """金额为 0 不通过"""
        txn = self._make_txn(amount=Decimal("0"))
        result = validate_transactions([txn])
        assert len(result.failed) == 1
        assert result.failed[0].rule == "non_positive_amount"

    def test_negative_amount_fails(self):
        """金额为负数不通过"""
        txn = self._make_txn(amount=Decimal("-100"))
        result = validate_transactions([txn])
        assert len(result.failed) == 1
        assert result.failed[0].rule == "non_positive_amount"

    def test_invalid_code_fails(self):
        """非法证券代码不通过"""
        txn = self._make_txn(code="ABCDEF")
        result = validate_transactions([txn])
        assert len(result.failed) == 1
        assert result.failed[0].rule == "invalid_code"

    def test_empty_code_fails(self):
        """空证券代码不通过"""
        txn = self._make_txn(code="")
        result = validate_transactions([txn])
        assert len(result.failed) == 1
        assert result.failed[0].rule == "invalid_code"

    def test_duplicate_detection(self):
        """重复记录检测"""
        txn1 = self._make_txn()
        txn2 = self._make_txn()  # 完全相同
        result = validate_transactions([txn1, txn2])
        assert len(result.valid) == 1
        assert len(result.failed) == 1
        assert result.failed[0].rule == "duplicate"

    def test_different_direction_not_duplicate(self):
        """不同方向不算重复"""
        txn1 = self._make_txn(direction=TradeDirection.BUY)
        txn2 = self._make_txn(direction=TradeDirection.SELL)
        result = validate_transactions([txn1, txn2])
        assert len(result.valid) == 2
        assert len(result.failed) == 0

    def test_before_open_date_warning(self):
        """开户日前的记录——通过但有警告"""
        txn = self._make_txn(trade_date=date(2025, 1, 1))
        result = validate_transactions([txn], account_open_date=date(2026, 1, 1))
        assert len(result.valid) == 1
        assert len(result.warnings) == 1
        assert result.warnings[0].rule == "before_open_date"

    def test_non_positive_quantity_warning(self):
        """数量 ≤ 0 —— 通过但有警告"""
        txn = self._make_txn(quantity=-10)
        result = validate_transactions([txn])
        assert len(result.valid) == 1
        assert len(result.warnings) == 1
        assert result.warnings[0].rule == "non_positive_quantity"


# ============================================================
# 测试 5：use_case 全流程
# ============================================================

class TestSyncBrokerStatementUseCase:
    """use_case 端到端"""

    def test_full_flow_with_mock_file(self):
        """用模拟文件跑通全流程"""
        uc = SyncBrokerStatementUseCase()
        result = uc.execute(
            broker_name="changjiang",
            file_path=MOCK_XLSX,
            user_id="test_user",
        )
        assert isinstance(result, SyncResult)
        assert result.imported_count > 0
        assert result.imported_count + result.failed_count + result.duplicate_count > 0
        # 确认交易记录列表
        assert len(result.transactions) == result.imported_count

    def test_unsupported_broker_raises(self):
        """不支持的券商抛异常"""
        uc = SyncBrokerStatementUseCase()
        with pytest.raises(UnsupportedBrokerError):
            uc.execute(
                broker_name="unknown_broker",
                file_path=MOCK_XLSX,
                user_id="test_user",
            )

    def test_nonexistent_file_raises(self):
        """不存在的文件抛异常"""
        uc = SyncBrokerStatementUseCase()
        with pytest.raises(ParseError):
            uc.execute(
                broker_name="changjiang",
                file_path="/tmp/nonexistent_file.xlsx",
                user_id="test_user",
            )


# ============================================================
# 测试 6：错误数据隔离
# ============================================================

class TestErrorDataIsolation:
    """错误数据正确隔离，不污染主库"""

    def test_mixed_valid_and_invalid(self):
        """混合数据中，有效数据通过、无效数据隔离"""
        transactions = [
            Transaction(
                trade_date=date(2026, 3, 15),
                code="510300",
                name="沪深300ETF",
                direction=TradeDirection.BUY,
                amount=Decimal("5000"),
                broker="changjiang",
            ),
            # 未来日期
            Transaction(
                trade_date=date(2030, 12, 31),
                code="510300",
                name="沪深300ETF",
                direction=TradeDirection.BUY,
                amount=Decimal("3000"),
                broker="changjiang",
            ),
            # 金额为 0
            Transaction(
                trade_date=date(2026, 3, 16),
                code="510500",
                name="中证500ETF",
                direction=TradeDirection.SELL,
                amount=Decimal("0"),
                broker="changjiang",
            ),
            # 非法代码
            Transaction(
                trade_date=date(2026, 3, 17),
                code="中国平安",
                name="中国平安",
                direction=TradeDirection.BUY,
                amount=Decimal("10000"),
                broker="changjiang",
            ),
        ]
        result = validate_transactions(transactions)
        assert len(result.valid) == 1
        assert result.valid[0].code == "510300"
        assert len(result.failed) == 3

    def test_all_duplicates_after_first(self):
        """全部重复数据——只保留第一条"""
        txn = Transaction(
            trade_date=date(2026, 3, 15),
            code="510300",
            name="沪深300ETF",
            direction=TradeDirection.BUY,
            amount=Decimal("5000"),
            broker="changjiang",
        )
        result = validate_transactions([txn, txn, txn])
        assert len(result.valid) == 1
        assert len(result.failed) == 2
        for f in result.failed:
            assert f.rule == "duplicate"


# ============================================================
# 测试 7：API 端到端（需要后端运行）
# ============================================================

class TestBrokerImportAPI:
    """API 端到端测试（需要后端在 8000 端口运行）"""

    @pytest.fixture
    def client(self):
        """获取 httpx 客户端"""
        import httpx
        import os
        host = os.environ.get("MB_TEST_HOST", "http://127.0.0.1:8000")
        client = httpx.Client(base_url=host, timeout=30)
        # 探活
        try:
            resp = client.get("/api/broker-import/supported")
            if resp.status_code != 200:
                pytest.skip("后端未运行或接口未注册")
        except Exception:
            pytest.skip("后端未运行")
        return client

    def test_get_supported_brokers(self, client):
        """获取支持的券商列表"""
        resp = client.get("/api/broker-import/supported")
        assert resp.status_code == 200
        data = resp.json()
        assert "changjiang" in data["brokers"]

    def test_upload_mock_file(self, client):
        """上传模拟文件"""
        with open(MOCK_XLSX, "rb") as f:
            resp = client.post(
                "/api/broker-import/upload",
                files={"file": ("changjiang_delivery_mock.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"broker_name": "changjiang", "user_id": "qa_test_20260419"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported_count"] > 0
        assert "failed_details" in data

    def test_upload_unsupported_broker(self, client):
        """上传不支持的券商"""
        with open(MOCK_XLSX, "rb") as f:
            resp = client.post(
                "/api/broker-import/upload",
                files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"broker_name": "unknown_broker", "user_id": "qa_test_20260419"},
            )
        assert resp.status_code == 400
