#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2-13 新闻模块：事件聚类 + 噪音过滤 + 重要度排序 回归测试。

设计原则（延续 P1-1 的教训）：**每条正面断言都配一条负面控制**。
聚类/过滤这类"少展示了一些东西"的逻辑最容易被写成"什么都少展示"还一直绿，
所以负面控制比正面断言更重要：

  - 聚类：证明它合并了同事件（正）→ 必须同时证明它没合并不同事件（负）
  - 过滤：证明它过滤了盘面播报（正）→ 必须同时证明它没误杀真实要闻（负）

全程离线：不触碰 AKShare / 网络 / LLM 网关，需要外部依赖时一律 monkeypatch。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import news_data as nd  # noqa: E402


# ------------------------------------------------------------------
# 1. 聚类：同一事件的不同报道聚成一簇
# ------------------------------------------------------------------

# 实测相似度 0.524，过阈值 0.50
T_RR_A = "央行决定下调存款准备金率0.5个百分点"
T_RR_B = "央行：下调金融机构存款准备金率0.5个百分点"
# 实测相似度 0.857
T_MOUTAI_A = "贵州茅台业绩预增净利润增长15%"
T_MOUTAI_B = "贵州茅台业绩预增，净利润增长两成"


def test_same_event_reports_cluster_into_one():
    """正面：两条标题不同但讲的是同一件事 → 聚成一簇。"""
    items = [{"title": T_RR_A}, {"title": T_RR_B}]
    clusters = nd.cluster_news_items(items)

    assert len(clusters) == 1
    assert clusters[0]["size"] == 2
    assert clusters[0]["related_count"] == 1
    reps = [i for i in items if i["is_representative"]]
    assert len(reps) == 1, "簇内只能有一条代表"


def test_cluster_representative_picks_most_informative():
    """簇代表 = 信息量最高的一条（有链接 > 有时间 > 有来源）。"""
    items = [
        {"title": T_MOUTAI_A, "time": "", "url": "", "source": ""},
        {"title": T_MOUTAI_B, "time": "2026-09-13", "url": "http://x/1", "source": "证券时报"},
    ]
    clusters = nd.cluster_news_items(items)
    assert clusters[0]["representative"] is items[1]
    assert items[0]["is_representative"] is False


def test_negative_control_opposite_events_never_merged():
    """【最重要】负面控制：「业绩预增」vs「业绩预减」相似度高达 0.75（远超阈值），
    但语义完全相反 —— 合并了就是把一条利好和一条利空揉成一条，比重复展示危害大得多。
    纯字符相似度救不了这种情况，必须靠 OPPOSITE_TERM_PAIRS 硬性否决。"""
    a, b = "某某股份业绩预增", "某某股份业绩预减"
    assert nd.title_similarity(a, b) > nd.CLUSTER_JACCARD_THRESHOLD, \
        "前置条件：这两条字面上确实高度相似，否则本测试没有在验证真正的风险"
    assert nd.has_opposite_terms(a, b) is True
    assert nd.is_same_event(a, b) is False

    clusters = nd.cluster_news_items([{"title": a}, {"title": b}])
    assert len(clusters) == 2, "不同事件（一利好一利空）绝不能合并"


def test_negative_control_similar_but_distinct_events_not_merged():
    """负面控制：同为央行新闻但确实是两件事（降准 vs 降LPR），相似度 0.20，
    低于阈值 → 不得合并。证明聚类不是"标题里都带'央行'就合并"。"""
    a, b = "央行下调存款准备金率0.5个百分点", "央行下调LPR利率10个基点"
    assert nd.title_similarity(a, b) < nd.CLUSTER_JACCARD_THRESHOLD
    assert nd.is_same_event(a, b) is False
    assert len(nd.cluster_news_items([{"title": a}, {"title": b}])) == 2


def test_negative_control_unrelated_titles_not_merged():
    """负面控制：完全不相干的两条（同一主体不同事件）不得合并。"""
    a = "贵州茅台发布三季报净利润增长15%"
    b = "贵州茅台与某酒企达成战略合作"
    assert nd.is_same_event(a, b) is False


def test_threshold_constants_are_documented_not_magic():
    """所有阈值/权重必须是模块级显式常量（可在测试里直接引用），
    不允许散落成函数内的字面量 —— 否则无法追溯来源。"""
    assert isinstance(nd.CLUSTER_JACCARD_THRESHOLD, float)
    assert isinstance(nd.SOURCE_WEIGHTS, dict)
    assert isinstance(nd.IMPORTANCE_LEVEL_WEIGHTS, dict)
    assert nd.IMPORTANCE_LEVEL_WEIGHTS["holding"] > \
        nd.IMPORTANCE_LEVEL_WEIGHTS["policy"] > \
        nd.IMPORTANCE_LEVEL_WEIGHTS["general"]


# ------------------------------------------------------------------
# 2. 噪音过滤：盘面播报进行情区，不进要闻
# ------------------------------------------------------------------

