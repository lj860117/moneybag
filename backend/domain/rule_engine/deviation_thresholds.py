"""
Deviation Thresholds -- 动态阈值（波动率分档）+ 极端行情保护
==============================================================
根据市场波动率动态调整偏离度容忍阈值，并在极端行情下触发二次确认。

核心逻辑：
  - 波动率 <15% → 容忍 5%
  - 波动率 15%-25% → 容忍 7%
  - 波动率 >25% → 容忍 10%
  - 波动率 >30% → 触发极端行情二次确认（只提醒，不禁止）

与 M3 偏离度体系的关系（方案 A 已确认）：
  动态阈值作为"前置过滤层"：
  1. 先判断偏离度是否在容忍范围内
  2. 若超出容忍范围，再按 M3 四档分级提醒（mild/clear/strong）
  3. 若在容忍范围内 → 不触发提醒

波动率数据源：沪深 300 近 20 日年化波动率

设计文档：docs/design/m7-plus/02-batch-m7-glide-path.md §5-6
不变式 #9：domain/ 不 import infra/
"""
from __future__ import annotations

from typing import Optional


# ============================================================
# 常量区（波动率分档阈值）
# ============================================================

# 低波动率上界
LOW_VOL_CEILING: float = 0.15
# 中波动率上界
MID_VOL_CEILING: float = 0.25
# 极端行情波动率阈值
EXTREME_VOL_THRESHOLD: float = 0.30

# 各档位偏离度容忍值
LOW_VOL_TOLERANCE: float = 0.05   # 波动率 <15% → 容忍 5%
MID_VOL_TOLERANCE: float = 0.07   # 波动率 15%-25% → 容忍 7%
HIGH_VOL_TOLERANCE: float = 0.10  # 波动率 >25% → 容忍 10%


# ============================================================
# 核心函数
# ============================================================

def get_tolerance(volatility: float) -> float:
    """根据市场波动率返回偏离度容忍值。

    Args:
        volatility: 沪深 300 近 20 日年化波动率（0-1 范围，如 0.20 = 20%）

    Returns:
        float — 偏离度容忍阈值（如 0.05, 0.07, 0.10）
    """
    if volatility < LOW_VOL_CEILING:
        return LOW_VOL_TOLERANCE
    elif volatility <= MID_VOL_CEILING:
        return MID_VOL_TOLERANCE
    else:
        return HIGH_VOL_TOLERANCE


def should_trigger_extreme_confirmation(volatility: float) -> bool:
    """判断是否需要极端行情二次确认。

    当全市场波动率 >30% 时返回 True：
    - 非再平衡的主动加仓需触发强制二次确认弹窗
    - 只提醒，不禁止：用户确认后可正常操作
    - 不是择时：不判断涨跌方向，不限制操作方向

    Args:
        volatility: 沪深 300 近 20 日年化波动率（0-1 范围）

    Returns:
        bool — True 表示需要触发二次确认
    """
    return volatility > EXTREME_VOL_THRESHOLD


def apply_dynamic_filter(
    deviation: float,
    volatility: float,
    m3_thresholds: Optional[dict[str, float]] = None,
) -> Optional[str]:
    """动态阈值前置过滤 + M3 四档分级。

    决策流程（方案 A）：
    1. 根据波动率确定容忍值
    2. 若偏离度 <= 容忍值 → 返回 None（不触发任何提醒）
    3. 若偏离度 > 容忍值 → 按 M3 四档分级返回提醒级别

    Args:
        deviation: 实际偏离度（绝对值，0-1 范围，如 0.08 = 8%）
        volatility: 当前沪深 300 近 20 日年化波动率
        m3_thresholds: M3 四档阈值配置，格式如：
            {
                "mild": 0.03,     # 3% — 温和提醒
                "clear": 0.07,    # 7% — 明确提醒
                "strong": 0.15,   # 15% — 强烈提醒
            }
            若未提供，使用 AllocationDefaults 中的默认值

    Returns:
        None — 在容忍范围内，不触发提醒
        "mild" — 超出容忍但偏离度较小
        "clear" — 明确偏离
        "strong" — 严重偏离
    """
    tolerance = get_tolerance(volatility)

    # 前置过滤：在容忍范围内不触发
    if deviation <= tolerance:
        return None

    # 超出容忍范围 → 按 M3 四档分级
    # 默认 M3 阈值（来自 AllocationDefaults）
    if m3_thresholds is None:
        m3_thresholds = {
            "mild": 0.03,
            "clear": 0.07,
            "strong": 0.15,
        }

    mild_threshold = m3_thresholds.get("mild", 0.03)
    clear_threshold = m3_thresholds.get("clear", 0.07)
    strong_threshold = m3_thresholds.get("strong", 0.15)

    if deviation >= strong_threshold:
        return "strong"
    elif deviation >= clear_threshold:
        return "clear"
    else:
        return "mild"


# ============================================================
# 导出
# ============================================================

__all__ = [
    "get_tolerance",
    "should_trigger_extreme_confirmation",
    "apply_dynamic_filter",
]
