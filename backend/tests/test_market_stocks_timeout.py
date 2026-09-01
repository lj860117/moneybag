"""
market/stocks.py 裸调用超时接入守门测试（P1 后续，任务#15 + 任务#9）
================================================================
背景：任务#14 排查发现 GET /api/watchlist/alerts（前端每15秒轮询交易
时段）间接调用的降级链（雪球→东财→新浪）里，雪球（get_stock_spot_xq）
和新浪（get_stock_daily_legacy）两处仍是裸调用无超时；fund_monitor.py
用的 get_fund_name_list/get_fund_estimated_nav 同样如此。任务#15 给这4处
接入 infra/data_source/fallback.py 新增的 call_with_timeout()。

任务#9（2026-09-01 追加）：market/stocks.py 里当时还剩的另外11处裸调用
（get_stock_realtime_quotes_em/get_stock_realtime_single内部/
get_stock_realtime_quotes/get_stock_code_name_list/get_index_pe/
get_index_valuation_csindex/get_fund_rank/get_etf_fund_daily/
get_futures_main/get_futures_foreign_hist/get_restricted_release_summary）
同样接入。实测发现两个独立问题（不是本次超时保护要解决的，但记录下来）：
  - stock_zh_a_spot_em 曾直接 ConnectionError（6.4s才报错）
  - futures_foreign_hist 当前直接 ValueError: Expected object or value
    （数据解析错误，上游接口本身的问题）
这两个都被各自的 except Exception 兜住返回 None，超时保护解决的是另一个
维度的问题：挂死（而非快速报错）时不能无限期等待。

这个文件测什么：
  - 每处调用确实走 call_with_timeout（而不是看起来改了、其实还是裸调用）
  - call_with_timeout 返回 None（模拟超时放弃）时，函数都优雅返回
    None，不抛异常
  - 静态扫描：这些函数体内不应再出现裸 ak.xxx() 调用

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
    返回 None，不能抛异常。

    FIX（任务#9 排查时发现的共性问题回补）：必须断言 mock_call.called，
    否则如果函数退化回裸调用，mock 打不中，走真实网络调用——如果那次
    真实调用碰巧因网络问题也返回 None，测试会"看起来通过"但根本没验证
    到超时保护本身。这4个原有测试当时都漏了这条断言，一并补上。
    """
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none) as mock_call:
        result = stocks_module.get_stock_spot_xq(symbol="SH600519")
        assert mock_call.called, "必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_stock_daily_legacy_degrades_on_timeout():
    """超时时 get_stock_daily_legacy 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none) as mock_call:
        result = stocks_module.get_stock_daily_legacy(symbol="sz000001")
        assert mock_call.called, "必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_fund_name_list_degrades_on_timeout():
    """超时时 get_fund_name_list 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none) as mock_call:
        result = stocks_module.get_fund_name_list()
        assert mock_call.called, "必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_fund_estimated_nav_degrades_on_timeout():
    """超时时 get_fund_estimated_nav 必须原样返回 None，不能抛异常。"""
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none) as mock_call:
        result = stocks_module.get_fund_estimated_nav()
        assert mock_call.called, "必须通过 call_with_timeout 调用，否则测的是真实网络调用"
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


# ============================================================
# 任务#9 追加：另外11处裸调用（2026-09-01）
# ============================================================

@pytest.mark.parametrize("func_name,call_kwargs", [
    ("get_stock_realtime_quotes_em", {}),
    ("get_stock_realtime_quotes", {}),
    ("get_stock_code_name_list", {}),
    ("get_index_pe", {"symbol": "沪深300"}),
    ("get_index_valuation_csindex", {"symbol": "000300"}),
    ("get_fund_rank", {"symbol": "全部"}),
    ("get_etf_fund_daily", {}),
    ("get_futures_main", {"symbol": "AU0"}),
    ("get_futures_foreign_hist", {"symbol": "布伦特原油"}),
    ("get_restricted_release_summary", {}),
])
def test_task9_function_uses_call_with_timeout(func_name, call_kwargs):
    """任务#9新增的10个函数都必须通过 call_with_timeout 调用，不能是
    裸 ak.xxx()（get_stock_realtime_single 因为是内部三级降级链的一部分，
    不对外暴露独立函数入口，在下面单独测）。"""
    func = getattr(stocks_module, func_name)
    with patch("infra.data_source.fallback.call_with_timeout") as mock_call:
        mock_call.return_value = None
        func(**call_kwargs)
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用"


