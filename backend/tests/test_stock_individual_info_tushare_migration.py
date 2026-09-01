"""
get_stock_individual_info Tushare 替换（任务#1，用户显式要求）守门测试
============================================================================
背景：用户要求"能用Tushare（5000积分）替换不稳定AKShare接口的就替换"。
get_stock_individual_info() 此前唯一数据源是 AKShare stock_individual_
info_em()，任务#8 诊断出该接口底层 push2.eastmoney.com 存在 ~30~70%
量级的间歇性连接被动 reset（完整消除法排查，详见
infra/data_source/alt/flows.py::get_stock_individual_info 函数内注释）。

改造：
1. infra/data_source/providers/tushare_provider.py 新增 metric
   "stock_basic_info"（_fetch_stock_basic_info），拼接
   pro.stock_basic()（行业/名称/上市时间）+ pro.daily_basic(limit=1)
   （市值，可选加分项，失败不影响主字段），输出格式对齐 AKShare 原版
   （[item, value] 两列 DataFrame）。
2. infra/data_source/alt/flows.py::get_stock_individual_info() 改为
   Tushare 为主（通过 TushareProvider 类调用，不直接依赖
   services.tushare_data——保持 infra 层不反向依赖 services 的架构
   契约），AKShare 降级为兜底。

这个文件测什么：
  - TushareProvider._SUPPORTED_METRICS 包含 "stock_basic_info"
  - TushareProvider._fetch_stock_basic_info() 返回格式正确（[item,
    value] 两列，包含行业/股票简称/上市时间字段）
  - daily_basic 市值查询失败不应影响行业等主字段返回（可选加分项
    的降级容错）
  - get_stock_individual_info() 优先走 Tushare，Tushare 不可用时
    正确降级到 AKShare
  - 端到端：真实下游消费方 services/holding_intelligence.py::
    get_stock_industry() 能从 Tushare 路径的返回值正确提取"行业"

不测什么：
  - AKShare stock_individual_info_em() 本身的真实网络行为（那是外部
    接口，任务#8 已有诊断记录，这里只测降级触发逻辑，不测其网络本身）
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
)
import infra.data_source.alt.flows as flows_module


def test_stock_basic_info_in_tushare_supported_metrics():
    """stock_basic_info 必须在 TushareProvider 白名单里。"""
    assert "stock_basic_info" in _TUSHARE_SUPPORTED_METRICS


def _make_fake_stock_basic_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["600519.SH"],
        "name": ["贵州茅台"],
        "industry": ["白酒"],
        "list_date": ["20010827"],
        "market": ["主板"],
    })


def _make_fake_daily_basic_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["600519.SH"],
        "trade_date": ["20260831"],
        "total_mv": [1624506.040832],  # 万元
        "circ_mv": [1624506.040832],
    })


def test_fetch_stock_basic_info_returns_akshare_compatible_format():
    """_fetch_stock_basic_info() 输出必须是 [item, value] 两列，包含
    行业/股票简称/上市时间字段——与 AKShare 原版格式兼容，下游
    services/holding_intelligence.py::get_stock_industry() 靠这个
    格式遍历找"行业"关键词行。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.stock_basic.return_value = _make_fake_stock_basic_df()
    fake_api.daily_basic.return_value = _make_fake_daily_basic_df()
    provider._api = fake_api

    df = provider._fetch_stock_basic_info(symbol="600519")

    assert df is not None
    assert list(df.columns) == ["item", "value"]
    items = dict(zip(df["item"], df["value"]))
    assert items["行业"] == "白酒"
    assert items["股票简称"] == "贵州茅台"
    assert items["上市时间"] == "20010827"
    # 市值单位换算：万元 × 10000 = 元（1624506.040832万元 × 10000 = 16245060408.32元）
    assert abs(items["总市值"] - 16245060408.320002) < 1


