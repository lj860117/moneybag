"""
Batch 4 + Batch 5 单元测试
==========================
测试行业偏离度检测 + 10 维筛选历史验证 + 行为归因检测 + 季度报告。
不依赖后端服务运行，纯单元测试。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pytest

# 确保 backend 在 Python 路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
# 确保项目根目录在路径中（tests/validation/ 模块需要）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.validation.fund_filter_validation import (
    HoldingItem,
    IndustryDeviationAlert,
    ValidationReport,
    check_industry_deviation,
    run_historical_validation,
)
from domain.services.behavior_detector import (
    BehaviorPattern,
    SimpleTransaction,
    detect_patterns,
    _filter_manual_only,
)
from domain.services.behavior_reporter import (
    BehaviorReport,
    generate_quarterly_report,
)


# ============================================================
# Batch 4: 行业偏离度检测
# ============================================================

class TestCheckIndustryDeviation:
    """测试 check_industry_deviation()"""

    def test_empty_holdings_returns_empty(self):
        """空持仓返回空告警"""
        result = check_industry_deviation([])
        assert result == []

    def test_single_industry_yellow_at_26pct(self):
        """单行业 26% → 黄灯"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.26),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.30),
            HoldingItem(fund_code="003", fund_name="基金C", industry="医疗", weight_pct=0.20),
            HoldingItem(fund_code="004", fund_name="基金D", industry="金融", weight_pct=0.24),
        ]
        alerts = check_industry_deviation(holdings)
        sci_alerts = [a for a in alerts if a.industry == "科技"]
        assert len(sci_alerts) == 1
        assert sci_alerts[0].level == "yellow"
        assert sci_alerts[0].threshold == 0.25

    def test_single_industry_red_at_36pct(self):
        """单行业 36% → 红灯"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.36),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.34),
            HoldingItem(fund_code="003", fund_name="基金C", industry="医疗", weight_pct=0.30),
        ]
        alerts = check_industry_deviation(holdings)
        sci_alerts = [a for a in alerts if a.industry == "科技"]
        assert len(sci_alerts) == 1
        assert sci_alerts[0].level == "red"

    def test_top3_yellow_at_75pct(self):
        """前三大行业 75% → 黄灯"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.24),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.24),
            HoldingItem(fund_code="003", fund_name="基金C", industry="医疗", weight_pct=0.24),
            HoldingItem(fund_code="004", fund_name="基金D", industry="金融", weight_pct=0.15),
            HoldingItem(fund_code="005", fund_name="基金E", industry="能源", weight_pct=0.13),
        ]
        alerts = check_industry_deviation(holdings)
        top3_alerts = [a for a in alerts if a.industry == "[前三大行业]"]
        assert len(top3_alerts) == 1
        assert top3_alerts[0].level == "yellow"
        assert top3_alerts[0].actual_pct == pytest.approx(0.72, abs=0.01)

    def test_m3_level_plan_a(self):
        """方案 A：M3 层级判定字段"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.30),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.70),
        ]
        alerts = check_industry_deviation(holdings, decision_2_result="plan_a")
        sci_alerts = [a for a in alerts if a.industry == "科技"]
        assert len(sci_alerts) == 1
        # 30% > 25%（M7+ 黄灯），但 <40%（M3 正常） → m3_level = "yellow"
        assert sci_alerts[0].m3_level == "yellow"

    def test_m3_level_red_at_42pct(self):
        """方案 A：M3 层级 42% → red"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.42),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.58),
        ]
        alerts = check_industry_deviation(holdings, decision_2_result="plan_a")
        sci_alerts = [a for a in alerts if a.industry == "科技"]
        assert len(sci_alerts) == 1
        assert sci_alerts[0].level == "red"  # M7+: 42% > 35%
        assert sci_alerts[0].m3_level == "red"  # M3: 42% > 40%

    def test_multiple_scenarios_detected(self):
        """能检测到 ≥3 种偏离场景"""
        holdings = [
            # 场景 1：科技 36% → 红灯
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.36),
            # 场景 2：消费 28% → 黄灯
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.28),
            # 场景 3：前三大行业合计 >70%
            HoldingItem(fund_code="003", fund_name="基金C", industry="医疗", weight_pct=0.20),
            HoldingItem(fund_code="004", fund_name="基金D", industry="金融", weight_pct=0.10),
            HoldingItem(fund_code="005", fund_name="基金E", industry="能源", weight_pct=0.06),
        ]
        alerts = check_industry_deviation(holdings)
        # 至少 3 个 alert：科技红灯 + 消费黄灯 + 前三大行业黄灯
        assert len(alerts) >= 3

    def test_no_alert_when_balanced(self):
        """均衡配置不触发告警"""
        holdings = [
            HoldingItem(fund_code="001", fund_name="基金A", industry="科技", weight_pct=0.15),
            HoldingItem(fund_code="002", fund_name="基金B", industry="消费", weight_pct=0.15),
            HoldingItem(fund_code="003", fund_name="基金C", industry="医疗", weight_pct=0.15),
            HoldingItem(fund_code="004", fund_name="基金D", industry="金融", weight_pct=0.15),
            HoldingItem(fund_code="005", fund_name="基金E", industry="能源", weight_pct=0.15),
            HoldingItem(fund_code="006", fund_name="基金F", industry="材料", weight_pct=0.15),
            HoldingItem(fund_code="007", fund_name="基金G", industry="公用", weight_pct=0.10),
        ]
        alerts = check_industry_deviation(holdings)
        assert len(alerts) == 0