@pytest.mark.parametrize("func_name,call_kwargs", [
    ("get_stock_realtime_quotes_em", {}),
    ("get_stock_realtime_quotes", {}),
    ("get_stock_code_name_list", {}),
    ("get_index_pe", {"symbol": "沪深300"}),
    ("get_index_valuation_csindex", {"symbol": "000300"}),
    ("get_fund_rank", {"symbol": "全部"}),
    ("get_etf_fund_daily", {}),
    ("get_futures_main", {"symbol": "AU0"}),
    ("get_futures_foreign_hist", {"symbol": "布伦特原油"}),
    ("get_restricted_release_summary", {}),
])
def test_task9_function_degrades_on_timeout(func_name, call_kwargs):
    """超时（call_with_timeout 返回 None）时，函数必须原样返回 None，
    不能抛异常——即使外层还有 except Exception 兜底，也要验证 None 这条
    "正常降级路径"本身没被破坏（比如被误改成访问 df.columns 之类的操作
    导致 AttributeError，那样即使有 except 兜底也走的是"异常路径"而不是
    "预期的降级路径"，行为不完全等价）。

    FIX（F1 故障注入发现）：原实现只断言 result is None，没断言
    mock_call.called——如果函数体退化回裸调用（没接 call_with_timeout），
    mock 补丁完全打不中，函数会走真实网络调用；如果那次真实调用碰巧
    因为网络问题也返回了 None（实测过 ConnectionError 会被 except 兜成
    None），这个测试会"看起来通过"，但验证的根本不是"接入超时保护后
    优雅降级"，而是"真实网络调用这次也失败了"——这是又一个"看起来测了
    但抓不住回归"的死测试，和 P0-c/P0-b/P1 里遇到的同一类问题。
    """
    func = getattr(stocks_module, func_name)
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none) as mock_call:
        result = func(**call_kwargs)
        assert mock_call.called, (
            f"{func_name} 没有调用 call_with_timeout——如果退化回裸调用，"
            "这个测试测的是真实网络调用而不是超时保护"
        )
        assert result is None


def test_stock_realtime_single_first_tier_uses_call_with_timeout():
    """get_stock_realtime_single 三级降级链的第一层（AKShare 全市场快照）
    必须通过 call_with_timeout 调用——它内部裸调 ak.stock_zh_a_spot_em()，
    这个调用点不在任何独立函数名下（不是 def get_xxx() 后紧跟裸调用），
    容易在静态扫描时被漏掉，单独测。
    """
    with patch("infra.data_source.fallback.call_with_timeout") as mock_call:
        mock_call.return_value = None
        stocks_module.get_stock_realtime_single("600519")
        assert mock_call.called, (
            "get_stock_realtime_single 第一层（AKShare）必须通过 "
            "call_with_timeout 调用"
        )


def test_stock_realtime_single_falls_through_to_next_tier_on_timeout():
    """get_stock_realtime_single 第一层超时（call_with_timeout 返回 None）
    时，必须正确 fall through 到第二层（腾讯降级），不能因为第一层挂死
    就让整条三级降级链失效——这是本次接超时保护要保证的核心行为：超时
    和"AKShare 返回 None"应该有同样的降级效果。
    """
    with patch("infra.data_source.fallback.call_with_timeout", side_effect=_mock_returns_none):
        with patch(
            "infra.data_source.providers.tencent_provider.get_stock_quote_tencent",
            return_value={"price": 100.0, "name": "测试股票"},
        ) as mock_tencent:
            result = stocks_module.get_stock_realtime_single("600519")
            assert mock_tencent.called, "第一层超时后应该 fall through 到腾讯降级"
            assert result is not None
            assert result["source"] == "tencent"


def test_task9_no_bare_ak_calls_remain():
    """回归锁定：任务#9新增的这批函数体内不应再出现裸 ak.xxx() 调用。"""
    import inspect

    targets = [
        stocks_module.get_stock_realtime_quotes_em,
        stocks_module.get_stock_realtime_single,
        stocks_module.get_stock_realtime_quotes,
        stocks_module.get_stock_code_name_list,
        stocks_module.get_index_pe,
        stocks_module.get_index_valuation_csindex,
        stocks_module.get_fund_rank,
        stocks_module.get_etf_fund_daily,
        stocks_module.get_futures_main,
        stocks_module.get_futures_foreign_hist,
        stocks_module.get_restricted_release_summary,
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


def test_no_bare_ak_calls_remain_in_entire_module():
    """最终防线：整个 market/stocks.py 文件不应再有任何裸 ak.xxx() 调用
    （P1 + 任务#9 两轮修复后，这个文件应该是全仓库第一个"裸调用清零"的
    infra/data_source 文件）。
    """
    module_path = Path(stocks_module.__file__)
    bare_calls = []
    for i, line in enumerate(module_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"\bak\.[a-z_]+\(", line) and "call_with_timeout(" not in line:
            bare_calls.append(f"{i}: {stripped}")

    assert bare_calls == [], f"market/stocks.py 仍有裸调用未接超时保护: {bare_calls}"

