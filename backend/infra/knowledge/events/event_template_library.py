"""
Event Template Library -- 市场事件模板库（20-30 类）
===================================================
预定义事件模板，用于凌晨工厂/手动触发的事件解读。

关键词匹配规则：
  - 每组内用 AND（如同时含"美联储"和"加息"才匹配）
  - 组间用 OR（满足任意一组即命中）
  - 每个事件类型定义 2-3 组等价关键词，降低误报

核心约束：
  - 不输出投资建议（不说"应该买/卖什么"）
  - 不关联具体标的（只说"你的债券配置"，不说"XX 债券基金"）
  - 模板正文填充后 ≤120 字

设计文档：docs/design/m7-plus/07-batch-m9-event-interpretation.md
不变式 #1：AI 不预测证券价格
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ============================================================
# 数据结构
# ============================================================

@dataclass
class EventTemplate:
    """单个事件模板"""
    event_type: str                      # 事件类型标识（如 "fed_rate_hike"）
    display_name: str                    # 显示名（如 "美联储加息"）
    keyword_groups: list[list[str]]      # 关键词组列表，每组内 AND，组间 OR
    template_text: str                   # 模板正文（含占位符 {related_allocation}）
    related_asset_classes: list[str]     # 关联资产大类
    disclaimer: str = "历史统计不代表未来，不构成投资建议"


@dataclass
class MatchedEvent:
    """匹配到的单个事件"""
    event_type: str                      # 事件类型标识
    source_title: str                    # 原始新闻标题
    source_date: date                    # 新闻日期
    matched_keywords: list[str]          # 命中的关键词
    filled_text: str                     # 填充后的正文（≤120 字）
    disclaimer: str                      # 免责声明
    rag_links: list[str] = field(default_factory=list)  # 相关 RAG 文章链接
    is_fallback: bool = False            # 是否使用兜底模板


# ============================================================
# 兜底模板
# ============================================================

FALLBACK_TEMPLATE = EventTemplate(
    event_type="unclassified",
    display_name="未分类事件",
    keyword_groups=[],  # 兜底模板不通过关键词匹配
    template_text=(
        "检测到{event_display}相关动态。"
        "该类事件历史上可能影响{related_allocation}的波动率，"
        "具体程度取决于后续发展。"
        "建议关注你的相关配置偏离度是否处于目标区间。"
    ),
    related_asset_classes=["mixed"],
)


# ============================================================
# 模板常量区（25 类事件）
# ============================================================

TEMPLATES: list[EventTemplate] = [
    # ---- 货币政策类 ----
    EventTemplate(
        event_type="fed_rate_hike",
        display_name="美联储加息",
        keyword_groups=[["美联储", "加息"], ["Fed", "加息"], ["联储", "升息"]],
        template_text=(
            "加息通常提升美元资产收益率，对新兴市场资金有压力。"
            "历史看加息周期中债券价格波动加大。"
            "你的{related_allocation}配置是否需要关注。"
        ),
        related_asset_classes=["bond", "cross_border"],
    ),
    EventTemplate(
        event_type="fed_rate_cut",
        display_name="美联储降息",
        keyword_groups=[["美联储", "降息"], ["Fed", "降息"], ["联储", "减息"]],
        template_text=(
            "降息通常降低无风险利率，对债券价格和股市估值形成支撑。"
            "你的{related_allocation}配置可关注偏离度变化。"
        ),
        related_asset_classes=["bond", "stock"],
    ),
    EventTemplate(
        event_type="pboc_rrr_cut",
        display_name="央行降准",
        keyword_groups=[["降准"], ["存款准备金", "下调"], ["央行", "释放流动性"]],
        template_text=(
            "降准增加银行可贷资金，通常利好债市。"
            "对股市影响取决于资金是否流入实体经济。"
            "你的{related_allocation}配置可关注。"
        ),
        related_asset_classes=["bond", "stock"],
    ),
    EventTemplate(
        event_type="pboc_rrr_hike",
        display_name="央行提准",
        keyword_groups=[["提准"], ["存款准备金", "上调"], ["央行", "收紧"]],
        template_text=(
            "提准收紧流动性，短期可能增加资金面压力。"
            "你的{related_allocation}配置偏离度是否在目标区间。"
        ),
        related_asset_classes=["bond", "stock"],
    ),
    EventTemplate(
        event_type="pboc_rate_cut",
        display_name="央行降息",
        keyword_groups=[["LPR", "下调"], ["央行", "降息"], ["MLF", "下调"]],
        template_text=(
            "降息降低融资成本，通常对债市和权益市场形成支撑。"
            "你的{related_allocation}配置可留意变化。"
        ),
        related_asset_classes=["bond", "stock"],
    ),

    # ---- 财政政策类 ----
    EventTemplate(
        event_type="fiscal_stimulus",
        display_name="财政刺激",
        keyword_groups=[["财政", "刺激"], ["国债", "增发"], ["专项债", "扩大"]],
        template_text=(
            "财政刺激通常增加市场流动性和经济增长预期。"
            "你的{related_allocation}配置偏离度是否需要关注。"
        ),
        related_asset_classes=["stock", "bond"],
    ),
    EventTemplate(
        event_type="tax_reform",
        display_name="税收政策调整",
        keyword_groups=[["减税", "降费"], ["税收", "改革"], ["个税", "调整"]],
        template_text=(
            "税收政策变动可能影响企业盈利和居民可支配收入。"
            "你的{related_allocation}整体配置可留意。"
        ),
        related_asset_classes=["stock"],
    ),

    # ---- 行业政策类 ----
    EventTemplate(
        event_type="industry_policy",
        display_name="行业政策变化",
        keyword_groups=[
            ["政策", "利好"], ["政策", "扶持"], ["政策", "规范"],
        ],
        template_text=(
            "政策变化可能影响行业盈利预期。"
            "建议关注该行业在你配置中的占比是否超过目标。"
            "你的{related_allocation}配置偏离度值得留意。"
        ),
        related_asset_classes=["stock"],
    ),
    EventTemplate(
        event_type="real_estate_policy",
        display_name="房地产政策",
        keyword_groups=[["房地产", "政策"], ["楼市", "调控"], ["限购", "放松"]],
        template_text=(
            "房地产政策变动历史上对地产链和银行板块有影响。"
            "你的{related_allocation}配置是否需要关注相关敞口。"
        ),
        related_asset_classes=["stock", "real_estate"],
    ),
    EventTemplate(
        event_type="tech_regulation",
        display_name="科技监管",
        keyword_groups=[["科技", "监管"], ["互联网", "反垄断"], ["平台", "整改"]],
        template_text=(
            "科技行业监管变化可能影响相关公司盈利预期。"
            "你的{related_allocation}中科技相关占比是否在目标区间。"
        ),
        related_asset_classes=["stock"],
    ),

    # ---- 地缘政治类 ----
    EventTemplate(
        event_type="geopolitical_tension",
        display_name="地缘紧张",
        keyword_groups=[["地缘", "冲突"], ["地缘", "紧张"], ["军事", "冲突"]],
        template_text=(
            "地缘不确定性通常提升避险资产需求。"
            "你的{related_allocation}配置中避险资产是否充足。"
        ),
        related_asset_classes=["gold", "cash"],
    ),
    EventTemplate(
        event_type="trade_war",
        display_name="贸易摩擦",
        keyword_groups=[["贸易", "摩擦"], ["关税", "加征"], ["贸易战"]],
        template_text=(
            "贸易摩擦可能增加出口企业成本和汇率波动。"
            "你的{related_allocation}中跨境资产配置是否需要关注。"
        ),
        related_asset_classes=["stock", "cross_border"],
    ),
    EventTemplate(
        event_type="sanctions",
        display_name="制裁事件",
        keyword_groups=[["制裁"], ["实体清单"], ["出口管制"]],
        template_text=(
            "制裁事件可能影响相关行业供应链和股价预期。"
            "你的{related_allocation}相关敞口是否在目标区间。"
        ),
        related_asset_classes=["stock", "cross_border"],
    ),

    # ---- 宏观经济数据类 ----
    EventTemplate(
        event_type="cpi_high",
        display_name="通胀走高",
        keyword_groups=[["CPI", "超预期"], ["通胀", "走高"], ["物价", "上涨"]],
        template_text=(
            "通胀走高可能侵蚀实际购买力，对固收资产影响较大。"
            "你的{related_allocation}配置是否覆盖通胀风险。"
        ),
        related_asset_classes=["bond", "gold"],
    ),
    EventTemplate(
        event_type="gdp_slowdown",
        display_name="经济放缓",
        keyword_groups=[["GDP", "放缓"], ["经济", "下行"], ["PMI", "收缩"]],
        template_text=(
            "经济放缓可能影响企业盈利和市场风险偏好。"
            "你的{related_allocation}中防御性资产占比是否合理。"
        ),
        related_asset_classes=["bond", "cash"],
    ),
    EventTemplate(
        event_type="employment_weak",
        display_name="就业数据疲软",
        keyword_groups=[["就业", "不及预期"], ["失业率", "上升"], ["裁员"]],
        template_text=(
            "就业疲软信号可能预示经济降温，影响消费和企业营收。"
            "你的{related_allocation}配置防御性是否足够。"
        ),
        related_asset_classes=["bond", "cash"],
    ),

    # ---- 汇率类 ----
    EventTemplate(
        event_type="rmb_depreciation",
        display_name="人民币贬值",
        keyword_groups=[["人民币", "贬值"], ["汇率", "破"], ["离岸", "走弱"]],
        template_text=(
            "人民币贬值可能影响跨境资产的本币计价回报。"
            "你的{related_allocation}中外币资产占比值得关注。"
        ),
        related_asset_classes=["cross_border"],
    ),
    EventTemplate(
        event_type="rmb_appreciation",
        display_name="人民币升值",
        keyword_groups=[["人民币", "升值"], ["汇率", "走强"], ["结汇", "增加"]],
        template_text=(
            "人民币升值可能压缩外币资产本币回报但利好进口企业。"
            "你的{related_allocation}中外币敞口可关注。"
        ),
        related_asset_classes=["cross_border"],
    ),

    # ---- 市场流动性类 ----
    EventTemplate(
        event_type="liquidity_crunch",
        display_name="流动性紧张",
        keyword_groups=[["资金面", "紧张"], ["Shibor", "飙升"], ["钱荒"]],
        template_text=(
            "流动性紧张可能导致短期利率上行，债券和股票承压。"
            "你的{related_allocation}中现金比例是否充足。"
        ),
        related_asset_classes=["cash", "bond"],
    ),
    EventTemplate(
        event_type="ipo_surge",
        display_name="IPO 密集发行",
        keyword_groups=[["IPO", "加速"], ["新股", "密集"], ["注册制", "加速"]],
        template_text=(
            "IPO 密集期可能分流市场资金，对存量估值有一定压力。"
            "你的{related_allocation}配置偏离度可关注。"
        ),
        related_asset_classes=["stock"],
    ),

    # ---- 风险事件类 ----
    EventTemplate(
        event_type="credit_default",
        display_name="信用违约",
        keyword_groups=[["违约"], ["债务危机"], ["信用", "暴雷"]],
        template_text=(
            "信用违约事件可能引发市场避险情绪，信用债利差扩大。"
            "你的{related_allocation}中债券品质是否需要关注。"
        ),
        related_asset_classes=["bond"],
    ),
    EventTemplate(
        event_type="market_crash",
        display_name="市场大幅下跌",
        keyword_groups=[["暴跌"], ["熔断"], ["跌停潮"]],
        template_text=(
            "市场大幅下跌时需关注整体仓位风险和再平衡时机。"
            "你的{related_allocation}偏离度是否已触发再平衡信号。"
        ),
        related_asset_classes=["stock", "bond"],
    ),
    EventTemplate(
        event_type="bank_risk",
        display_name="银行业风险",
        keyword_groups=[["银行", "风险"], ["银行", "挤兑"], ["存款", "安全"]],
        template_text=(
            "银行业风险事件可能影响金融系统信心和资金安全感知。"
            "你的{related_allocation}中现金和存款安排可关注。"
        ),
        related_asset_classes=["cash", "bond"],
    ),

    # ---- 大宗商品类 ----
    EventTemplate(
        event_type="oil_price_surge",
        display_name="油价飙升",
        keyword_groups=[["油价", "飙升"], ["原油", "大涨"], ["OPEC", "减产"]],
        template_text=(
            "油价飙升可能传导至通胀和运输成本，影响相关行业。"
            "你的{related_allocation}配置中通胀对冲是否足够。"
        ),
        related_asset_classes=["gold", "stock"],
    ),
    EventTemplate(
        event_type="gold_surge",
        display_name="黄金大涨",
        keyword_groups=[["黄金", "大涨"], ["金价", "新高"], ["黄金", "飙升"]],
        template_text=(
            "黄金大涨通常反映市场避险需求或通胀预期升温。"
            "你的{related_allocation}中黄金占比是否处于目标区间。"
        ),
        related_asset_classes=["gold"],
    ),
]


# ============================================================
# 公开函数
# ============================================================

def get_all_templates() -> list[EventTemplate]:
    """返回所有预定义事件模板（25 类）。"""
    return TEMPLATES.copy()


def get_template(event_type: str) -> Optional[EventTemplate]:
    """按事件类型获取模板。未找到返回 None。"""
    for t in TEMPLATES:
        if t.event_type == event_type:
            return t
    return None


def get_fallback_template() -> EventTemplate:
    """返回兜底模板（未分类事件使用）。"""
    return FALLBACK_TEMPLATE


# ============================================================
# 导出
# ============================================================

__all__ = [
    "EventTemplate",
    "MatchedEvent",
    "TEMPLATES",
    "FALLBACK_TEMPLATE",
    "get_all_templates",
    "get_template",
    "get_fallback_template",
]