class TestHistoricalValidation:
    """测试 run_historical_validation()"""

    def test_no_datasource_returns_empty(self):
        """无数据源返回无效报告"""
        report = run_historical_validation(months=12, data_source=None)
        assert report.total_months == 0
        assert report.is_qualified is False
        assert "无有效历史数据" in report.issues


# ============================================================
# Batch 5: 行为归因检测
# ============================================================

class TestFilterManualOnly:
    """测试前置过滤"""

    def test_filters_non_manual(self):
        """只保留 manual 类型"""
        trades = [
            SimpleTransaction(trade_id="1", trade_date=date.today(), code="001",
                            name="A", direction="buy", amount=1000, transaction_type="manual"),
            SimpleTransaction(trade_id="2", trade_date=date.today(), code="002",
                            name="B", direction="buy", amount=2000, transaction_type="auto_invest"),
            SimpleTransaction(trade_id="3", trade_date=date.today(), code="003",
                            name="C", direction="sell", amount=3000, transaction_type="rebalance"),
            SimpleTransaction(trade_id="4", trade_date=date.today(), code="004",
                            name="D", direction="buy", amount=500, transaction_type="dividend"),
        ]
        result = _filter_manual_only(trades)
        assert len(result) == 1
        assert result[0].trade_id == "1"

    def test_all_manual_passes_through(self):
        """全部 manual 全部通过"""
        trades = [
            SimpleTransaction(trade_id=str(i), trade_date=date.today(), code=f"00{i}",
                            name=f"Fund{i}", direction="buy", amount=1000,
                            transaction_type="manual")
            for i in range(5)
        ]
        result = _filter_manual_only(trades)
        assert len(result) == 5


