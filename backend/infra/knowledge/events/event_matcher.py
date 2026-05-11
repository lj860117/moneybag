"""
Event Matcher -- 事件匹配 + 模板填充
=====================================
根据新闻标题/摘要匹配预定义事件类型，填充模板生成解读文案。

匹配逻辑：
  - 每组关键词内用 AND（如 ["美联储", "加息"] 要求两词同时出现）
  - 组间用 OR（任一组命中即匹配成功）
  - 未匹配到任何模板时使用兜底模板

核心约束：
  - 填充后正文 ≤120 字（硬护栏）
  - 不输出投资建议
  - 不关联具体标的

设计文档：docs/design/m7-plus/07-batch-m9-event-interpretation.md
实现 NightWorkerStep Protocol（target_stage=2.5）

# TODO: §九验证2 — 凌晨工厂支持步骤扩展后，实现 NightWorkerStep 包装类
#       EventInterpretStep，挂载到阶段 2.5（规则引擎和 LLM 翻译之间）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from infra.knowledge.events.event_template_library import (
    EventTemplate,
    MatchedEvent,
    get_all_templates,
    get_fallback_template,
)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class NewsItem:
    """新闻条目（对应 Tushare major_news 接口返回）"""
    title: str                   # 新闻标题
    content: str = ""            # 新闻摘要/正文（可为空，主要用标题匹配）
    pub_date: date = date.today  # type: ignore  # 发布日期
    source: str = ""             # 来源

    def __post_init__(self) -> None:
        # 确保 pub_date 是 date 类型
        if callable(self.pub_date):
            self.pub_date = date.today()
        elif isinstance(self.pub_date, datetime):
            self.pub_date = self.pub_date.date()


# ============================================================
# 资产大类 → 中文映射（用于模板填充）
# ============================================================

ASSET_CLASS_DISPLAY: dict[str, str] = {
    "bond": "债券",
    "stock": "股票",
    "cash": "现金",
    "gold": "黄金",
    "cross_border": "跨境",
    "real_estate": "房产",
    "mixed": "综合",
}


# ============================================================
# 核心匹配逻辑
# ============================================================

def _match_keyword_group(text: str, keyword_group: list[str]) -> bool:
    """检查文本是否包含一组关键词（组内 AND）。"""
    return all(kw in text for kw in keyword_group)


def match_single_news(
    news: NewsItem,
    templates: list[EventTemplate],
) -> Optional[str]:
    """
    单条新闻匹配事件类型。

    将新闻标题和摘要合并后，依次匹配模板的关键词组。
    首个命中的模板即为匹配结果（模板列表顺序 = 优先级）。

    Returns:
        event_type 或 None（未匹配到任何模板，调用方应使用兜底）
    """
    # 合并标题+摘要作为匹配文本
    text = f"{news.title} {news.content}"

    for template in templates:
        # 每个模板的关键词组间是 OR 关系
        for group in template.keyword_groups:
            if _match_keyword_group(text, group):
                return template.event_type

    return None


def fill_template(
    template: EventTemplate,
    user_allocation: dict,
    news: NewsItem,
) -> str:
    """
    填充模板占位符，生成最终文案。

    占位符：
      - {related_allocation}: 用户持有的关联资产大类中文名
      - {event_display}: 事件显示名（兜底模板使用）

    约束：输出正文 ≤120 字（超出则截断并加省略号）。

    Args:
        template: 事件模板
        user_allocation: 用户当前资产配置 dict，key 为资产类别如 "bond"/"stock"
        news: 新闻条目（用于兜底模板提取事件名）
    """
    # 构建关联资产的中文描述
    related_parts: list[str] = []
    for asset_class in template.related_asset_classes:
        display = ASSET_CLASS_DISPLAY.get(asset_class, asset_class)
        # 如果用户持有该类资产，优先提示
        if asset_class in user_allocation:
            related_parts.append(display)
        else:
            related_parts.append(display)

    related_text = "、".join(related_parts) if related_parts else "综合"

    # 填充模板
    filled = template.template_text.format(
        related_allocation=related_text,
        event_display=template.display_name,
    )

    # 硬护栏：正文 ≤120 字
    if len(filled) > 120:
        filled = filled[:117] + "..."

    return filled


def match_events(
    news_list: list[NewsItem],
    user_allocation: dict,
    templates: Optional[list[EventTemplate]] = None,
) -> list[MatchedEvent]:
    """
    批量匹配事件。

    对每条新闻进行关键词匹配，命中则填充对应模板生成解读文案。
    未匹配到的新闻使用兜底模板。

    Args:
        news_list: 新闻列表（来自 Tushare major_news 或手动输入）
        user_allocation: 用户当前资产配置
        templates: 事件模板列表（默认使用 get_all_templates()）

    Returns:
        MatchedEvent 列表（已匹配+兜底）
    """
    if templates is None:
        templates = get_all_templates()

    fallback = get_fallback_template()
    results: list[MatchedEvent] = []

    for news in news_list:
        event_type = match_single_news(news, templates)

        if event_type is not None:
            # 找到匹配的模板
            matched_template: Optional[EventTemplate] = None
            for t in templates:
                if t.event_type == event_type:
                    matched_template = t
                    break

            if matched_template is None:
                continue  # 防御性，理论上不会发生

            # 提取命中的关键词（用于记录）
            text = f"{news.title} {news.content}"
            matched_kws: list[str] = []
            for group in matched_template.keyword_groups:
                if _match_keyword_group(text, group):
                    matched_kws = group
                    break

            filled_text = fill_template(matched_template, user_allocation, news)

            results.append(MatchedEvent(
                event_type=event_type,
                source_title=news.title,
                source_date=news.pub_date,
                matched_keywords=matched_kws,
                filled_text=filled_text,
                disclaimer=matched_template.disclaimer,
                is_fallback=False,
            ))
        else:
            # 使用兜底模板
            filled_text = fill_template(fallback, user_allocation, news)

            results.append(MatchedEvent(
                event_type="unclassified",
                source_title=news.title,
                source_date=news.pub_date,
                matched_keywords=[],
                filled_text=filled_text,
                disclaimer=fallback.disclaimer,
                is_fallback=True,
            ))

    return results


# ============================================================
# 导出
# ============================================================

__all__ = [
    "NewsItem",
    "match_events",
    "match_single_news",
    "fill_template",
    "ASSET_CLASS_DISPLAY",
]
