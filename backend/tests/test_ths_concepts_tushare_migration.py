"""
同花顺题材 Tushare 替换（任务#3，用户显式要求）守门测试
============================================================================
背景：用户要求"能用Tushare(5000积分)替换不稳定AKShare接口的就替换"。
ths_concepts.py 的 get_hot_concepts()/get_concept_stocks() 此前依赖
AKShare stock_board_concept_name_ths()（本身仍可用，但只有 [name,code]
两列，从未真正提供过按涨跌幅排序的"热门"语义——任务#3 实施时才发现
这个静默 bug）+ stock_board_concept_cons_ths()（任务#8 诊断出已被
AKShare 库升级彻底删除，AttributeError 长期被 except 吞掉，恒返回
空列表）。两个接口必须配套才有意义，已切换到 Tushare
（ths_index+ths_daily+ths_member）为主，AKShare 降级兜底。

这个文件测什么：
  - TushareProvider._SUPPORTED_METRICS 包含 ths_hot_concepts/
    ths_concept_members
  - _fetch_ths_hot_concepts()：ths_index join ths_daily 正确按涨跌幅
    降序排列，输出字段名对齐 AKShare 原格式
  - _fetch_ths_concept_members()：题材名称→ts_code 映射查找 + 成分股
    正确提取为6位代码
  - get_hot_concepts()/get_concept_stocks() 优先走 Tushare，Tushare
    不可用时降级到 AKShare
  - AKShare 降级路径的诚实性：stock_board_concept_name_ths() 实际
    只有 [name,code] 两列，降级结果的"涨跌幅"字段必须是 None（不能
    编造假涨跌幅数据）
  - 端到端：get_stock_theme_tags() 能正确反查题材归属（回归锁定
    services/recommend_engine.py::_score_theme() 这个真实消费方
    的功能，此前因为死接口一直恒定返回中性50分）

不测什么：
  - Tushare/AKShare 真实网络调用本身（那是外部依赖，这里只测数据
    拼接和降级触发逻辑）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.data_source.providers.tushare_provider import (
    TushareProvider,
    _SUPPORTED_METRICS as _TUSHARE_SUPPORTED_METRICS,
    _financial_cache as _tushare_financial_cache,
)
import infra.data_source.alt.ths_concepts as ths_module


@pytest.fixture(autouse=True)
def _clear_module_level_caches():
    """_get_ths_concept_index_map()/_get_latest_ths_daily() 用的是模块级
    _financial_cache（TushareProvider 类外部共享），不是每个 provider
    实例独立的——不清空会导致测试间缓存互相污染（前一个测试写入的
    ths_index/ths_daily 缓存被后一个测试复用，即使换了不同的
    fake_api.side_effect）。每个测试前自动清空。"""
    _tushare_financial_cache.clear()
    ths_module._cache.clear()
    yield
    _tushare_financial_cache.clear()
    ths_module._cache.clear()


def test_ths_metrics_in_tushare_supported_metrics():
    assert "ths_hot_concepts" in _TUSHARE_SUPPORTED_METRICS
    assert "ths_concept_members" in _TUSHARE_SUPPORTED_METRICS


def _make_fake_ths_index_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["886068.TI", "886060.TI", "885942.TI"],
        "name": ["AI视频", "短剧游戏", "数据安全"],
        "count": [56, 87, 174],
        "exchange": ["A", "A", "A"],
        "list_date": ["20240219", "20231208", "20211129"],
        "type": ["N", "N", "N"],
    })


def _make_fake_ths_daily_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["886068.TI", "886060.TI", "885942.TI"],
        "trade_date": ["20260831"] * 3,
        "pct_change": [5.7393, 5.3478, 1.2],
    })


def test_fetch_ths_hot_concepts_sorts_by_pct_change_descending():
    """必须按涨跌幅降序排列，字段名对齐 AKShare 原格式
    （板块名称/涨跌幅/成分股数量）。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.ths_index.return_value = _make_fake_ths_index_df()
    fake_api.trade_cal.return_value = pd.DataFrame({"cal_date": ["20260901", "20260831"]})
    fake_api.ths_daily.side_effect = lambda trade_date: (
        _make_fake_ths_daily_df() if trade_date == "20260831" else pd.DataFrame()
    )
    provider._api = fake_api

    result = provider._fetch_ths_hot_concepts(limit=10)

    assert result is not None
    assert len(result) == 3
    pct_changes = [r["涨跌幅"] for r in result]
    assert pct_changes == sorted(pct_changes, reverse=True), "必须按涨跌幅降序排列"
    assert result[0]["板块名称"] == "AI视频"
    assert result[0]["成分股数量"] == 56


def test_fetch_ths_hot_concepts_handles_todays_data_not_ready_yet():
    """Tushare ths_daily 存在 T+1 延迟——2026-09-01 实测当天查询返回
    0 行，前一交易日才有数据。必须能正确回退到最近一个有数据的交易日，
    不能因为"今天没数据"就整体失败。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.ths_index.return_value = _make_fake_ths_index_df()
    fake_api.trade_cal.return_value = pd.DataFrame({
        "cal_date": ["20260901", "20260831", "20260828", "20260827", "20260826"]
    })

    call_log = []

    def _ths_daily_side_effect(trade_date):
        call_log.append(trade_date)
        if trade_date == "20260901":
            return pd.DataFrame()  # 今天还没数据
        return _make_fake_ths_daily_df()

    fake_api.ths_daily.side_effect = _ths_daily_side_effect
    provider._api = fake_api

    result = provider._fetch_ths_hot_concepts(limit=10)

    assert result is not None, "应正确回退到有数据的交易日，不应整体失败"
    assert "20260901" in call_log, "应先尝试最新交易日"
    assert len(call_log) > 1, "最新交易日无数据时应继续尝试更早的交易日"


def test_fetch_ths_concept_members_resolves_name_to_ts_code():
    """题材名称必须先在 ths_index 映射表里查到 ts_code，再用 ts_code
    （不是名称本身）调用 ths_member。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.ths_index.return_value = _make_fake_ths_index_df()
    fake_api.ths_member.return_value = pd.DataFrame({
        "ts_code": ["885942.TI"] * 3,
        "con_code": ["000004.SZ", "000032.SZ", "000066.SZ"],
        "con_name": ["*ST国华", "深桑达A", "中国长城"],
    })
    provider._api = fake_api

    codes = provider._fetch_ths_concept_members(concept_name="数据安全")

    assert codes == ["000004", "000032", "000066"]
    fake_api.ths_member.assert_called_once_with(ts_code="885942.TI")


