"""
Behavior Intervention Rules -- 行为归因联动执行（M9 Batch 6）
=============================================================
将行为检测（Batch 5）结果转化为执行干预动作，通过 M3 已有字段实现。

核心原则：
  - 所有执行干预通过 M3 已有字段（action/position_pct_max）实现
  - 不新增执行层代码，只新增规则配置
  - 再平衡永远在白名单，不受任何干预限制

§九 验证 1 结果：NOT SUPPORTED（position_pct_max 不支持运行时临时覆盖）
→ 全部干预降级为"纯弹窗提醒"模式（is_degraded=True）

设计文档：docs/design/m7-plus/06-batch-m9-behavior-detection.md
不变式 #1：AI 不预测证券价格
不变式 #8：domain/services 之间禁止互相 import

# TODO: 前端集成 — api/behavior.py 需新增路由：
#       GET /api/behavior/guard-status?userId=xxx
#       POST /api/behavior/guard-toggle (body: {enabled, reason})
#       GET /api/behavior/active-interventions?userId=xxx
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from domain.services.behavior_detector import BehaviorPattern
from domain.rule_engine.defaults import BehaviorInterventionDefaults

logger = logging.getLogger(__name__)


# ============================================================
# §九 验证 1 结果
# ============================================================

# M3 的 position_pct_max 当前不支持运行时临时覆盖
# 所有需要覆盖该字段的干预降级为纯弹窗提醒
VERIFICATION_1_RESULT: str = "not_supported"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class InterventionRule:
    """单条干预规则"""
    trigger_pattern: str         # 对应 BehaviorPattern.pattern_type
    action_type: str             # "cooldown" | "position_limit" | "block" | "confirm"
    m3_action: str               # "提醒" | "禁止"
    m3_field: Optional[str]      # 如 "position_pct_max"（需要覆盖的字段）
    temp_value: Optional[float]  # 临时覆盖值
    duration: Optional[str]      # "24h" | "end_of_month" | "until_resolved"
    whitelist: list[str] = field(default_factory=list)  # ["rebalance", "auto_invest"]


@dataclass
class ActiveIntervention:
    """当前生效的干预"""
    user_id: str
    rule: InterventionRule
    triggered_at: datetime
    expires_at: Optional[datetime]
    status: str                  # "active" | "expired" | "overridden_by_user"
    trigger_evidence: BehaviorPattern


@dataclass
class InterventionAction:
    """最终输出的干预动作"""
    action_type: str             # "prompt" | "limit" | "block"
    message: str                 # 用户可见的干预提示
    m3_overrides: Optional[dict] # 对 M3 字段的临时覆盖（降级时为 None）
    requires_confirmation: bool  # 是否需要用户二次确认
    is_degraded: bool            # 验证 1 不支持时为 True


# ============================================================
# 交易请求简化结构（避免 import 其他 domain/services）
# ============================================================

@dataclass
class TradeRequest:
    """交易请求（用于白名单检查和干预评估）"""
    trade_type: str              # "manual" | "rebalance" | "auto_invest" | "dividend"
    code: str = ""               # 证券代码
    amount: float = 0.0          # 交易金额
    direction: str = "buy"       # "buy" | "sell"


# ============================================================
# 5 类偏差 → 干预规则映射
# ============================================================

INTERVENTION_RULES: list[InterventionRule] = [
    # 1. 追涨冲动（RSI>70）→ 冷静期 24h
    InterventionRule(
        trigger_pattern="chasing_high",
        action_type="cooldown",
        m3_action="提醒",
        m3_field=None,  # 冷静期不需要覆盖仓位字段
        temp_value=None,
        duration="24h",
        whitelist=["rebalance", "auto_invest", "dividend"],
    ),
    # 2. 过度交易（本月 manual ≥5 次）→ 仓位上限下调 20%
    InterventionRule(
        trigger_pattern="over_trading",
        action_type="position_limit",
        m3_action="提醒",
        m3_field="position_pct_max",
        temp_value=0.80,  # 原仓位上限 * 0.80
        duration="end_of_month",
        whitelist=["rebalance", "auto_invest", "dividend"],
    ),
    # 3. 行业集中（>35%）→ 自动拦截新增买入
    InterventionRule(
        trigger_pattern="confirmation_bias",
        action_type="block",
        m3_action="禁止",
        m3_field=None,
        temp_value=None,
        duration="until_resolved",
        whitelist=["rebalance", "auto_invest", "dividend"],
    ),
    # 4. FOMO 交易（大涨日买入）→ 金额锁死余额 5%
    InterventionRule(
        trigger_pattern="fomo",
        action_type="position_limit",
        m3_action="提醒",
        m3_field="position_pct_max",
        temp_value=BehaviorInterventionDefaults.FOMO_AMOUNT_CAP_PCT,
        duration="24h",
        whitelist=["rebalance", "auto_invest", "dividend"],
    ),
    # 5. 高位加仓（PE 分位>70%）→ 强制确认弹窗
    InterventionRule(
        trigger_pattern="high_pe_adding",
        action_type="confirm",
        m3_action="提醒",
        m3_field=None,
        temp_value=None,
        duration=None,
        whitelist=["rebalance", "auto_invest", "dividend"],
    ),
]


# ============================================================
# 内存存储（用户干预状态）
# ============================================================

# 全局开关状态：user_id → bool
_guard_enabled_store: dict[str, bool] = {}

# 活跃干预列表：user_id → list[ActiveIntervention]
_active_interventions: dict[str, list[ActiveIntervention]] = {}


# ============================================================
# 全局开关
# ============================================================

def is_guard_enabled(user_id: str) -> bool:
    """
    检查行为风控总开关状态。

    关闭时所有干预降级为纯报告（Batch 5 检测照常但不触发限制）。
    默认启用（True）。
    """
    return _guard_enabled_store.get(
        user_id, BehaviorInterventionDefaults.BEHAVIOR_GUARD_ENABLED
    )


def set_guard_enabled(user_id: str, enabled: bool, reason: str) -> None:
    """
    设置行为风控总开关。

    变更记录到日志（生产环境应写入 decision_log）。
    关闭后页面顶部应常驻提示："⚠️ 行为风控已关闭，所有交易不受行为约束"
    """
    old_state = is_guard_enabled(user_id)
    _guard_enabled_store[user_id] = enabled
    logger.info(
        f"[行为风控开关] user={user_id} {old_state}→{enabled} reason={reason}"
    )


# ============================================================
# 白名单检查
# ============================================================

# 永久白名单交易类型
WHITELIST_TRADE_TYPES: set[str] = {"rebalance", "auto_invest", "dividend"}


def check_whitelist(trade: TradeRequest) -> bool:
    """
    检查交易是否在白名单内。

    再平衡/定投/分红永远在白名单，不受任何干预限制。
    Returns True = 在白名单中（不受限制）。
    """
    return trade.trade_type in WHITELIST_TRADE_TYPES


# ============================================================
# 干预评估
# ============================================================

def _get_rule_for_pattern(pattern_type: str) -> Optional[InterventionRule]:
    """根据偏差类型查找对应的干预规则。"""
    for rule in INTERVENTION_RULES:
        if rule.trigger_pattern == pattern_type:
            return rule
    return None


def _build_message(rule: InterventionRule, pattern: BehaviorPattern, is_degraded: bool) -> str:
    """构建用户可见的干预消息。"""
    degraded_prefix = "[仅提醒] " if is_degraded else ""

    messages = {
        "cooldown": (
            f"{degraded_prefix}检测到追涨冲动信号（{pattern.description}）。"
            f"建议 {BehaviorInterventionDefaults.COOLDOWN_HOURS}h 内谨慎操作同标的。"
        ),
        "position_limit": (
            f"{degraded_prefix}检测到{pattern.pattern_type}信号（{pattern.description}）。"
            f"建议控制本次交易仓位。"
        ),
        "block": (
            f"{degraded_prefix}检测到行业集中风险（{pattern.description}）。"
            f"建议暂停新增该行业买入。"
        ),
        "confirm": (
            f"{degraded_prefix}检测到高位加仓信号（{pattern.description}）。"
            f"请确认是否继续操作。"
        ),
    }
    return messages.get(rule.action_type, f"{degraded_prefix}检测到行为偏差：{pattern.description}")


def evaluate_intervention(
    pattern: BehaviorPattern,
    current_trade: TradeRequest,
    m3_config: dict,
    verification_1_result: str = VERIFICATION_1_RESULT,
) -> Optional[InterventionAction]:
    """
    根据偏差模式评估是否触发干预。

    流程：
    1. 检查全局开关 → 关闭则返回 None（纯报告模式）
    2. 检查白名单 → 再平衡等不受限
    3. 查找对应规则 → 无规则则跳过
    4. 根据 verification_1_result 决定正常执行或降级
    5. 构建 InterventionAction 返回

    Args:
        pattern: Batch 5 检测到的行为偏差
        current_trade: 当前交易请求
        m3_config: M3 规则引擎当前配置
        verification_1_result: "supported" | "not_supported"

    Returns:
        InterventionAction 或 None（开关关闭/白名单/无匹配规则）
    """
    # 1. 全局开关检查（使用 m3_config 中的 user_id，兜底为空字符串）
    user_id = m3_config.get("user_id", "")
    if not is_guard_enabled(user_id):
        logger.debug(f"[行为风控] 开关关闭，跳过干预 user={user_id}")
        return None

    # 2. 白名单检查
    if check_whitelist(current_trade):
        logger.debug(f"[行为风控] 白名单交易，跳过干预 type={current_trade.trade_type}")
        return None

    # 3. 查找规则
    rule = _get_rule_for_pattern(pattern.pattern_type)
    if rule is None:
        return None

    # 4. 降级判断
    is_degraded = (verification_1_result != "supported")
    needs_m3_override = rule.m3_field is not None

    # 构建 M3 覆盖
    m3_overrides: Optional[dict] = None
    if not is_degraded and needs_m3_override:
        # 正常模式：可以覆盖 M3 字段
        m3_overrides = {rule.m3_field: rule.temp_value}

    # 5. 构建输出动作
    # 降级时：所有动作类型都变为 "prompt"（纯提醒）
    action_type = rule.action_type
    if is_degraded:
        if action_type == "block":
            action_type = "prompt"  # 降级：拦截 → 提醒
        elif action_type == "position_limit":
            action_type = "prompt"  # 降级：限仓 → 提醒

    # 映射到输出 action_type
    output_action_map = {
        "cooldown": "prompt",
        "position_limit": "limit",
        "block": "block",
        "confirm": "prompt",
    }
    final_action_type = output_action_map.get(action_type, "prompt")
    if is_degraded:
        final_action_type = "prompt"  # 降级统一为 prompt

    message = _build_message(rule, pattern, is_degraded)
    requires_confirmation = (rule.action_type in ("confirm", "block"))

    # 记录活跃干预
    now = datetime.now()
    expires_at: Optional[datetime] = None
    if rule.duration == "24h":
        expires_at = now + timedelta(hours=BehaviorInterventionDefaults.COOLDOWN_HOURS)
    elif rule.duration == "end_of_month":
        # 当月最后一天
        if now.month == 12:
            expires_at = datetime(now.year + 1, 1, 1)
        else:
            expires_at = datetime(now.year, now.month + 1, 1)

    intervention = ActiveIntervention(
        user_id=user_id,
        rule=rule,
        triggered_at=now,
        expires_at=expires_at,
        status="active",
        trigger_evidence=pattern,
    )
    _active_interventions.setdefault(user_id, []).append(intervention)

    return InterventionAction(
        action_type=final_action_type,
        message=message,
        m3_overrides=m3_overrides,
        requires_confirmation=requires_confirmation,
        is_degraded=is_degraded,
    )


# ============================================================
# 活跃干预查询
# ============================================================

def get_active_interventions(user_id: str) -> list[ActiveIntervention]:
    """
    获取当前所有生效的干预。

    自动过滤已过期的干预（将其标记为 expired）。
    """
    interventions = _active_interventions.get(user_id, [])
    now = datetime.now()
    active: list[ActiveIntervention] = []

    for inv in interventions:
        if inv.status != "active":
            continue
        if inv.expires_at is not None and now > inv.expires_at:
            inv.status = "expired"
            continue
        active.append(inv)

    return active


def override_intervention(user_id: str, intervention_index: int) -> bool:
    """
    用户手动覆盖干预（需二次确认后调用）。

    将指定干预标记为 overridden_by_user。
    Returns True = 覆盖成功。
    """
    active = get_active_interventions(user_id)
    if 0 <= intervention_index < len(active):
        active[intervention_index].status = "overridden_by_user"
        logger.info(
            f"[行为风控] 用户覆盖干预 user={user_id} "
            f"pattern={active[intervention_index].trigger_evidence.pattern_type}"
        )
        return True
    return False


# ============================================================
# 测试辅助（重置内存状态）
# ============================================================

def _reset_state() -> None:
    """仅用于测试：重置所有内存状态。"""
    _guard_enabled_store.clear()
    _active_interventions.clear()


# ============================================================
# 导出
# ============================================================

__all__ = [
    "InterventionRule",
    "ActiveIntervention",
    "InterventionAction",
    "TradeRequest",
    "INTERVENTION_RULES",
    "VERIFICATION_1_RESULT",
    "is_guard_enabled",
    "set_guard_enabled",
    "check_whitelist",
    "evaluate_intervention",
    "get_active_interventions",
    "override_intervention",
    "_reset_state",
]
