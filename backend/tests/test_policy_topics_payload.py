"""
P0-A 行为级守卫：``GET /api/policy/impact`` 线上 500
====================================================

根因
----
``get_all_policy_topics()`` 往 ``topics[en_key]`` 里塞的是**新闻列表**
（正常路径 ``data.get("news", [])``、异常路径 ``[]`` —— 两条路径都是 list），
而下游 ``analyze_policy_impact_ds()`` / ``_build_policy_context()`` 按 dict
消费（``data.get("emoji")`` / ``data.get("news")``），于是

    AttributeError: 'list' object has no attribute 'get'

→ ``GET /api/policy/impact`` HTTP 500（生产 09-18 两次）。

关键点：**正常路径本身就是坏的**。只把异常分支改成 dict 是不够的——那只会把
崩溃从异常路径转移到更高频的正常路径上。所以这里锁的是：
  1. 两条路径都产出 dict；
  2. 两条路径产出的 dict **键集合完全一致**（否则就从「list vs dict」
     退化成更难发现的「dict vs dict 但键不齐」）。

不测什么
--------
  - 真实网络抓取（``get_policy_news_by_topic`` 一律用 fake 注入）
  - DeepSeek 调用（把 ``LLM_API_KEY`` 摘掉，走 data_only 分支）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.cache import MemoryCache  # noqa: E402
from services import policy_data  # noqa: E402


@pytest.fixture
def fresh_policy_cache(monkeypatch):
    """每个用例用全新缓存，避免用例间互相污染。"""
    monkeypatch.setattr(policy_data, "_policy_cache", MemoryCache(default_ttl=3600))
    return policy_data._policy_cache


def _fake_news(topic_cn, limit=5):
    return {
        "topic": topic_cn,
        "news": [{"title": f"{topic_cn}-news-{i}", "time": "2026-09-18"} for i in range(2)],
        "emoji": "🏠",
    }


def test_happy_path_topics_values_are_dicts_not_lists(fresh_policy_cache, monkeypatch):
    """正常路径：topics 的每个值都必须是 dict，不能是 news list。

    这是 500 的直接成因——修复前这里拿到的是 list，下游 .get() 立刻炸。
    """
    monkeypatch.setattr(policy_data, "get_policy_news_by_topic", _fake_news)

    topics = policy_data.get_all_policy_topics()["topics"]

    assert topics, "至少应有主题返回"
    for key, value in topics.items():
        assert isinstance(value, dict), f"topics[{key!r}] 是 {type(value).__name__}，不是 dict"
        # dict 必须真的能被下游 .get() 消费
        assert isinstance(value.get("news"), list)
        assert isinstance(value.get("emoji"), str) and value.get("emoji")


def test_error_and_happy_paths_emit_identical_key_sets(fresh_policy_cache, monkeypatch):
    """异常路径与正常路径的键集合必须完全一致。

    只把异常分支改成 dict 是不够的：键不同会制造出「dict vs dict 但键不齐」
    这种比原来更隐蔽的不一致。
    """
    def _boom(topic_cn, limit=5):
        raise RuntimeError("upstream exploded")

    # 正常路径
    monkeypatch.setattr(policy_data, "get_policy_news_by_topic", _fake_news)
    happy = policy_data.get_all_policy_topics()["topics"]

    # 异常路径
    monkeypatch.setattr(policy_data, "_policy_cache", MemoryCache(default_ttl=3600))
    monkeypatch.setattr(policy_data, "get_policy_news_by_topic", _boom)
    error = policy_data.get_all_policy_topics()["topics"]

    assert set(happy) == set(error), "两条路径覆盖的主题集合应一致"

    def _keys(value):
        """非 dict 的值单独标记，避免退化成难读的 TypeError。"""
        if isinstance(value, dict):
            return frozenset(value.keys())
        return frozenset({f"<NOT-A-DICT:{type(value).__name__}>"})

    happy_keys = {_keys(v) for v in happy.values()}
    error_keys = {_keys(v) for v in error.values()}
    assert len(happy_keys) == 1, f"正常路径内部键集合不统一: {happy_keys}"
    assert len(error_keys) == 1, f"异常路径内部键集合不统一: {error_keys}"
    assert happy_keys == error_keys, (
        f"正常路径与异常路径键集合不一致: {happy_keys} vs {error_keys}"
    )
    assert happy_keys == {frozenset(policy_data._TOPIC_PAYLOAD_KEYS)}


def test_analyze_policy_impact_ds_survives_legacy_list_payload(fresh_policy_cache, monkeypatch):
    """回归用例：喂入修复前的 list 形态，不得抛 AttributeError。

    修复前这里必抛 'list' object has no attribute 'get' → /api/policy/impact 500。
    这里锁的是「不再 500」，且新闻内容不能因为归一化被吞掉。
    """
    legacy = {
        "topics": {
            "realestate": [{"title": "楼市新政A"}, {"title": "楼市新政B"}],
            "tech": [{"title": "芯片补贴"}],
        }
    }
    monkeypatch.setattr(policy_data, "get_all_policy_topics", lambda: legacy)
    monkeypatch.setattr(policy_data, "get_real_estate_data", lambda: {"available": False})
    monkeypatch.delenv("LLM_API_KEY", raising=False)  # 走 data_only 分支，不碰网络

    result = policy_data.analyze_policy_impact_ds()

    assert isinstance(result, dict)
    assert result.get("source") == "data_only"
    # 新闻必须真的进了分析输入，而不是被静默丢弃
    analysis = result.get("analysis", "")
    assert "楼市新政A" in analysis
    assert "芯片补贴" in analysis


def test_normalizer_coerces_legacy_list_and_keeps_news():
    """归一化：list 形态被当作 news 保留，而不是当成错误丢掉。"""
    out = dict(policy_data._iter_policy_topics({"topics": {"realestate": [{"title": "X"}]}}))

    assert isinstance(out["realestate"], dict)
    assert out["realestate"]["news"] == [{"title": "X"}]
    assert out["realestate"]["emoji"], "归一化后 emoji 仍应有值"


def test_iter_policy_topics_tolerates_non_dict_container():
    """顶层不是 dict（如测试里的 fake 返回 list）时不应抛异常。"""
    assert list(policy_data._iter_policy_topics([])) == []
    assert list(policy_data._iter_policy_topics({"topics": []})) == []
