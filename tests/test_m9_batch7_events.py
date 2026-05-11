"""
Tests for M9 Batch 7: 事件解读模块
====================================
测试事件模板库 + 关键词匹配 + 模板填充。

运行方式：
    pytest tests/test_m9_batch7_events.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# 确保 backend 目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from infra.knowledge.events.event_template_library import (
    EventTemplate,
    MatchedEvent,
    TEMPLATES,
    FALLBACK_TEMPLATE,
    get_all_templates,
    get_template,
    get_fallback_template,
)
from infra.knowledge.events.event_matcher import (
    NewsItem,
    match_events,
    match_single_news,
    fill_template,
)


# ============================================================
# 模板库测试
# ============================================================

class TestEventTemplateLibrary:
    """事件模板库基础测试"""

    def test_template_count_gte_20(self):
        """模板库 ≥20 类事件"""
        templates = get_all_templates()
        assert len(templates) >= 20, f"模板数量不足：{len(templates)} < 20"

    def test_all_templates_have_required_fields(self):
        """所有模板必须包含必填字段"""
        for t in TEMPLATES:
            assert t.event_type, f"模板缺少 event_type"
            assert t.display_name, f"模板 {t.event_type} 缺少 display_name"
            assert len(t.keyword_groups) > 0, f"模板 {t.event_type} 缺少关键词组"
            assert t.template_text, f"模板 {t.event_type} 缺少 template_text"
            assert len(t.related_asset_classes) > 0, f"模板 {t.event_type} 缺少关联资产"
            assert t.disclaimer, f"模板 {t.event_type} 缺少免责声明"

    def test_event_type_unique(self):
        """事件类型标识唯一"""
        types = [t.event_type for t in TEMPLATES]
        assert len(types) == len(set(types)), f"存在重复的 event_type"

    def test_keyword_groups_non_empty(self):
        """每组关键词至少包含 1 个关键词"""
        for t in TEMPLATES:
            for group in t.keyword_groups:
                assert len(group) >= 1, (
                    f"模板 {t.event_type} 存在空关键词组"
                )

    def test_get_template_existing(self):
        """按类型查找已有模板"""
        t = get_template("fed_rate_hike")
        assert t is not None
        assert t.display_name == "美联储加息"

    def test_get_template_nonexistent(self):
        """查找不存在的模板返回 None"""
        t = get_template("nonexistent_event_xyz")
        assert t is None

    def test_fallback_template(self):
        """兜底模板存在且结构完整"""
        fb = get_fallback_template()
        assert fb.event_type == "unclassified"
        assert fb.display_name == "未分类事件"
        assert "{event_display}" in fb.template_text or "{related_allocation}" in fb.template_text

    def test_disclaimer_present(self):
        """所有模板含免责声明"""
        for t in TEMPLATES:
            assert "不构成投资建议" in t.disclaimer


# ============================================================
# 关键词匹配测试
# ============================================================

class TestEventMatcher:
    """事件匹配逻辑测试"""

    def test_match_fed_rate_hike(self):
        """美联储加息关键词匹配"""
        news = NewsItem(title="美联储宣布加息25个基点", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "fed_rate_hike"

    def test_match_fed_rate_hike_alt_keywords(self):
        """美联储加息替代关键词（Fed+加息）"""
        news = NewsItem(title="Fed再次加息，市场波动加大", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "fed_rate_hike"

    def test_match_pboc_rrr_cut(self):
        """央行降准匹配"""
        news = NewsItem(title="央行宣布全面降准0.5个百分点", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "pboc_rrr_cut"

    def test_match_geopolitical(self):
        """地缘紧张匹配"""
        news = NewsItem(title="多国地缘冲突升级引发市场担忧", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "geopolitical_tension"

    def test_match_trade_war(self):
        """贸易摩擦匹配"""
        news = NewsItem(title="新一轮关税加征计划公布", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "trade_war"

    def test_match_cpi_high(self):
        """通胀匹配"""
        news = NewsItem(title="5月CPI超预期上涨", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result == "cpi_high"

    def test_no_match_returns_none(self):
        """无法匹配的新闻返回 None"""
        news = NewsItem(title="某明星结婚消息刷屏", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result is None

    def test_and_logic_within_group(self):
        """组内 AND：需要同时包含所有关键词"""
        # 只有"美联储"没有"加息"，不应匹配 fed_rate_hike
        news = NewsItem(title="美联储主席发表讲话", pub_date=date(2026, 5, 1))
        result = match_single_news(news, TEMPLATES)
        assert result != "fed_rate_hike"

    def test_content_also_matches(self):
        """标题+摘要合并匹配"""
        news = NewsItem(
            title="重要经济政策发布",
            content="美联储宣布加息50个基点",
            pub_date=date(2026, 5, 1),
        )
        result = match_single_news(news, TEMPLATES)
        assert result == "fed_rate_hike"


# ============================================================
# 模板填充测试
# ============================================================

class TestFillTemplate:
    """模板填充 + 120 字限制测试"""

    def test_fill_basic(self):
        """基本模板填充"""
        template = get_template("fed_rate_hike")
        assert template is not None
        user_alloc = {"bond": 0.3, "stock": 0.5}
        news = NewsItem(title="美联储加息", pub_date=date(2026, 5, 1))

        filled = fill_template(template, user_alloc, news)
        assert "债券" in filled or "跨境" in filled
        assert len(filled) <= 120

    def test_fill_length_constraint(self):
        """填充后正文 ≤120 字"""
        for template in TEMPLATES:
            user_alloc = {"bond": 0.2, "stock": 0.5, "gold": 0.05}
            news = NewsItem(title="测试新闻", pub_date=date(2026, 5, 1))
            filled = fill_template(template, user_alloc, news)
            assert len(filled) <= 120, (
                f"模板 {template.event_type} 填充后 {len(filled)} 字超出限制"
            )

    def test_fill_fallback_template(self):
        """兜底模板填充"""
        fallback = get_fallback_template()
        user_alloc = {"stock": 0.6}
        news = NewsItem(title="未分类的市场动态", pub_date=date(2026, 5, 1))

        filled = fill_template(fallback, user_alloc, news)
        assert len(filled) <= 120


# ============================================================
# 批量匹配测试
# ============================================================

class TestMatchEvents:
    """批量匹配事件测试"""

    def test_match_events_basic(self):
        """基本批量匹配"""
        news_list = [
            NewsItem(title="美联储宣布加息25基点", pub_date=date(2026, 5, 1)),
            NewsItem(title="央行宣布降准", pub_date=date(2026, 5, 1)),
            NewsItem(title="某公司发布新产品", pub_date=date(2026, 5, 1)),
        ]
        user_alloc = {"bond": 0.3, "stock": 0.5}

        results = match_events(news_list, user_alloc)

        assert len(results) == 3
        assert results[0].event_type == "fed_rate_hike"
        assert results[0].is_fallback is False
        assert results[1].event_type == "pboc_rrr_cut"
        assert results[1].is_fallback is False
        assert results[2].event_type == "unclassified"
        assert results[2].is_fallback is True

    def test_match_events_all_have_disclaimer(self):
        """所有匹配结果都有免责声明"""
        news_list = [
            NewsItem(title="美联储加息", pub_date=date(2026, 5, 1)),
            NewsItem(title="随机新闻", pub_date=date(2026, 5, 1)),
        ]
        results = match_events(news_list, {"bond": 0.3})

        for r in results:
            assert "不构成投资建议" in r.disclaimer

    def test_match_events_filled_text_limit(self):
        """批量匹配的所有结果正文 ≤120 字"""
        news_list = [
            NewsItem(title="美联储加息", pub_date=date(2026, 5, 1)),
            NewsItem(title="央行降准释放流动性", pub_date=date(2026, 5, 1)),
            NewsItem(title="地缘冲突升级", pub_date=date(2026, 5, 1)),
            NewsItem(title="油价飙升创新高", pub_date=date(2026, 5, 1)),
            NewsItem(title="黄金大涨突破历史高位", pub_date=date(2026, 5, 1)),
        ]
        results = match_events(news_list, {"bond": 0.2, "stock": 0.5, "gold": 0.05})

        for r in results:
            assert len(r.filled_text) <= 120, (
                f"事件 {r.event_type} 正文 {len(r.filled_text)} 字超限"
            )

    def test_match_events_empty_list(self):
        """空新闻列表返回空结果"""
        results = match_events([], {"bond": 0.3})
        assert results == []

    def test_matched_keywords_recorded(self):
        """命中的关键词被记录"""
        news_list = [
            NewsItem(title="美联储宣布加息", pub_date=date(2026, 5, 1)),
        ]
        results = match_events(news_list, {"bond": 0.3})
        assert len(results) == 1
        assert "美联储" in results[0].matched_keywords
        assert "加息" in results[0].matched_keywords


# ============================================================
# NewsItem 测试
# ============================================================

class TestNewsItem:
    """NewsItem 数据结构测试"""

    def test_default_pub_date(self):
        """默认 pub_date 为今天"""
        news = NewsItem(title="测试")
        assert news.pub_date == date.today()

    def test_explicit_date(self):
        """可指定 pub_date"""
        news = NewsItem(title="测试", pub_date=date(2026, 1, 15))
        assert news.pub_date == date(2026, 1, 15)
