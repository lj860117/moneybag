"""
Behavior Reporter -- 季度行为模式报告
=====================================
将检测到的行为偏差模式生成 Markdown 格式的季度报告。

核心规则：
  - 用具体数据说话，不带情绪、不贴标签
  - 不输出投资建议（只描述行为模式）
  - 不足 3 个月交易数据时返回空报告
  - 合并到 M5 已有月度复盘报告中，新增"交易模式"章节

设计文档：docs/design/m7-plus/05-batch-m8-dynamic-threshold.md
不变式 #8：domain/services 之间禁止互相 import
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from domain.services.behavior_detector import BehaviorPattern


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BehaviorReport:
    """季度行为模式报告"""
    user_id: str
    quarter: str                   # 如 "2025Q4"
    patterns_found: int            # 检测到的模式数
    report_markdown: str           # Markdown 格式报告内容
    volatility_context: str        # 季度波动率背景描述
    generated_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.generated_at is None:
            self.generated_at = datetime.now()


# ============================================================
# 报告模板常量
# ============================================================

_REPORT_HEADER = """## 交易模式分析

> 本报告基于你 {quarter} 的手动交易记录生成，仅描述交易行为模式，不构成任何投资建议。

**市场背景：** {volatility_context}

---

"""

_PATTERN_SECTION = """### {index}. {title}

- **严重程度：** {severity_cn}
- **数据支撑：** {evidence_count}/{total_relevant} 次交易符合该模式（占比 {ratio:.0%}）
- **结论：** {description}
{market_note}
"""

_EMPTY_REPORT = """## 交易模式分析

> 数据不足：{quarter} 手动交易记录不足 3 个月，暂无法生成行为分析报告。
> 系统需要至少 3 个月的手动交易数据来识别有统计意义的行为模式。
"""

_NO_PATTERN_REPORT = """## 交易模式分析

> 本报告基于你 {quarter} 的手动交易记录生成。

**市场背景：** {volatility_context}

---

✅ 本季度未检测到明显的行为偏差模式。你的交易决策在数据层面未呈现显著的系统性偏差。
"""


# ============================================================
# 模式名称映射（pattern_type → 中文标题）
# ============================================================

_PATTERN_TITLES: dict[str, str] = {
    "chasing_high": "追高倾向",
    "stop_loss_inconsistent": "止损不一致",
    "confirmation_bias": "确认偏误",
    "fomo": "FOMO 交易",
    "over_trading": "过度交易",
    "high_pe_adding": "高位加仓",
    "anchoring": "锚定效应",
}

_SEVERITY_CN: dict[str, str] = {
    "mild": "轻微",
    "moderate": "中等",
    "severe": "显著",
}


# ============================================================
# 核心函数
# ============================================================

def generate_quarterly_report(
    patterns: list[BehaviorPattern],
    user_id: str,
    quarter: str,
    volatility_context: Optional[str] = None,
    has_enough_data: bool = True,
) -> BehaviorReport:
    """生成季度行为模式报告（Markdown 格式）。

    合并到 M5 已有的月度复盘报告中，新增"交易模式"章节。
    用具体数据说话，不带情绪、不贴标签。

    Args:
        patterns: detect_patterns() 的输出
        user_id: 用户 ID
        quarter: 季度标识（如 "2025Q4"）
        volatility_context: 季度波动率背景描述
        has_enough_data: 是否有足够数据（不足 3 个月时为 False）

    Returns:
        BehaviorReport — 含完整 Markdown 报告
    """
    # 默认波动率背景
    if volatility_context is None:
        volatility_context = "未获取到市场波动率数据"

    # 数据不足：返回空报告
    if not has_enough_data:
        return BehaviorReport(
            user_id=user_id,
            quarter=quarter,
            patterns_found=0,
            report_markdown=_EMPTY_REPORT.format(quarter=quarter),
            volatility_context=volatility_context,
        )

    # 无模式检出
    if not patterns:
        return BehaviorReport(
            user_id=user_id,
            quarter=quarter,
            patterns_found=0,
            report_markdown=_NO_PATTERN_REPORT.format(
                quarter=quarter,
                volatility_context=volatility_context,
            ),
            volatility_context=volatility_context,
        )

    # 生成报告正文
    markdown = _REPORT_HEADER.format(
        quarter=quarter,
        volatility_context=volatility_context,
    )

    for i, pattern in enumerate(patterns, 1):
        title = _PATTERN_TITLES.get(pattern.pattern_type, pattern.pattern_type)
        severity_cn = _SEVERITY_CN.get(pattern.severity, pattern.severity)

        # 市场背景注释
        market_note = ""
        if pattern.market_context:
            market_note = f"- **市场背景：** {pattern.market_context}\n"

        markdown += _PATTERN_SECTION.format(
            index=i,
            title=title,
            severity_cn=severity_cn,
            evidence_count=pattern.evidence_count,
            total_relevant=pattern.total_relevant,
            ratio=pattern.ratio,
            description=pattern.description,
            market_note=market_note,
        )

    # 添加尾部说明
    markdown += _build_footer(patterns)

    return BehaviorReport(
        user_id=user_id,
        quarter=quarter,
        patterns_found=len(patterns),
        report_markdown=markdown,
        volatility_context=volatility_context,
    )


# ============================================================
# 辅助函数
# ============================================================

def _build_footer(patterns: list[BehaviorPattern]) -> str:
    """生成报告尾部（统计摘要 + 免责声明）"""
    severe_count = sum(1 for p in patterns if p.severity == "severe")
    moderate_count = sum(1 for p in patterns if p.severity == "moderate")
    mild_count = sum(1 for p in patterns if p.severity == "mild")

    footer = "\n---\n\n### 统计摘要\n\n"
    footer += f"| 严重程度 | 数量 |\n|---|---|\n"
    footer += f"| 显著 | {severe_count} |\n"
    footer += f"| 中等 | {moderate_count} |\n"
    footer += f"| 轻微 | {mild_count} |\n"

    footer += "\n---\n\n"
    footer += (
        "> ⚠️ 本报告仅基于历史交易数据的统计分析，描述行为模式而非预测未来。"
        "不构成任何投资建议。\n"
    )

    return footer


# ============================================================
# 导出
# ============================================================

__all__ = [
    "BehaviorReport",
    "generate_quarterly_report",
]