NOISE_TITLES = [
    "三大指数开盘涨跌不一 沪指涨0.2%",
    "沪指收盘涨0.3%，两市成交额8000亿",
    "收评：创业板指全天震荡走高",
    "早盘播报：两市高开低走",
]

KEEP_TITLES = [
    "央行下调存款准备金率0.5个百分点",           # 政策要闻，绝不能被误过滤
    "贵州茅台业绩预增，净利润增长15%",            # 个股要闻
    "证监会就IPO注册制征求意见",                  # 监管要闻
    "某某股份中标5亿元工程项目",                  # 个股要闻
]


@pytest.mark.parametrize("title", NOISE_TITLES)
def test_market_boardcast_is_filtered_with_rule_name(title):
    """正面：盘面播报类标题被识别，且能追溯到命中的规则名。"""
    hit = nd.match_noise_rule(title)
    assert hit is not None, f"{title} 应被判为盘面播报"
    rule_name, note = hit
    assert rule_name in {r["name"] for r in nd.NOISE_RULES}
    assert note  # 规则必须写明为什么算噪音


@pytest.mark.parametrize("title", KEEP_TITLES)
def test_negative_control_real_news_not_filtered(title):
    """【最重要】负面控制：真实要闻不得被误过滤。
    特别是"央行下调存款准备金率 0.5 个百分点"—— 它带"个百分点"，
    很容易被写歪的"数字+百分比=行情播报"规则误杀。"""
    assert nd.match_noise_rule(title) is None, f"{title} 是真实要闻，不能被过滤"


def test_filtered_noise_is_counted_and_traceable(monkeypatch):
    """被过滤的新闻不能凭空消失：filtered_noise 计数 + market_noise 带规则名。"""
    titles = ["三大指数开盘涨跌不一"] + KEEP_TITLES[:2]

    def fake_stock_news(code, limit):
        return [{"title": t, "source": "东方财富"} for t in titles]

    monkeypatch.setattr(nd, "get_stock_news_by_code", fake_stock_news)
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])

    result = nd.get_holdings_news([{"code": "600519"}], [])

    assert result["filtered_noise"] == 1
    assert len(result["market_noise"]) == 1
    assert result["market_noise"][0]["title"] == "三大指数开盘涨跌不一"
    assert result["market_noise"][0]["noise_rule"] in {r["name"] for r in nd.NOISE_RULES}
    # 去噪后的要闻区里不该再有它
    assert "三大指数开盘涨跌不一" not in [i["title"] for i in result["importance_ranked"]]
    # 但也没凭空消失：stocks 里仍在（兼容性）
    assert "三大指数开盘涨跌不一" in [i["title"] for i in result["stocks"]["600519"]]


# ------------------------------------------------------------------
# 3. 重要度排序
# ------------------------------------------------------------------

def _rank_titles(items):
    return [i["title"] for i in nd.rank_by_importance(items)]


def test_importance_holding_beats_policy_beats_general():
    """涉及持仓 > 政策级 > 普通，且来源权重不能跨层翻转。"""
    items = [
        {"title": "某某公司召开发布会", "source": "央行", "affected_holdings": []},      # 普通+最高来源
        {"title": "国务院印发扩大内需政策文件", "source": "东方财富",
         "affected_holdings": []},                                                    # 政策级+最低来源
        {"title": "某某股份业绩预增", "source": "未知小站", "affected_holdings": ["600519"]},  # 持仓+最低来源
    ]
    ranked = _rank_titles(items)
    assert ranked[0] == "某某股份业绩预增"          # holding
    assert ranked[1] == "国务院印发扩大内需政策文件"  # policy
    assert ranked[2] == "某某公司召开发布会"         # general


def test_source_weight_applies_within_same_level():
    """同一层内，来源权重高的排前面。"""
    items = [
        {"title": "国务院印发刺激方案（聚合源）", "source": "东方财富", "affected_holdings": []},
        {"title": "国务院印发刺激方案（官方源）", "source": "新华社", "affected_holdings": []},
    ]
    ranked = _rank_titles(items)
    assert ranked[0].endswith("（官方源）")
    assert nd._source_weight("新华社") > nd._source_weight("东方财富")


def test_unknown_source_is_neutral_not_punished():
    """未收录来源不惩罚也不奖赏（= 默认权重），避免隐性偏差。"""
    assert nd._source_weight("某个没收录的媒体") == nd.DEFAULT_SOURCE_WEIGHT
    assert nd._source_weight("") == nd.DEFAULT_SOURCE_WEIGHT


def test_ranking_is_stable_across_repeated_runs():
    """稳定性：同一批输入排序两次，结果必须完全一致（含同分项）。"""
    def build():
        return [
            {"title": "A 政策一", "source": "东方财富", "affected_holdings": []},
            {"title": "B 政策二", "source": "东方财富", "affected_holdings": []},
            {"title": "C 政策三", "source": "东方财富", "affected_holdings": []},
            {"title": "D 持仓", "source": "东方财富", "affected_holdings": ["600519"]},
        ]

    first = _rank_titles(build())
    second = _rank_titles(build())
    third = _rank_titles(build())
    assert first == second == third
    # 同分三项（A/B/C）保持输入相对顺序 → 证明排序是稳定的，不是随机打乱
    assert first == ["D 持仓", "A 政策一", "B 政策二", "C 政策三"]


