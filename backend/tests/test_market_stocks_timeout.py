"""
market/stocks.py 裸调用超时接入守门测试（P1 后续，任务#15）
================================================================
背景：任务#14 排查发现 GET /api/watchlist/alerts（前端每15秒轮询交易
时段）间接调用的降级链（雪球→东财→新浪）里，雪球（get_stock_spot_xq）
和新浪（get_stock_daily_legacy）两处仍是裸调用无超时；fund_monitor.py
用的 get_fund_name_list/get_fund_estimated_nav 同样如此。本次给这4处
接入 infra/data_source/fallback.py 新增的 call_with_timeout()。

这个文件测什么：
  - 每处调用确实走 call_with_timeout（而不是看起来改了、其实还是裸调用）
  - call_with_timeout 返回 None（模拟超时放弃）时，四个函数都优雅返回
    None，不抛异常
  - 静态扫描：这4个函数体内不应再出现裸 ak.xxx() 调用

不测什么：
  - call_with_timeout 本身的线程超时机制（那是
    test_fallback_runner_timeout.py 的职责，这里只测"调用方是否正确
    接入"的契约）。
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import infra.data_source.market.stocks as stocks_module


def _mock_returns_none(*args, **kwargs):
    """模拟 call_with_timeout 超时放弃：返回 None。"""
    return None


@pytest.mark.parametrize("func_name,expect_kwargs", [
    ("get_stock_spot_xq", {"symbol": "SH600519"}),
    ("get_stock_daily_legacy", {"symbol": "sz000001"}),
    ("get_fund_name_list", {}),
    ("get_fund_estimated_nav", {}),
])
def test_function_uses_call_with_timeout_not_bare_call(func_name, expect_kwargs):
    """四个函数都必须通过 call_with_timeout 调用，不能退化回裸 ak.xxx()。"""
    func = getattr(stocks_module, func_name)
    with patch("infra.data_source.fallback.call_with_timeout") as mock_call:
        mock_call.return_value = None
        if func_name == "get_stock_spot_xq":
            func(symbol="SH600519")
        elif func_name == "get_stock_daily_legacy":
            func(symbol="sz000001")
        else:
            func()
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用"


def test_stock_spot_xq_degrades_on_timeout():
    """超时（call_with_timeout 返回 None）时，get_stock_spot_xq 必须原样
    返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none):
        result = stocks_module.get_stock_spot_xq(symbol="SH600519")
        assert result is None


def test_stock_daily_legacy_degrades_on_timeout():
    """超时时 get_stock_daily_legacy 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none):
        result = stocks_module.get_stock_daily_legacy(symbol="sz000001")
        assert result is None


def test_fund_name_list_degrades_on_timeout():
    """超时时 get_fund_name_list 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none):
        result = stocks_module.get_fund_name_list()
        assert result is None


def test_fund_estimated_nav_degrades_on_timeout():
    """超时时 get_fund_estimated_nav 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none):
        result = stocks_module.get_fund_estimated_nav()
        assert result is None


def test_no_bare_ak_calls_remain_in_these_four_functions():
    """回归锁定：这4个函数体内不应再出现裸 ak.xxx() 调用（必须经过
    call_with_timeout）。用函数源码文本定位，而不是整个文件扫描——
    因为文件里其他函数（如已走 FallbackRunner 的 get_stock_daily_hist）
    允许有不同的调用形态，不该被这条规则误伤。
    """
    import inspect

    targets = [
        stocks_module.get_stock_spot_xq,
        stocks_module.get_stock_daily_legacy,
        stocks_module.get_fund_name_list,
        stocks_module.get_fund_estimated_nav,
    ]
    bare_calls = []
    for fn in targets:
        src = inspect.getsource(fn)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\bak\.[a-z_]+\(", line) and "call_with_timeout(" not in line:
                bare_calls.append(f"{fn.__name__}: {stripped}")

    assert bare_calls == [], f"发现未接入 call_with_timeout 的裸调用: {bare_calls}"