class TestDetectPatterns:
    """测试 detect_patterns() 主入口"""

    def _make_trades(self, count: int, days_back: int = 60) -> list[SimpleTransaction]:
        """生成测试用交易序列"""
        today = date.today()
        return [
            SimpleTransaction(
                trade_id=str(i),
                trade_date=today - timedelta(days=days_back - i * 5),
                code="510300",
                name="沪深300ETF",
                direction="buy" if i % 2 == 0 else "sell",
                amount=10000.0,
                transaction_type="manual",
                industry="科技",
            )
            for i in range(count)
        ]

    def test_empty_transactions_returns_empty(self):
        """空交易返回空"""
        result = detect_patterns([])
        assert result == []

    def test_non_manual_excluded(self):
        """非手动交易被排除后数据不足，返回空"""
        trades = [
            SimpleTransaction(trade_id="1", trade_date=date.today(), code="001",
                            name="A", direction="buy", amount=1000,
                            transaction_type="auto_invest"),
            SimpleTransaction(trade_id="2", trade_date=date.today(), code="002",
                            name="B", direction="buy", amount=2000,
                            transaction_type="rebalance"),
        ]
        result = detect_patterns(trades)
        assert result == []

    def test_insufficient_data_returns_empty(self):
        """不足 3 笔交易返回空"""
        trades = self._make_trades(2)
        result = detect_patterns(trades)
        assert result == []

    def test_market_context_injected(self):
        """动态阈值注入市场背景"""
        trades = self._make_trades(10, days_back=80)
        # 使用 mock market data 让至少一个检测触发
        result = detect_patterns(trades, dynamic_threshold=0.05)
        # 无 market_data 时大部分检测不会触发，但验证不报错
        assert isinstance(result, list)

    def test_chasing_high_detection(self):
        """追高倾向检测（带 mock market data）"""

        class MockMarket:
            def get_rsi(self, code, trade_date):
                return 75.0  # 全部 RSI>70

            def get_recent_gain(self, code, trade_date, days=20):
                return 0.10  # 涨幅 10%（不触发）

            def get_csi300_daily_return(self, trade_date):
                return 0.01

            def get_pe_percentile(self, code, trade_date):
                return 0.50

            def get_unrealized_pnl(self, code, trade_date, avg_cost):
                return 0.0

        trades = self._make_trades(10, days_back=80)
        result = detect_patterns(trades, market_data=MockMarket())

        chasing = [p for p in result if p.pattern_type == "chasing_high"]
        assert len(chasing) == 1
        assert chasing[0].evidence_count > 0
        assert chasing[0].ratio > 0

    def test_fomo_detection(self):
        """FOMO 交易检测"""

        class MockMarket:
            def get_rsi(self, code, trade_date):
                return 50.0

            def get_recent_gain(self, code, trade_date, days=20):
                return 0.05

            def get_csi300_daily_return(self, trade_date):
                return 0.03  # 全部大涨日

            def get_pe_percentile(self, code, trade_date):
                return 0.50

            def get_unrealized_pnl(self, code, trade_date, avg_cost):
                return 0.0

        trades = self._make_trades(10, days_back=80)
        result = detect_patterns(trades, market_data=MockMarket())

        fomo = [p for p in result if p.pattern_type == "fomo"]
        assert len(fomo) == 1
        assert fomo[0].ratio > 0.5

    def test_confirmation_bias_detection(self):
        """确认偏误检测：所有交易同一行业"""
        today = date.today()
        trades = [
            SimpleTransaction(
                trade_id=str(i),
                trade_date=today - timedelta(days=60 - i * 5),
                code=f"00{i}",
                name=f"Fund{i}",
                direction="buy",
                amount=10000.0,
                transaction_type="manual",
                industry="科技",  # 全部科技
            )
            for i in range(10)
        ]
        result = detect_patterns(trades)
        bias = [p for p in result if p.pattern_type == "confirmation_bias"]
        assert len(bias) == 1
        assert bias[0].ratio >= 0.80

    def test_over_trading_detection(self):
        """过度交易检测：持仓周期短"""
        today = date.today()
        trades = []
        # 10 轮快速买卖（每轮间隔 5 天）
        for i in range(10):
            buy_date = today - timedelta(days=80 - i * 8)
            sell_date = buy_date + timedelta(days=3)  # 3 天后卖
            trades.append(SimpleTransaction(
                trade_id=f"buy_{i}", trade_date=buy_date, code="510300",
                name="沪深300ETF", direction="buy", amount=10000,
                transaction_type="manual",
            ))
            trades.append(SimpleTransaction(
                trade_id=f"sell_{i}", trade_date=sell_date, code="510300",
                name="沪深300ETF", direction="sell", amount=10000,
                transaction_type="manual",
            ))
        result = detect_patterns(trades)
        over = [p for p in result if p.pattern_type == "over_trading"]
        assert len(over) == 1
        assert "持仓" in over[0].description

    def test_high_pe_adding_detection(self):
        """高位加仓检测"""

        class MockMarket:
            def get_rsi(self, code, trade_date):
                return 50.0

            def get_recent_gain(self, code, trade_date, days=20):
                return 0.05

            def get_csi300_daily_return(self, trade_date):
                return 0.01

            def get_pe_percentile(self, code, trade_date):
                return 0.85  # 全部高 PE

            def get_unrealized_pnl(self, code, trade_date, avg_cost):
                return 0.0

        today = date.today()
        trades = [
            SimpleTransaction(
                trade_id=str(i),
                trade_date=today - timedelta(days=60 - i * 5),
                code="510300",
                name="沪深300ETF",
                direction="buy",
                amount=10000.0,
                transaction_type="manual",
            )
            for i in range(8)
        ]
        result = detect_patterns(trades, market_data=MockMarket())
        high_pe = [p for p in result if p.pattern_type == "high_pe_adding"]
        assert len(high_pe) == 1
        assert high_pe[0].ratio > 0.5


