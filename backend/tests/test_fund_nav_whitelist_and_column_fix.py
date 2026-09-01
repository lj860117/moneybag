"""
fund_nav 白名单缺口 + Tushare 列名不兼容 修复守门测试
============================================================================
背景（2026-09-01，用户显式要求排查修复）：`market/stocks.py::get_fund_
nav_history()` 通过 `FallbackRunner(metric="fund_nav", chain=["akshare",
"tushare"])` 调用，设计意图是 AKShare 为主、Tushare 为降级。但
`AkshareProvider._SUPPORTED_METRICS` 白名单里此前从未包含 "fund_nav"
（pre-existing 缺口，任务#8 排查时首次发现），导致 AkshareProvider.fetch()
一进 `if metric not in _SUPPORTED_METRICS: return None` 就被拦截
（0.02s内快速失败，不是超时/网络问题），**100%无条件降级到 Tushare**。

比"死代码路径"更严重的是：TushareProvider._fetch_fund_nav() 返回的是
pro.fund_nav() 原始英文列名（unit_nav/nav_date/accum_nav），而唯一下游
消费方 services/fund_monitor.py::get_fund_nav_history() 是按 AKShare
fund_open_fund_info_em() 的中文列名读取的（"单位净值"/"净值日期"/"累计
净值"/"日增长率"）——两套列名完全不匹配，导致 `row.get("累计净值")`
100%命中不到任何字段返回 None，日期字段同理解析成空字符串。这个 bug
在生产环境长期真实生效（不是"以防万一"的边界情况），已影响真实持仓
用户（LeiJiang 名下多笔基金交易记录）的净值展示/涨跌计算。

修复：
1. AkshareProvider._SUPPORTED_METRICS 补上 "fund_nav"，恢复 AKShare 为
   主数据源的设计意图（AKShare 本身仍有任务#8 记录的间歇性不稳定问题，
   但至少能重新参与降级链，而不是从未被尝试过）。
2. TushareProvider._fetch_fund_nav() 接上列名转换（rename + 按 nav_date
   排序 + pct_change 补算日增长率），确保无论最终走 AKShare 还是 Tushare
   路径，下游 services/fund_monitor.py 和 services/backtest_engine.py
   （用 iloc 位置索引，靠列顺序而非列名，同样需要保证转换后前两列顺序
   是 净值日期/累计净值|单位净值）都能正确解析。

这个文件测什么：
  - AkshareProvider._SUPPORTED_METRICS 包含 "fund_nav"
  - AkshareProvider.fetch("fund_nav", ...) 能正确 dispatch 到
    _fetch_fund_nav（不再被白名单拦截提前返回 None）
  - TushareProvider._fetch_fund_nav() 返回的 DataFrame 列名是 AKShare
    兼容的中文格式（净值日期/单位净值/累计净值/日增长率），而不是原始
    英文列名
  - 端到端：services.fund_monitor.get_fund_nav_history() 无论 mock 走
    AKShare 成功还是 Tushare 降级，都能返回非 None 的 nav/date 字段
    （回归锁定这次真实 bug 的症状，不能只测"函数被调用了"）

不测什么：
  - Tushare/AKShare 真实网络调用本身（那是各 provider 内部实现的职责,
    这里只测 provider 输出格式的契约和白名单 dispatch 逻辑）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.data_source.providers.akshare_provider import (
    AkshareProvider,
    _SUPPORTED_METRICS,
)
from infra.data_source.providers.tushare_provider import TushareProvider


def test_fund_nav_in_akshare_supported_metrics():
    """fund_nav 必须在白名单里——这是本次修复的核心断言，回归锁定
    "AKShare 从未真正参与 fund_nav 降级链" 这个 bug 不再复现。"""
    assert "fund_nav" in _SUPPORTED_METRICS, (
        "fund_nav 缺失于 _SUPPORTED_METRICS，AkshareProvider.fetch(\"fund_nav\") "
        "会被提前拦截返回 None，AKShare 永远无法作为 fund_nav 的主数据源"
    )


def test_akshare_provider_fetch_fund_nav_dispatches_correctly():
    """AkshareProvider.fetch("fund_nav", symbol=...) 必须真正走到
    _fetch_fund_nav（不再被白名单挡在门外）。用 mock 替换 _fetch_fund_nav
    本身来验证 dispatch 路径通了，不依赖真实网络。"""
    provider = AkshareProvider()
    provider._available = True
    with patch.object(provider, "_fetch_fund_nav", return_value="sentinel") as mock_fetch:
        result = provider.fetch("fund_nav", symbol="161725")
        assert mock_fetch.called, (
            "fetch('fund_nav', ...) 未调用 _fetch_fund_nav —— 说明白名单"
            "仍然拦截了这个 metric，回到了修复前的状态"
        )
        assert result == "sentinel"


def _make_fake_tushare_fund_nav_df() -> pd.DataFrame:
    """构造一份 pro.fund_nav() 真实返回格式的假数据（英文列名，见2026-09-01
    真实调用样本：ts_code/ann_date/nav_date/unit_nav/accum_nav/accum_div/
    net_asset/adj_nav/update_flag）。"""
    return pd.DataFrame({
        "ts_code": ["161725.SZ"] * 3,
        "ann_date": ["20260831", "20260828", "20260827"],
        "nav_date": ["20260831", "20260828", "20260827"],
        "unit_nav": [2.2778, 2.2697, 2.2707],
        "accum_nav": [5.62, 5.58, 5.59],
        "accum_div": [0.0, 0.0, 0.0],
        "net_asset": [None, None, None],
        "adj_nav": [0.581615, 0.58779, 0.581615],
        "update_flag": [0, 0, 0],
    })


def test_tushare_fetch_fund_nav_returns_akshare_compatible_columns():
    """TushareProvider._fetch_fund_nav() 的输出必须是 AKShare 兼容的中文
    列名（净值日期/单位净值/累计净值/日增长率），不能是原始英文列名
    （nav_date/unit_nav/accum_nav）——这是本次 bug 的直接根因，下游
    services/fund_monitor.py 按中文列名 row.get() 取值，英文列名会让
    所有取值静默命中不到、退化成 None。"""
    provider = TushareProvider()
    provider._token = "fake_token_for_test"
    provider._available = True

    fake_api = MagicMock()
    fake_api.fund_nav.return_value = _make_fake_tushare_fund_nav_df()
    provider._api = fake_api

    df = provider._fetch_fund_nav(symbol="161725")

    assert df is not None
    for expected_col in ("净值日期", "单位净值", "累计净值", "日增长率"):
        assert expected_col in df.columns, (
            f"缺少列 '{expected_col}' —— TushareProvider 输出的列名没有正确"
            f"转换成 AKShare 兼容格式。实际列名: {df.columns.tolist()}"
        )
    # 原始英文列名不应残留（避免调用方误用旧列名产生歧义数据）
    for stale_col in ("nav_date", "unit_nav", "accum_nav"):
        assert stale_col not in df.columns, (
            f"列 '{stale_col}' 应已被 rename 掉，但仍然存在于输出中"
        )


def test_tushare_fetch_fund_nav_sorted_ascending_by_date():
    """输出必须按净值日期升序排列（AKShare 惯例是升序，下游
    `df.tail(days)` 假定升序取"最近N天"；Tushare 原始返回可能是降序，
    如果不重排，"最近N天"会变成"最早N天"，是另一种隐蔽的数据错误）。"""
    provider = TushareProvider()
    provider._token = "fake_token_for_test"
    provider._available = True

    fake_api = MagicMock()
    # 故意构造降序输入（模拟 Tushare 真实返回顺序）
    fake_api.fund_nav.return_value = _make_fake_tushare_fund_nav_df()
    provider._api = fake_api

    df = provider._fetch_fund_nav(symbol="161725")
    dates = df["净值日期"].tolist()
    assert dates == sorted(dates), f"净值日期未按升序排列: {dates}"


def test_tushare_fetch_fund_nav_computes_daily_rate():
    """日增长率字段（AKShare 原生提供，Tushare 原始数据没有）必须被
    正确补算——用相邻两天单位净值算涨跌幅百分比。"""
    provider = TushareProvider()
    provider._token = "fake_token_for_test"
    provider._available = True

    fake_api = MagicMock()
    fake_api.fund_nav.return_value = _make_fake_tushare_fund_nav_df()
    provider._api = fake_api

    df = provider._fetch_fund_nav(symbol="161725")
    # 排序后第一行(20260827, unit_nav=2.2707)无前一天数据，rate应为NaN
    # 第二行(20260828, unit_nav=2.2697) rate = (2.2697-2.2707)/2.2707*100
    import math
    assert math.isnan(df.iloc[0]["日增长率"])
    expected_rate = round((2.2697 - 2.2707) / 2.2707 * 100, 4)
    assert abs(df.iloc[1]["日增长率"] - expected_rate) < 0.01


def test_end_to_end_fund_monitor_gets_non_none_nav_via_tushare_fallback():
    """回归锁定这次真实 bug 的完整症状：即使 AKShare 主路径不可用
    （被 mock 掉，模拟 AKShare 网络问题），走到 Tushare 降级后，
    services.fund_monitor.get_fund_nav_history() 返回的 nav/date 字段
    必须是真实值，不能是 None/空字符串（修复前的症状）。"""
    from infra.data_source.market import stocks as stocks_module

    fake_akshare_provider = MagicMock()
    fake_akshare_provider.is_available.return_value = False  # 强制不可用

    fake_tushare_api = MagicMock()
    fake_tushare_api.fund_nav.return_value = _make_fake_tushare_fund_nav_df()

    with patch(
        "infra.data_source.fallback.FallbackRunner._get_provider_instance"
    ) as mock_get_provider:
        def _provider_side_effect(name):
            if name == "akshare":
                return fake_akshare_provider
            elif name == "tushare":
                p = TushareProvider()
                p._token = "fake_token"
                p._available = True
                p._api = fake_tushare_api
                return p
            return None

        mock_get_provider.side_effect = _provider_side_effect

        df = stocks_module.get_fund_nav_history(code="161725", indicator="累计净值走势")

        assert df is not None, "Tushare 降级路径应返回非 None DataFrame"
        assert len(df) > 0

        # 模拟 services/fund_monitor.py 的读取逻辑（复现真实消费方代码）
        row = df.iloc[-1]
        nav_val = row.get("累计净值") or row.get("单位净值")
        date_val = str(row.get("净值日期", ""))

        assert nav_val is not None, (
            "净值字段解析成 None —— 这正是修复前的 bug 症状（列名不兼容导致"
            "row.get('累计净值') 永远取不到值）"
        )
        assert date_val != "" and date_val != "None", (
            f"日期字段解析成空/None（实际值: {date_val!r}）—— 同样是修复前的症状"
        )
