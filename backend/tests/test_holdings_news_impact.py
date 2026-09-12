#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1-1 持仓新闻：利好利空标签 + 全局去重 + 持仓影响映射 回归测试。

设计原则：**每条正面断言都配一条负面控制**，证明断言真的在被验证，
而不是测试写歪了还一直绿（P0-1 那个"空结果=100分=通过"的教训）。

全程离线：不触碰 AKShare / 企微 / LLM 网关，需要外部依赖时一律 monkeypatch。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import news_data as nd  # noqa: E402


# ------------------------------------------------------------------
# classify_sentiment
# ------------------------------------------------------------------

def test_lexicon_flags_bullish():
    sentiment, labeled, source, _ = nd.classify_sentiment("贵州茅台业绩预增，净利润增长15%")
    assert (sentiment, labeled) == ("利好", True)
    assert source == "lexicon"


def test_lexicon_flags_bearish():
    sentiment, labeled, source, _ = nd.classify_sentiment("某某股份遭大股东减持，业绩预减")
    assert (sentiment, labeled) == ("利空", True)
    assert source == "lexicon"


def test_negative_control_short_keyword_does_not_mislabel():
    """负面控制：标题里只有"增长"的近义泛词、没有词典命中时，
    必须 labeled=False —— 不能拿"中性"冒充已判定，否则下游会当成确认无风险。"""
    sentiment, labeled, source, _ = nd.classify_sentiment("公司召开2026年第一次临时股东大会")
    assert labeled is False, "没命中任何词典，必须如实标记未判定"
    assert sentiment == "中性"
    assert source == "none"


def test_conflicting_signals_marked_not_silently_picked():
    """多空信号同时出现时不猜方向，标中性 + label_source=lexicon_conflict。"""
    sentiment, labeled, source, _ = nd.classify_sentiment("业绩预增但遭高管减持")
    assert labeled is True          # 确实命中了词典
    assert sentiment == "中性"       # 但方向矛盾，不下结论
    assert source == "lexicon_conflict"


def test_impact_map_used_when_holding_code_matches():
    """宏观政策新闻：持仓代码在规则 bullish 名单里 → 利好，且带回 tag。"""
    sentiment, labeled, source, tag = nd.classify_sentiment("央行宣布降准释放流动性", "110020")
    assert (sentiment, labeled) == ("利好", True)
    assert source == "impact_map"
    assert tag == "货币宽松"


def test_impact_map_unmapped_is_not_fake_neutral():
    """负面控制：命中的宏观规则里既有 bullish 也有 bearish，但本持仓代码都不在
    名单内 → 必须 labeled=False，不能拍脑袋给"中性"。"""
    sentiment, labeled, source, _ = nd.classify_sentiment("美联储鹰派释放加息信号", "999999")
    assert labeled is False
    assert source == "impact_map_unmapped"


# ------------------------------------------------------------------
# 去重
# ------------------------------------------------------------------

def test_norm_title_ignores_punctuation_and_spaces():
    assert nd._norm_title("央行 降准！") == nd._norm_title("央行降准")


def _fake_news(titles):
    return [{"title": t} for t in titles]


def test_cross_holding_dedupe_keeps_one_and_maps_all(monkeypatch):
    """同一条新闻出现在两个持仓下：只保留一次，但 affected_holdings 记两个。"""
    shared = "央行降准释放流动性"

    def fake_stock_news(code, limit):
        return _fake_news([shared]) if code == "600519" else []

    def fake_fund_news(code, limit):
        return _fake_news([shared]) if code == "110020" else []

    monkeypatch.setattr(nd, "get_stock_news_by_code", fake_stock_news)
    monkeypatch.setattr(nd, "get_fund_news", fake_fund_news)

    result = nd.get_holdings_news([{"code": "600519"}], [{"code": "110020"}])

    assert result["duplicates_removed"] == 1
    assert list(result["stocks"].keys()) == ["600519"]
    # 第二次出现被去重，但影响归属没丢
    assert result["stocks"]["600519"][0]["affected_holdings"] == ["600519", "110020"]
    assert "110020" not in result["funds"]


def test_negative_control_dedupe_is_not_blanket_dropping(monkeypatch):
    """负面控制：标题不同的新闻不能被误去重，否则去重逻辑就是"只留第一条"。"""
    def fake_stock_news(code, limit):
        return _fake_news(["贵州茅台业绩预增", "五粮液中标大额订单"])

    monkeypatch.setattr(nd, "get_stock_news_by_code", fake_stock_news)
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])

    result = nd.get_holdings_news([{"code": "600519"}], [])
    assert result["duplicates_removed"] == 0
    assert len(result["stocks"]["600519"]) == 2


# ------------------------------------------------------------------
# 影响映射 / 汇总
# ------------------------------------------------------------------

def test_impact_map_counts_per_holding(monkeypatch):
    monkeypatch.setattr(
        nd, "get_stock_news_by_code",
        lambda c, l: _fake_news(["业绩预增超预期", "遭监管问询函"]),
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])

    result = nd.get_holdings_news([{"code": "600519"}], [])
    bucket = result["impact_map"]["600519"]

    assert bucket["利好"] == 1
    assert bucket["利空"] == 1
    assert result["labeled_count"] == 2
    assert result["unlabeled_count"] == 0


def test_summary_default_is_rule_based_no_llm_call(monkeypatch):
    """默认不调 LLM：既省成本，也保证离线可跑。"""
    def boom(*a, **kw):
        raise AssertionError("默认路径绝不能调用 LLM 网关")

    monkeypatch.setattr(
        nd, "get_stock_news_by_code",
        lambda c, l: _fake_news(["业绩预增"]),
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])
    monkeypatch.setattr(nd, "LLMGateway", boom, raising=False)

    result = nd.get_holdings_news([{"code": "600519"}], [])
    assert result["summary_source"] == "rule"
    assert "利好" in result["summary"]


def test_summary_degrades_honestly_when_llm_unavailable(monkeypatch):
    """LLM 失败/降级时，摘要必须明说"未生成"，不能用模板话术冒充结论。"""
    class _GW:
        @staticmethod
        def instance():
            return _GW()

        def call_sync(self, *a, **kw):
            return {"content": "", "fallback": True}

    monkeypatch.setattr(
        nd, "get_stock_news_by_code", lambda c, l: _fake_news(["发布三季报"]),
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])
    import infra.llm.gateway as gw_mod
    monkeypatch.setattr(gw_mod, "LLMGateway", _GW, raising=False)

    result = nd.get_holdings_news([{"code": "600519"}], [], llm_summary=True)
    assert result["summary_source"] == "rule"
    assert "未生成" in result["summary"]


# ------------------------------------------------------------------
# prompt 格式化
# ------------------------------------------------------------------

def test_prompt_marks_unlabeled_as_unjudged_not_neutral():
    """未判定必须显示成「未判定」，不能显示成「中性」误导 LLM。"""
    text = nd.format_holdings_news_for_prompt({
        "stocks": {"600519": [
            {"title": "召开股东大会", "sentiment": "中性", "labeled": False,
             "affected_holdings": ["600519"]},
        ]},
        "funds": {},
    })
    assert "未判定" in text
    assert "[中性]" not in text


def test_prompt_shows_multi_holding_impact():
    text = nd.format_holdings_news_for_prompt({
        "stocks": {"600519": [
            {"title": "央行降准", "sentiment": "利好", "labeled": True,
             "affected_holdings": ["600519", "110020"]},
        ]},
        "funds": {},
    })
    assert "同时影响：110020" in text


def test_prompt_empty_input_returns_empty_string():
    assert nd.format_holdings_news_for_prompt({}) == ""