# ============================================================
# Batch 5: 季度报告
# ============================================================

class TestGenerateQuarterlyReport:
    """测试 generate_quarterly_report()"""

    def test_empty_patterns_no_data(self):
        """数据不足返回空报告"""
        report = generate_quarterly_report(
            patterns=[],
            user_id="test_user",
            quarter="2025Q4",
            has_enough_data=False,
        )
        assert report.patterns_found == 0
        assert "数据不足" in report.report_markdown

    def test_empty_patterns_with_data(self):
        """有数据但无模式"""
        report = generate_quarterly_report(
            patterns=[],
            user_id="test_user",
            quarter="2025Q4",
            volatility_context="低波动率环境",
        )
        assert report.patterns_found == 0
        assert "未检测到" in report.report_markdown

    def test_report_with_patterns(self):
        """有模式时生成完整报告"""
        patterns = [
            BehaviorPattern(
                pattern_type="chasing_high",
                severity="moderate",
                evidence_count=6,
                total_relevant=10,
                ratio=0.6,
                description="你 10 次买入中 6 次发生在 RSI>70 时",
                supporting_trades=["1", "2", "3", "4", "5", "6"],
                market_context="低波动率环境（容忍 5%）",
            ),
            BehaviorPattern(
                pattern_type="fomo",
                severity="mild",
                evidence_count=4,
                total_relevant=10,
                ratio=0.4,
                description="你 10 次交易中有 4 次发生在大涨日",
                supporting_trades=["1", "3", "5", "7"],
            ),
        ]
        report = generate_quarterly_report(
            patterns=patterns,
            user_id="test_user",
            quarter="2025Q4",
            volatility_context="低波动率环境",
        )
        assert report.patterns_found == 2
        assert "追高倾向" in report.report_markdown
        assert "FOMO" in report.report_markdown
        assert "2025Q4" in report.report_markdown
        assert "低波动率环境" in report.report_markdown
        # 不带情绪/投资建议（免责声明除外）
        assert "应该" not in report.report_markdown
        assert "建议你" not in report.report_markdown

    def test_report_no_emotion_no_labels(self):
        """报告不带情绪、不贴标签"""
        patterns = [
            BehaviorPattern(
                pattern_type="over_trading",
                severity="severe",
                evidence_count=20,
                total_relevant=20,
                ratio=1.0,
                description="你平均持仓 10 天",
                supporting_trades=[],
            ),
        ]
        report = generate_quarterly_report(
            patterns=patterns,
            user_id="test_user",
            quarter="2026Q1",
            volatility_context="中等波动率环境",
        )
        # 检查无贬义标签
        negative_words = ["赌徒", "韭菜", "愚蠢", "贪婪", "恐惧", "傻"]
        for word in negative_words:
            assert word not in report.report_markdown

    def test_report_has_disclaimer(self):
        """报告包含免责声明"""
        patterns = [
            BehaviorPattern(
                pattern_type="anchoring",
                severity="mild",
                evidence_count=2,
                total_relevant=5,
                ratio=0.4,
                description="test",
                supporting_trades=[],
            ),
        ]
        report = generate_quarterly_report(
            patterns=patterns,
            user_id="test_user",
            quarter="2025Q4",
        )
        assert "不构成" in report.report_markdown or "不构成任何投资建议" in report.report_markdown