def test_ranking_scores_are_deterministic(monkeypatch):
    """端到端：get_holdings_news 两次调用，importance_ranked 逐项一致（含分值）。"""
    titles = ["某某股份业绩预增", "国务院印发基建投资刺激方案", "公司召开股东大会"]

    monkeypatch.setattr(
        nd, "get_stock_news_by_code",
        lambda c, l: [{"title": t, "source": "证券时报"} for t in titles],
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])

    r1 = nd.get_holdings_news([{"code": "600519"}], [])
    r2 = nd.get_holdings_news([{"code": "600519"}], [])

    snap = lambda r: [(i["title"], i["importance_score"], i["importance_level"])
                      for i in r["importance_ranked"]]
    assert snap(r1) == snap(r2)
    assert snap(r1)[0][0] == "某某股份业绩预增"


# ------------------------------------------------------------------
# 4. 兼容性 & 已有行为不回退
# ------------------------------------------------------------------

def test_compat_three_legacy_keys_keep_semantics(monkeypatch):
    """stocks / funds / summary 三个键仍存在且语义不变。"""
    monkeypatch.setattr(
        nd, "get_stock_news_by_code",
        lambda c, l: [{"title": "贵州茅台业绩预增"}, {"title": "贵州茅台遭问询函"}],
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [{"title": "央行降准释放流动性"}])

    result = nd.get_holdings_news([{"code": "600519"}], [{"code": "110020"}])

    for key in ("stocks", "funds", "summary"):
        assert key in result
    assert isinstance(result["stocks"], dict) and isinstance(result["funds"], dict)
    assert isinstance(result["summary"], str)
    # 语义不变：stocks[code] 仍是该持仓的新闻列表
    assert [n["title"] for n in result["stocks"]["600519"]] == \
        ["贵州茅台业绩预增", "贵州茅台遭问询函"]
    assert [n["title"] for n in result["funds"]["110020"]] == ["央行降准释放流动性"]
    # 摘要仍如实反映利好/利空/未判定
    assert result["summary_source"] == "rule"
    assert "利好" in result["summary"] and "利空" in result["summary"]
    # 既有键不回退
    assert result["duplicates_removed"] == 0
    assert result["impact_map"]["600519"]["利好"] == 1
    assert result["impact_map"]["600519"]["利空"] == 1


def test_no_regression_unlabeled_still_shows_unjudged():
    """已有行为不回退：labeled=False 的未判定新闻仍显示为「未判定」而非「中性」。"""
    text = nd.format_holdings_news_for_prompt({
        "stocks": {"600519": [
            {"title": "召开股东大会", "sentiment": "中性", "labeled": False,
             "affected_holdings": ["600519"]},
        ]},
        "funds": {},
    })
    assert "未判定" in text
    assert "[中性]" not in text


def test_prompt_folds_cluster_members_into_representative():
    """聚类后 prompt 只喂簇代表，其余折叠为「另有N条相关报道」。"""
    text = nd.format_holdings_news_for_prompt({
        "stocks": {"600519": [
            {"title": T_MOUTAI_A, "sentiment": "利好", "labeled": True,
             "affected_holdings": ["600519"], "is_representative": True, "related_count": 1},
            {"title": T_MOUTAI_B, "sentiment": "利好", "labeled": True,
             "affected_holdings": ["600519"], "is_representative": False, "related_count": 1},
        ]},
        "funds": {},
    })
    assert T_MOUTAI_A in text
    assert T_MOUTAI_B not in text, "非簇代表不应重复注入 prompt"
    assert "另有1条相关报道" in text


def test_end_to_end_all_new_keys_present(monkeypatch):
    """端到端冒烟：新增键全部存在，且计数自洽。"""
    titles = [
        "三大指数开盘涨跌不一",                       # 噪音
        "央行决定下调存款准备金率0.5个百分点",          # 要闻 A
        "央行：下调金融机构存款准备金率0.5个百分点",     # 要闻 A 的另一篇报道 → 合并
        "某某股份业绩预增",                            # 要闻 B
    ]
    monkeypatch.setattr(
        nd, "get_stock_news_by_code",
        lambda c, l: [{"title": t, "source": "东方财富"} for t in titles],
    )
    monkeypatch.setattr(nd, "get_fund_news", lambda c, l: [])

    r = nd.get_holdings_news([{"code": "600519"}], [])

    assert r["filtered_noise"] == 1
    assert r["clusters_merged"] == 1, "降准那两篇应合并，合并掉 1 条"
    # 4 条 → 1 噪音 + 3 要闻，3 条要闻里 2 条同事件合并 → 要闻区 2 条
    assert len(r["importance_ranked"]) == 2
    assert all(i["importance_score"] > 0 for i in r["importance_ranked"])
