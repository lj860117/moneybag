"""
fund_detail.py AKShare 超时保护守门测试（P0-c）
=================================================
背景：backend/api/fund_detail.py 里曾有 12 处裸 `ak.xxx()` 调用，全部没有
超时保护——上游（东财/新浪/巨潮资讯）网络挂死时，`requests.get` 会让处理
这次同步请求的 worker 线程永久卡死。这与 P0-b 看门狗要解决的问题同源：
`services/utils.py` 里的 `ak_call()`（daemon thread + join(timeout)）
写于 2026-06-14，此前零调用方，写完之后又放了两个多月零调用方——
本次是它第一次被真正接上业务代码。

这个文件测什么、不测什么：
  - 测什么：每个受影响的函数/端点在 ak_call 返回 None（模拟超时放弃）时，
    必须优雅降级（返回 available=False / ok=False / 沿用旧缓存），
    绝不能抛出未捕获异常导致 500，更不能让调用退化回裸 df.xxx() 触发
    AttributeError on None。
  - 也测什么：每个改动点确实调用了 ak_call（而不是看起来改了、
    其实还是走裸 ak.xxx()）——用 mock 断言调用发生过。
  - 不测什么：ak_call 内部的超时机制本身（那是 services/utils.py 的
    职责，属于另一个模块，这里只做"调用方是否正确使用"的契约测试）。

另外验证一个真实发现：`ak.stock_hot_rank_wc_em` 在当前 akshare(1.18.60)
里已经不存在（AttributeError），此前被 except: pass 静默吞掉，"雪球热度分"
功能已经失效了不知道多久。修复为 stock_hot_rank_em + 5 分钟 TTL 缓存，
两处重复逻辑（_inject_hot_scores / _ipo_watchlist_fallback）合并成
_get_hot_rank_scores()，避免同一个 bug 改一处漏一处。
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def fund_detail_module(tmp_path, monkeypatch):
    """每个测试用独立的 DATA_DIR，避免测试间通过文件缓存互相污染。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    import api.fund_detail as fd
    importlib.reload(fd)
    yield fd


def _mock_ak_call_returns_none(*args, **kwargs):
    """模拟 ak_call 超时放弃：调用方约定超时后返回 None。"""
    return None


# ============================================================
# 1. fund_purchase_em（全市场申购数据，实测 15.4s，最接近超时红线）
# ============================================================

def test_purchase_df_uses_ak_call_not_bare_call(fund_detail_module):
    """_get_purchase_df 必须通过 ak_call 调用，不能退化回裸 ak.fund_purchase_em()。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call") as mock_call:
        mock_call.return_value = None
        fd._purchase_df_cache["df"] = None
        fd._purchase_df_cache["t"] = 0
        fd._get_purchase_df()
        assert mock_call.called, "必须通过 ak_call 调用 fund_purchase_em，不能裸调"
        # timeout=25：实测 fund_purchase_em 耗时 15.4s，逼近默认 15s 超时，
        # 必须显式传更大的 timeout，否则会被自己的超时保护误杀
        _, kwargs = mock_call.call_args
        assert kwargs.get("timeout") == 25, "fund_purchase_em 必须用 25s 超时（实测 15.4s 太接近默认 15s）"


def test_purchase_df_timeout_returns_none_not_raise(fund_detail_module):
    """ak_call 超时返回 None 时，_get_purchase_df 必须原样返回 None，不能抛异常。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        fd._purchase_df_cache["df"] = None
        fd._purchase_df_cache["t"] = 0
        result = fd._get_purchase_df()
        assert result is None


def test_purchase_info_degrades_on_timeout(fund_detail_module):
    """上游超时时，_get_fund_purchase_info 必须返回 available=False，不能抛异常。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        fd._purchase_df_cache["df"] = None
        fd._purchase_df_cache["t"] = 0
        result = fd._get_fund_purchase_info("000001")
        assert result == {"available": False}


# ============================================================
# 2. 分红/拆分检测（fund_open_fund_info_em ×2）
# ============================================================

def test_dividend_recent_degrades_on_timeout(fund_detail_module):
    """分红/拆分接口超时时，必须返回空结果结构，不能抛异常。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd._get_fund_dividend_recent("000001")
        assert result["has_recent"] is False
        assert result["events"] == []


