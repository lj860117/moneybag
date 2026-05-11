"""
事件解读模块（M9 Batch 7）
==========================
市场事件关键词匹配 + 模板填充，生成定时解读。

核心原则：
  - 不预测涨跌，只解释事件对配置的潜在影响
  - 关键词匹配（非 NLP/AI）
  - 单条正文 ≤120 字
  - 结尾必带免责声明

设计文档：docs/design/m7-plus/07-batch-m9-event-interpretation.md
"""
from infra.knowledge.events.event_template_library import (
    EventTemplate,
    MatchedEvent,
    get_all_templates,
    get_template,
    get_fallback_template,
    TEMPLATES,
)
from infra.knowledge.events.event_matcher import (
    match_events,
    match_single_news,
    fill_template,
    NewsItem,
)

__all__ = [
    "EventTemplate",
    "MatchedEvent",
    "get_all_templates",
    "get_template",
    "get_fallback_template",
    "TEMPLATES",
    "match_events",
    "match_single_news",
    "fill_template",
    "NewsItem",
]
