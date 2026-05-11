"""
Tests for M9 Batch 6: 行为归因联动执行
========================================
测试 5 类偏差干预 + 全局开关 + 白名单 + 降级模式。

运行方式：
    pytest tests/test_m9_batch6_intervention.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# 确保 backend 目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from domain.services.behavior_detector import BehaviorPattern
from domain.rule_engine.behavior_intervention_rules import (
    InterventionRule,
    ActiveIntervention,
    InterventionAction,
    TradeRequest,
    INTERVENTION_RULES,
    VERIFICATION_1_RESULT,
    is_guard_enabled,
    set_guard_enabled,
    check_whitelist,
    evaluate_intervention,
    get_active_interventions,
    override_intervention,
    _reset_state,
)


# ============================================================
# 测试 Fixtures
# ============================================================

def _make_pattern(pattern_type: str, severity: str = "moderate") -> BehaviorPattern:
    """构造测试用的 BehaviorPattern"""
    return BehaviorPattern(
        pattern_type=pattern_type,
        severity=severity,
        evidence_count=3,
        total_relevant=10,
        ratio=0.3,
        description=f"测试偏差：{pattern_type}",
        supporting_trades=["t1", "t2", "t3"],
    )


def _make_trade(trade_type: str = "manual", direction: str = "buy") -> TradeRequest:
    """构造测试用的 TradeRequest"""
    return TradeRequest(
        trade_type=trade_type,
        code="600519",
        amount=10000.0,
        direction=direction,
    )


def _m3_config(user_id: str = "test_user") -> dict:
    """构造测试用的 M3 配置"""
    return {"user_id": user_id, "position_pct_max": 0.25}


# ============================================================
# 全局开关测试
# ============================================================

class TestGuardSwitch:
    """行为风控全局开关测试"""

    def setup_method(self):
        _reset_state()

    def test_default_enabled(self):
        """默认开启"""
        assert is_guard_enabled("user_001") is True

    def test_disable_guard(self):
        """关闭开关"""
        set_guard_enabled("user_001", False, "用户主动关闭")
        assert is_guard_enabled("user_001") is False

    def test_enable_guard(self):
        """重新开启"""
        set_guard_enabled("user_001", False, "关闭")
        set_guard_enabled("user_001", True, "重新开启")
        assert is_guard_enabled("user_001") is True

    def test_per_user_isolation(self):
        """不同用户状态隔离"""
        set_guard_enabled("user_A", False, "A 关闭")
        assert is_guard_enabled("user_A") is False
        assert is_guard_enabled("user_B") is True  # B 仍然默认开启

    def test_guard_off_no_intervention(self):
        """开关关闭时 evaluate_intervention 返回 None"""
        set_guard_enabled("test_user", False, "测试关闭")
        pattern = _make_pattern("chasing_high")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is None


# ============================================================
# 白名单测试
# ============================================================

class TestWhitelist:
    """白名单检查测试"""

    def test_rebalance_in_whitelist(self):
        """再平衡永远在白名单"""
        trade = _make_trade(trade_type="rebalance")
        assert check_whitelist(trade) is True

    def test_auto_invest_in_whitelist(self):
        """定投在白名单"""
        trade = _make_trade(trade_type="auto_invest")
        assert check_whitelist(trade) is True

    def test_dividend_in_whitelist(self):
        """分红在白名单"""
        trade = _make_trade(trade_type="dividend")
        assert check_whitelist(trade) is True

    def test_manual_not_in_whitelist(self):
        """手动交易不在白名单"""
        trade = _make_trade(trade_type="manual")
        assert check_whitelist(trade) is False

    def test_rebalance_bypasses_intervention(self):
        """再平衡不受干预限制"""
        _reset_state()
        pattern = _make_pattern("chasing_high", "severe")
        trade = _make_trade(trade_type="rebalance")
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is None


# ============================================================
# 5 类偏差干预测试（降级模式，因为 verification_1 = not_supported）
# ============================================================

class TestInterventionDegraded:
    """5 类偏差在降级模式下的干预测试"""

    def setup_method(self):
        _reset_state()

    def test_chasing_high_cooldown(self):
        """追涨冲动 → 冷静期提醒（降级模式）"""
        pattern = _make_pattern("chasing_high")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())

        assert result is not None
        assert result.action_type == "prompt"
        assert result.is_degraded is True
        assert result.m3_overrides is None
        assert "追涨冲动" in result.message
        assert result.requires_confirmation is False

    def test_over_trading_position_limit(self):
        """过度交易 → 仓位上限提醒（降级模式，无实际限制）"""
        pattern = _make_pattern("over_trading")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())

        assert result is not None
        assert result.action_type == "prompt"  # 降级为 prompt
        assert result.is_degraded is True
        assert result.m3_overrides is None  # 降级无覆盖
        assert "over_trading" in result.message

    def test_confirmation_bias_block(self):
        """行业集中 → 拦截提醒（降级模式）"""
        pattern = _make_pattern("confirmation_bias")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())

        assert result is not None
        assert result.action_type == "prompt"  # 降级从 block → prompt
        assert result.is_degraded is True
        assert "行业集中" in result.message
        assert result.requires_confirmation is True

    def test_fomo_amount_cap(self):
        """FOMO 交易 → 金额限制提醒（降级模式）"""
        pattern = _make_pattern("fomo")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())

        assert result is not None
        assert result.action_type == "prompt"
        assert result.is_degraded is True
        assert result.m3_overrides is None

    def test_high_pe_confirm(self):
        """高位加仓 → 强制确认弹窗（降级模式）"""
        pattern = _make_pattern("high_pe_adding")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())

        assert result is not None
        assert result.action_type == "prompt"
        assert result.is_degraded is True
        assert "高位加仓" in result.message
        assert result.requires_confirmation is True


# ============================================================
# 正常模式测试（模拟 verification_1 = supported）
# ============================================================

class TestInterventionNormal:
    """假设 verification_1 = supported 时的干预行为"""

    def setup_method(self):
        _reset_state()

    def test_over_trading_with_m3_override(self):
        """正常模式：过度交易应产生 M3 覆盖"""
        pattern = _make_pattern("over_trading")
        trade = _make_trade()
        result = evaluate_intervention(
            pattern, trade, _m3_config(),
            verification_1_result="supported",
        )

        assert result is not None
        assert result.is_degraded is False
        assert result.action_type == "limit"
        assert result.m3_overrides is not None
        assert "position_pct_max" in result.m3_overrides

    def test_fomo_with_m3_override(self):
        """正常模式：FOMO 应产生 M3 覆盖"""
        pattern = _make_pattern("fomo")
        trade = _make_trade()
        result = evaluate_intervention(
            pattern, trade, _m3_config(),
            verification_1_result="supported",
        )

        assert result is not None
        assert result.is_degraded is False
        assert result.m3_overrides is not None
        assert result.m3_overrides["position_pct_max"] == 0.05

    def test_block_no_m3_override(self):
        """正常模式：block 类型不需要 M3 覆盖"""
        pattern = _make_pattern("confirmation_bias")
        trade = _make_trade()
        result = evaluate_intervention(
            pattern, trade, _m3_config(),
            verification_1_result="supported",
        )

        assert result is not None
        assert result.is_degraded is False
        assert result.action_type == "block"
        assert result.m3_overrides is None


# ============================================================
# 活跃干预管理测试
# ============================================================

class TestActiveInterventions:
    """活跃干预状态管理"""

    def setup_method(self):
        _reset_state()

    def test_intervention_recorded(self):
        """干预触发后记录到活跃列表"""
        pattern = _make_pattern("chasing_high")
        trade = _make_trade()
        evaluate_intervention(pattern, trade, _m3_config())

        active = get_active_interventions("test_user")
        assert len(active) == 1
        assert active[0].rule.trigger_pattern == "chasing_high"
        assert active[0].status == "active"

    def test_multiple_interventions(self):
        """多次触发叠加"""
        for pt in ["chasing_high", "fomo", "high_pe_adding"]:
            pattern = _make_pattern(pt)
            trade = _make_trade()
            evaluate_intervention(pattern, trade, _m3_config())

        active = get_active_interventions("test_user")
        assert len(active) == 3

    def test_override_intervention(self):
        """用户覆盖干预"""
        pattern = _make_pattern("chasing_high")
        trade = _make_trade()
        evaluate_intervention(pattern, trade, _m3_config())

        success = override_intervention("test_user", 0)
        assert success is True

        active = get_active_interventions("test_user")
        assert len(active) == 0  # 被覆盖的不再出现在活跃列表

    def test_override_invalid_index(self):
        """覆盖不存在的索引返回 False"""
        success = override_intervention("test_user", 99)
        assert success is False

    def test_no_active_for_unknown_user(self):
        """未知用户无活跃干预"""
        active = get_active_interventions("nonexistent_user")
        assert active == []


# ============================================================
# 规则映射完整性测试
# ============================================================

class TestRuleMappingCompleteness:
    """规则映射完整性"""

    def test_five_rules_defined(self):
        """定义了 5 条干预规则"""
        assert len(INTERVENTION_RULES) == 5

    def test_all_rules_have_whitelist(self):
        """所有规则都包含白名单（至少含 rebalance）"""
        for rule in INTERVENTION_RULES:
            assert "rebalance" in rule.whitelist, (
                f"规则 {rule.trigger_pattern} 白名单缺少 rebalance"
            )

    def test_verification_1_is_not_supported(self):
        """§九 验证 1 结果为 not_supported"""
        assert VERIFICATION_1_RESULT == "not_supported"

    def test_unmatched_pattern_returns_none(self):
        """未匹配的偏差类型返回 None"""
        _reset_state()
        pattern = _make_pattern("anchoring")  # anchoring 没有对应干预规则
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is None


# ============================================================
# 集成场景测试
# ============================================================

class TestIntegrationScenarios:
    """5 类偏差各 1 个完整场景"""

    def setup_method(self):
        _reset_state()

    def test_scenario_chasing_high(self):
        """场景 1：追涨冲动 → 冷静期 → 再平衡不受限"""
        # 手动交易触发干预
        pattern = _make_pattern("chasing_high", "severe")
        manual_trade = _make_trade(trade_type="manual")
        result = evaluate_intervention(pattern, manual_trade, _m3_config())
        assert result is not None
        assert result.action_type == "prompt"

        # 再平衡不受限
        rebalance_trade = _make_trade(trade_type="rebalance")
        result2 = evaluate_intervention(pattern, rebalance_trade, _m3_config())
        assert result2 is None

    def test_scenario_over_trading(self):
        """场景 2：过度交易 → 仓位提醒 → 定投不计入"""
        pattern = _make_pattern("over_trading", "moderate")
        manual_trade = _make_trade(trade_type="manual")
        result = evaluate_intervention(pattern, manual_trade, _m3_config())
        assert result is not None

        # 定投不受限
        auto_trade = _make_trade(trade_type="auto_invest")
        result2 = evaluate_intervention(pattern, auto_trade, _m3_config())
        assert result2 is None

    def test_scenario_industry_concentration(self):
        """场景 3：行业集中 → 拦截提醒 → 全局开关关闭则无干预"""
        pattern = _make_pattern("confirmation_bias", "severe")
        trade = _make_trade()

        # 开关开启：触发
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is not None

        # 关闭开关：不触发
        set_guard_enabled("test_user", False, "测试关闭")
        result2 = evaluate_intervention(pattern, trade, _m3_config())
        assert result2 is None

    def test_scenario_fomo(self):
        """场景 4：FOMO → 金额提醒 → 用户覆盖"""
        pattern = _make_pattern("fomo", "moderate")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is not None

        # 用户覆盖
        override_intervention("test_user", 0)
        active = get_active_interventions("test_user")
        assert len(active) == 0

    def test_scenario_high_pe(self):
        """场景 5：高位加仓 → 确认弹窗 → 需二次确认"""
        pattern = _make_pattern("high_pe_adding", "moderate")
        trade = _make_trade()
        result = evaluate_intervention(pattern, trade, _m3_config())
        assert result is not None
        assert result.requires_confirmation is True
        assert "高位加仓" in result.message