def test_dividend_recent_uses_ak_call_twice(fund_detail_module):
    """分红detail + 拆分detail 两次调用都必须走 ak_call。"""
    fd = fund_detail_module
    fd._dividend_cache.clear()
    with patch("services.utils.ak_call") as mock_call:
        mock_call.return_value = None
        fd._get_fund_dividend_recent("000002")
        assert mock_call.call_count == 2, "分红 + 拆分两处调用都必须走 ak_call"


# ============================================================
# 3. fund_portfolio_hold_em（季度持仓）
# ============================================================

def test_portfolio_holdings_degrades_on_timeout(fund_detail_module):
    """持仓接口连续两个年份都超时（返回None）时，必须返回 available=False。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        # fund_portfolio_holdings 是路由处理函数，内部会调用 _get_cached，
        # 先确保没有命中缓存
        fd._detail_cache.clear()
        result = fd.fund_portfolio_holdings("000001")
        assert result.get("available") is False


# ============================================================
# 4. stock_hot_rank_em（修复已死的 stock_hot_rank_wc_em）
# ============================================================

def test_hot_rank_function_name_is_not_the_dead_one(fund_detail_module):
    """回归锁定：确保没有人把函数名改回已经不存在的 stock_hot_rank_wc_em。

    真实背景：ak.stock_hot_rank_wc_em 在 akshare 1.18.60 里已被移除，
    调用会抛 AttributeError，此前被 except: pass 静默吞掉多时。
    只检查真实调用形式 `ak.stock_hot_rank_wc_em(`（排除注释里提到旧函数名
    做背景说明的行，那种提及是允许的、也是应该保留的）。
    """
    src = Path(fund_detail_module.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "ak.stock_hot_rank_wc_em(" not in line, (
            f"发现对已死函数的真实调用: {stripped!r} —— "
            "stock_hot_rank_wc_em 在当前 akshare 版本已不存在"
        )
    assert "ak.stock_hot_rank_em" in src, "热度榜必须使用仍然存在的 stock_hot_rank_em"


def test_hot_rank_scores_uses_ak_call_with_timeout(fund_detail_module):
    """_get_hot_rank_scores 必须走 ak_call 并显式传超时。"""
    fd = fund_detail_module
    fd._hot_rank_cache["scores"] = {}
    fd._hot_rank_cache["t"] = 0
    with patch("services.utils.ak_call") as mock_call:
        mock_call.return_value = None
        fd._get_hot_rank_scores()
        assert mock_call.called
        _, kwargs = mock_call.call_args
        assert kwargs.get("timeout") == 10


def test_hot_rank_scores_keeps_stale_cache_on_timeout(fund_detail_module):
    """超时时必须沿用旧缓存而不是清空——避免"接口偶尔慢一次"就让热度分整体消失。"""
    fd = fund_detail_module
    fd._hot_rank_cache["scores"] = {"中文在线": 1}
    fd._hot_rank_cache["t"] = 0  # 强制过期，触发重新拉取
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd._get_hot_rank_scores()
        assert result == {"中文在线": 1}, "拉取失败时应沿用旧缓存，不能清空"


def test_hot_rank_dedup_logic_shared_by_both_callers(fund_detail_module):
    """_inject_hot_scores 与 _ipo_watchlist_fallback 必须共用同一个取数函数。

    这是本次修复顺手解决的一个重复代码问题：原来两处各自实现一遍
    "拉热度榜 + 组装 dict" 的逻辑，如果只改一处会留下另一处继续调用
    已经不存在的函数——这正是 P0-b 里"零命中自检"教训的同型问题：
    同一个 bug 分散在两处，改一半等于没改。
    """
    src = Path(fund_detail_module.__file__).read_text(encoding="utf-8")
    assert src.count("def _get_hot_rank_scores") == 1
    # 两个调用者都必须引用共享函数，而不是各自重复实现
    assert "hot_scores = _get_hot_rank_scores()" in src
    assert src.count("hot_scores = _get_hot_rank_scores()") == 2, (
        "_inject_hot_scores 和 _ipo_watchlist_fallback 都必须调用共享函数"
    )


# ============================================================
# 5. fund_name_em（基金名称→代码，购买凭证解析用）
# ============================================================

def test_fund_code_lookup_falls_back_to_none_on_timeout(fund_detail_module):
    """名称查代码接口超时时，必须返回 None（找不到），不能抛异常。"""
    fd = fund_detail_module
    fd._fund_name_cache = {}
    fd._fund_name_cache_ts = 0.0
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd._get_fund_code_by_name("某基金")
        assert result is None


# ============================================================
# 6. fund_open_fund_info_em（单位净值走势，nav-history 端点）
# ============================================================

def test_nav_history_degrades_on_timeout(fund_detail_module):
    """净值历史接口超时时，端点必须返回 ok=False，不能抛异常。"""
    fd = fund_detail_module
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        fd._detail_cache.clear()
        result = fd.fund_nav_history("000001", days=90)
        assert result.get("ok") is False


# ============================================================
# 7. currency_boc_sina（汇率）
# ============================================================

def test_fx_rate_degrades_on_timeout(fund_detail_module):
    """汇率接口超时时，端点必须返回 ok=False，不能抛异常。"""
    fd = fund_detail_module
    fd._FX_CACHE.clear()
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd.get_fx_rate("USD")
        assert result.get("ok") is False


# ============================================================
# 8. stock_new_ipo_cninfo（A股IPO日历，2处：ipo_upcoming兜底 + upcoming-live）
# ============================================================

def test_ipo_upcoming_live_hs_degrades_on_timeout(fund_detail_module):
    """A股IPO日历超时时，端点必须返回 ok=False。"""
    fd = fund_detail_module
    fd._IPO_CACHE.clear()
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd.get_ipo_upcoming_live(market="hs")
        assert result.get("ok") is False


def test_ipo_upcoming_live_hk_degrades_on_timeout(fund_detail_module):
    """港股IPO日历超时时，端点必须返回 ok=False。"""
    fd = fund_detail_module
    fd._IPO_CACHE.clear()
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        result = fd.get_ipo_upcoming_live(market="hk")
        assert result.get("ok") is False


def test_ipo_upcoming_akshare_fallback_does_not_raise_on_timeout(fund_detail_module):
    """ipo_upcoming 的 akshare 兜底路径超时时不能抛异常（即使 tushare 那一路也失败）。"""
    fd = fund_detail_module
    fd._detail_cache.clear()
    with patch("services.utils.ak_call", side_effect=_mock_ak_call_returns_none):
        with patch("services.tushare_data._call_tushare", side_effect=Exception("tushare 不可用")):
            # 不应抛异常；具体返回结构以现网行为为准，这里只守护"不 500"
            result = fd.ipo_upcoming()
            assert isinstance(result, dict)


# ============================================================
# 9. 全量静态守门：确保没有遗留裸调用
# ============================================================

def test_no_bare_ak_calls_remain(fund_detail_module):
    """回归锁定：fund_detail.py 里不应再出现裸 ak.xxx() 调用（必须经过 ak_call）。

    检测方法：找到所有 `ak.函数名(` 模式，排除注释行和字符串里提到的旧函数名，
    确认每一处都是 `ak_call(ak.函数名` 的形式。
    """
    import re
    src_path = Path(fund_detail_module.__file__)
    bare_calls = []
    for i, line in enumerate(src_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"(?<!ak_call\()\bak\.[a-z_]+\(", line)
        if m and "ak_call(" not in line:
            bare_calls.append((i, line.strip()))
    assert bare_calls == [], f"发现未接入 ak_call 的裸调用: {bare_calls}"