def test_fetch_ths_concept_members_returns_none_for_unknown_concept():
    """题材名称在映射表里找不到时应返回 None（不是空列表），区分
    "这个题材真的没有成分股"和"这个题材名称本身就不存在/输入有误"。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.ths_index.return_value = _make_fake_ths_index_df()
    provider._api = fake_api

    codes = provider._fetch_ths_concept_members(concept_name="不存在的题材名称xyz")
    assert codes is None


def test_get_hot_concepts_prefers_tushare():
    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(
             TushareProvider, "fetch",
             return_value=[{"板块名称": "数据安全", "涨跌幅": 2.5, "成分股数量": 174}],
         ) as mock_ts_fetch, \
         patch("akshare.stock_board_concept_name_ths") as mock_ak:
        # 清空缓存避免其他测试污染
        ths_module._cache.delete("hot_concepts")
        result = ths_module.get_hot_concepts(limit=10)

        assert mock_ts_fetch.called
        assert not mock_ak.called
        assert result[0]["板块名称"] == "数据安全"


def test_get_hot_concepts_falls_back_to_akshare_honestly_reports_no_pct_change():
    """AKShare 降级路径必须诚实：stock_board_concept_name_ths() 实际
    只有 [name,code] 两列（2026-09-01 实测确认，此前代码误以为有
    涨跌幅字段），降级结果的"涨跌幅"必须是 None，不能编造假数据。"""
    fake_ak_df = pd.DataFrame({
        "name": ["AI PC", "AI手机"],
        "code": ["309121", "309120"],
    })
    with patch.object(TushareProvider, "is_available", return_value=False), \
         patch("akshare.stock_board_concept_name_ths", return_value=fake_ak_df):
        ths_module._cache.delete("hot_concepts")
        result = ths_module.get_hot_concepts(limit=10)

        assert len(result) == 2
        for r in result:
            assert r["涨跌幅"] is None, "AKShare 路径没有涨跌幅数据，不能编造"
            assert r["板块名称"] in ("AI PC", "AI手机")


def test_get_concept_stocks_prefers_tushare():
    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(TushareProvider, "fetch", return_value=["000004", "000032"]) as mock_ts_fetch, \
         patch("akshare.stock_board_concept_cons_ths", create=True) as mock_ak:
        codes = ths_module.get_concept_stocks("数据安全")

        assert mock_ts_fetch.called
        assert not mock_ak.called
        assert codes == ["000004", "000032"]


def test_get_concept_stocks_falls_back_to_akshare_when_tushare_unavailable():
    """AKShare stock_board_concept_cons_ths() 在当前 AKShare 1.18.60
    中已被库升级彻底删除（任务#8 诊断结论），真实环境里访问这个属性
    会直接 AttributeError（模块压根没有这个名字），不是"调用后抛异常"
    ——用 create=True 让 patch 允许目标属性不存在，同时用 AttributeError
    模拟真实的失败形态。"""
    with patch.object(TushareProvider, "is_available", return_value=False), \
         patch("akshare.stock_board_concept_cons_ths", create=True,
               side_effect=AttributeError("module 'akshare' has no attribute "
                                           "'stock_board_concept_cons_ths'")):
        codes = ths_module.get_concept_stocks("数据安全")
        # AKShare 接口已死，降级也会失败，最终应优雅返回空列表而非抛异常
        assert codes == []


def test_end_to_end_get_stock_theme_tags_via_tushare():
    """端到端回归锁定真实消费方 services/recommend_engine.py::
    _score_theme() 的功能：get_stock_theme_tags() 必须能正确反查
    题材归属（此前因死接口一直恒定返回空列表，导致题材评分永远是
    中性50分）。"""
    fake_hot_concepts = [
        {"板块名称": "AI视频", "涨跌幅": 5.7, "成分股数量": 56, "ts_code": "886068.TI"},
        {"板块名称": "短剧游戏", "涨跌幅": 5.3, "成分股数量": 87, "ts_code": "886060.TI"},
    ]

    def _fake_fetch(metric=None, **kwargs):
        if metric == "ths_hot_concepts":
            return fake_hot_concepts
        elif metric == "ths_concept_members":
            concept = kwargs.get("concept_name")
            if concept == "AI视频":
                return ["000681", "000156"]
            elif concept == "短剧游戏":
                return ["300999"]
            return None
        return None

    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(TushareProvider, "fetch", side_effect=_fake_fetch):
        ths_module._cache.delete("hot_concepts")
        ths_module._cache.delete("concept_stocks_AI视频")
        ths_module._cache.delete("concept_stocks_短剧游戏")
        ths_module._cache.delete("theme_tags_000681")

        tags = ths_module.get_stock_theme_tags("000681", top_concepts=10)
        assert tags == ["AI视频"], f"应命中AI视频题材，实际: {tags}"