def test_fetch_stock_basic_info_market_cap_failure_does_not_break_industry_field():
    """daily_basic（市值）失败不应影响 stock_basic（行业等）主字段的
    返回——市值是可选加分项，主字段更重要（这是唯一真实消费方
    get_stock_industry() 需要的字段）。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.stock_basic.return_value = _make_fake_stock_basic_df()
    fake_api.daily_basic.side_effect = Exception("daily_basic 网络错误模拟")
    provider._api = fake_api

    df = provider._fetch_stock_basic_info(symbol="600519")

    assert df is not None
    items = dict(zip(df["item"], df["value"]))
    assert items["行业"] == "白酒", "市值查询失败不应影响行业字段"
    assert "总市值" not in items, "市值查询失败时不应包含总市值字段（不能编造假市值）"


def test_fetch_stock_basic_info_returns_none_on_empty_stock_basic():
    """stock_basic 返回空结果时应返回 None（股票代码不存在等场景），
    不应返回一个只有股票代码没有任何实际信息的空壮 DataFrame。"""
    provider = TushareProvider()
    provider._token = "fake_token"
    provider._available = True

    fake_api = MagicMock()
    fake_api.stock_basic.return_value = pd.DataFrame()
    provider._api = fake_api

    df = provider._fetch_stock_basic_info(symbol="999999")
    assert df is None


def test_get_stock_individual_info_prefers_tushare():
    """get_stock_individual_info() 必须优先尝试 Tushare，不应绕过它
    直接走 AKShare（回归锁定"Tushare 为主"的设计意图）。"""
    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(
             TushareProvider, "fetch",
             return_value=pd.DataFrame([("行业", "白酒")], columns=["item", "value"]),
         ) as mock_ts_fetch, \
         patch("akshare.stock_individual_info_em") as mock_ak:
        result = flows_module.get_stock_individual_info("600519")

        assert mock_ts_fetch.called, "应优先调用 TushareProvider.fetch"
        assert not mock_ak.called, "Tushare 成功时不应再降级到 AKShare"
        assert result is not None
        assert result.iloc[0]["value"] == "白酒"


def test_get_stock_individual_info_falls_back_to_akshare_when_tushare_unavailable():
    """Tushare 不可用（未配置 token 等）时必须降级到 AKShare，不能
    直接返回 None——保留"总比没有"的兜底能力。"""
    with patch.object(TushareProvider, "is_available", return_value=False), \
         patch("akshare.stock_individual_info_em", return_value="ak_sentinel") as mock_ak:
        result = flows_module.get_stock_individual_info("600519")

        assert mock_ak.called, "Tushare 不可用时应降级到 AKShare"
        assert result == "ak_sentinel"


def test_get_stock_individual_info_falls_back_to_akshare_when_tushare_returns_empty():
    """Tushare 可用但返回空结果（如股票代码不存在）时，也应降级到
    AKShare 再试一次，而不是直接放弃返回 None。"""
    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(TushareProvider, "fetch", return_value=None), \
         patch("akshare.stock_individual_info_em", return_value="ak_fallback_sentinel") as mock_ak:
        result = flows_module.get_stock_individual_info("600519")

        assert mock_ak.called
        assert result == "ak_fallback_sentinel"


def test_end_to_end_holding_intelligence_gets_correct_industry_via_tushare():
    """端到端回归锁定：真实下游消费方 services/holding_intelligence.py::
    get_stock_industry() 必须能从 Tushare 路径返回的 DataFrame 中正确
    提取"行业"字段值（不依赖真实网络，mock TushareProvider）。"""
    from infra.cache import MemoryCache
    import services.holding_intelligence as hi_module

    # 清空缓存，避免其他测试/真实调用留下的缓存影响本次断言
    hi_module._intel_cache = MemoryCache(default_ttl=3600)

    with patch.object(TushareProvider, "is_available", return_value=True), \
         patch.object(
             TushareProvider, "fetch",
             return_value=_make_fake_stock_basic_and_value_df(),
         ):
        industry = hi_module.get_stock_industry("600519")
        assert industry == "白酒"


def _make_fake_stock_basic_and_value_df() -> pd.DataFrame:
    return pd.DataFrame([
        ("股票代码", "600519"),
        ("股票简称", "贵州茅台"),
        ("行业", "白酒"),
        ("上市时间", "20010827"),
    ], columns=["item", "value"])
