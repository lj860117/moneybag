"""
Glide Path Rules -- 年龄下滑配比 + 风格偏离检测
=================================================
根据年龄计算目标资产配比（大类配置纪律），并检测风格/市值维度偏离。

核心逻辑：
  - equity_plus_gold_pct = max(100 - age, 20)，向下取整到 5% 倍数
  - 黄金固定 5%，从合计中划出
  - 年龄向下取整到最近 5 岁档位查表（不线性插值）
  - 用户可覆盖 ±10%，超出抛 OverrideOutOfRangeError

设计文档：docs/design/m7-plus/02-batch-m7-glide-path.md
不变式 #9：domain/ 不 import infra/
不变式 #8：不 import allocation_rules（通过 Protocol 解耦）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


# ============================================================
# 常量区（风格偏离检测默认目标）
# ============================================================

# 价值风格目标：50%（即持仓中价值型基金占 50%）
DEFAULT_STYLE_VALUE_TARGET: float = 0.50
# 大盘风格目标：70%（即持仓中大盘基金占 70%）
DEFAULT_STYLE_LARGE_CAP_TARGET: float = 0.70
# 价值偏离黄灯阈值：±10%
STYLE_VALUE_YELLOW_THRESHOLD: float = 0.10
# 大盘偏离黄灯阈值：±15%
STYLE_LARGE_CAP_YELLOW_THRESHOLD: float = 0.15
# 黄金默认占比
GOLD_PCT_DEFAULT: float = 0.05
# 用户覆盖允许范围
USER_OVERRIDE_RANGE: float = 0.10


# ============================================================
# 年龄→配比查表（公式 + 手工校正结果）
# ============================================================

# key = 年龄档位（5 的倍数），value = (stock_pct, bond_pct, cash_pct, gold_pct)
# 所有值为 0-100 的整数（百分比），合计 = 100
_GLIDE_PATH_TABLE: dict[int, tuple[int, int, int, int]] = {
    20: (75, 10, 10, 5),   # 20 岁及以下
    25: (70, 15, 10, 5),
    30: (65, 20, 10, 5),
    35: (60, 25, 10, 5),
    40: (55, 30, 10, 5),
    45: (50, 30, 15, 5),
    50: (45, 35, 15, 5),
    55: (40, 40, 15, 5),
    60: (35, 45, 15, 5),
    65: (30, 45, 20, 5),
    70: (25, 45, 25, 5),   # 70 岁及以上（保底）
    75: (20, 45, 30, 5),   # 75+ 保底（equity_plus_gold=25→20%）
    80: (15, 50, 30, 5),   # 80+ 保底
}

# 支持的最小/最大档位
_MIN_BUCKET: int = 20
_MAX_BUCKET: int = 80


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AllocationTarget:
    """Glide Path 计算出的目标配比"""
    stock_pct: float       # 股票目标占比（已扣除黄金），0-1
    bond_pct: float        # 债券目标占比，0-1
    cash_pct: float        # 现金目标占比，0-1
    gold_pct: float        # 黄金目标占比（默认 5%），0-1
    age_bucket: int        # 使用的年龄档位（5 的倍数）
    user_override: bool    # 是否有用户覆盖


@dataclass
class DeviationAlert:
    """偏离度提醒"""
    level: str             # "yellow" | "red"
    dimension: str         # "value_style" | "large_cap_style"
    actual: float          # 实际占比
    target: float          # 目标占比
    threshold: float       # 触发阈值（偏离幅度）


# ============================================================
# 异常类
# ============================================================

class OverrideOutOfRangeError(ValueError):
    """用户覆盖超出允许范围（±10%）"""

    def __init__(self, field: str, default_value: float, override_value: float, allowed_range: float):
        self.field = field
        self.default_value = default_value
        self.override_value = override_value
        self.allowed_range = allowed_range
        deviation = abs(override_value - default_value)
        super().__init__(
            f"覆盖超出范围：{field} 默认 {default_value*100:.0f}%，"
            f"覆盖值 {override_value*100:.0f}%，偏离 {deviation*100:.0f}%，"
            f"允许范围 ±{allowed_range*100:.0f}%"
        )


# ============================================================
# 核心函数
# ============================================================

def _age_to_bucket(age: int) -> int:
    """年龄向下取整到 5 的倍数档位，限制在 [_MIN_BUCKET, _MAX_BUCKET] 范围内。"""
    bucket = (age // 5) * 5
    return max(_MIN_BUCKET, min(_MAX_BUCKET, bucket))


def calculate_target_allocation(
    age: int,
    user_overrides: Optional[dict[str, float]] = None,
) -> AllocationTarget:
    """根据年龄计算目标资产配比。

    Args:
        age: 主要决策者年龄（家庭取户主年龄）
        user_overrides: 用户自定义覆盖，key 为 "stock_pct"/"bond_pct"/"cash_pct"/"gold_pct"，
                        值为 0-1 的浮点数。每项允许偏离默认值 ±10%。

    Returns:
        AllocationTarget — 包含目标配比和使用的年龄档位

    Raises:
        OverrideOutOfRangeError — 用户覆盖超出 ±10% 范围
        ValueError — 年龄不合法
    """
    if age < 0 or age > 120:
        raise ValueError(f"年龄不合法：{age}，应在 0-120 范围内")

    bucket = _age_to_bucket(age)

    # 查表获取默认值
    if bucket in _GLIDE_PATH_TABLE:
        stock_int, bond_int, cash_int, gold_int = _GLIDE_PATH_TABLE[bucket]
    else:
        # 超出表范围的极端情况（理论上不会走到这里）
        stock_int, bond_int, cash_int, gold_int = _GLIDE_PATH_TABLE[_MAX_BUCKET]

    # 转为 0-1 浮点数
    stock_pct = stock_int / 100.0
    bond_pct = bond_int / 100.0
    cash_pct = cash_int / 100.0
    gold_pct = gold_int / 100.0

    has_override = False

    if user_overrides:
        has_override = True
        defaults = {
            "stock_pct": stock_pct,
            "bond_pct": bond_pct,
            "cash_pct": cash_pct,
            "gold_pct": gold_pct,
        }

        for field, override_val in user_overrides.items():
            if field not in defaults:
                continue
            default_val = defaults[field]
            deviation = abs(override_val - default_val)
            if deviation > USER_OVERRIDE_RANGE + 1e-9:  # 浮点容差
                raise OverrideOutOfRangeError(
                    field=field,
                    default_value=default_val,
                    override_value=override_val,
                    allowed_range=USER_OVERRIDE_RANGE,
                )

        # 应用覆盖
        if "stock_pct" in user_overrides:
            stock_pct = user_overrides["stock_pct"]
        if "bond_pct" in user_overrides:
            bond_pct = user_overrides["bond_pct"]
        if "cash_pct" in user_overrides:
            cash_pct = user_overrides["cash_pct"]
        if "gold_pct" in user_overrides:
            gold_pct = user_overrides["gold_pct"]

    return AllocationTarget(
        stock_pct=stock_pct,
        bond_pct=bond_pct,
        cash_pct=cash_pct,
        gold_pct=gold_pct,
        age_bucket=bucket,
        user_override=has_override,
    )


def check_style_deviation(
    actual_value_pct: float,
    actual_large_cap_pct: float,
    target_value_pct: float = DEFAULT_STYLE_VALUE_TARGET,
    target_large_cap_pct: float = DEFAULT_STYLE_LARGE_CAP_TARGET,
) -> List[DeviationAlert]:
    """检测风格偏离（价值/成长 + 大盘/小盘两个维度）。

    Args:
        actual_value_pct: 实际价值型基金占比（0-1）
        actual_large_cap_pct: 实际大盘基金占比（0-1）
        target_value_pct: 价值风格目标（默认 0.5）
        target_large_cap_pct: 大盘风格目标（默认 0.7）

    Returns:
        DeviationAlert 列表。无偏离时返回空列表。
    """
    alerts: List[DeviationAlert] = []

    # 价值偏离检测：目标 50%，实际 <40% 或 >60% → 标黄
    value_deviation = abs(actual_value_pct - target_value_pct)
    if value_deviation > STYLE_VALUE_YELLOW_THRESHOLD:
        alerts.append(DeviationAlert(
            level="yellow",
            dimension="value_style",
            actual=actual_value_pct,
            target=target_value_pct,
            threshold=STYLE_VALUE_YELLOW_THRESHOLD,
        ))

    # 大盘偏离检测：目标 70%，实际 <55% 或 >85% → 标黄
    large_cap_deviation = abs(actual_large_cap_pct - target_large_cap_pct)
    if large_cap_deviation > STYLE_LARGE_CAP_YELLOW_THRESHOLD:
        alerts.append(DeviationAlert(
            level="yellow",
            dimension="large_cap_style",
            actual=actual_large_cap_pct,
            target=target_large_cap_pct,
            threshold=STYLE_LARGE_CAP_YELLOW_THRESHOLD,
        ))

    return alerts


# ============================================================
# 导出
# ============================================================

__all__ = [
    "AllocationTarget",
    "DeviationAlert",
    "OverrideOutOfRangeError",
    "calculate_target_allocation",
    "check_style_deviation",
]
