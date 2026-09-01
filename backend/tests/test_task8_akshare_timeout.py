"""
任务#8：macro/indicators.py + alt/flows.py + akshare_provider.py +
ths_concepts.py + fundamental/financials.py 剩余59处（AST校验后为57处，
文本粗算重复计入2个装饰性文档字符串）裸 ak.xxx() 调用接超时保护
============================================================================
背景：任务#9（market/stocks.py）之后，用户显式授权继续处理这5个文件里
剩余的裸调用。用 AST 解析（非文本 grep）精确定位，逐一测量真实服务器
延迟后统一接入 call_with_timeout()（10s 为主，个别大表/慢接口用15s）。

排查过程中额外发现的问题（不是本次超时保护要解决的，但记录+已处理）：
  1. get_stock_individual_info（alt/flows.py）：push2.eastmoney.com
     后端间歇性连接被动 reset（~30~70%失败率，非超时/挂死），已在函数
     docstring 写入完整消除法诊断结论，避免未来重新排查。
  2. 4个已被 AKShare 库升级删除的接口（AttributeError 一直被吞掉）：
     - akshare_provider.py 的 _fetch_fund_name (`ak.fund_info_sz/sh`)
     - akshare_provider.py 的 _fetch_fund_rank (`ak.fund_rank_ts`)
     - financials.py 的 get_stock_lg_indicator (`ak.stock_a_lg_indicator`)
       → 已找到替代接口 `ak.stock_a_gxl_lg` 并切换（验证可用）
     - ths_concepts.py 的 get_concept_stocks (`ak.stock_board_concept_cons_ths`)
  3. 3个解析层已损坏（100%复现，非网络问题）：
     stock_market_activity_legu / stock_hsgt_hold_stock_em /
     fund_portfolio_hold_em —— 均已在各自 docstring 标注，未做功能修复
     （范围外，需要单独评估）。

本文件测什么：
  - 5个文件里每个新接入的调用点确实走 call_with_timeout（而非退化回裸调用）
  - call_with_timeout 返回 None（模拟超时放弃）时，所有函数都优雅返回
    None/[]，不抛异常
  - 静态扫描（AST）：5个文件不应再出现任何裸 ak.xxx() 调用
  - get_stock_lg_indicator 的新接口切换：验证 symbol 映射逻辑正确

不测什么：
  - call_with_timeout 本身的线程超时机制（test_fallback_runner_timeout.py
    的职责）
  - 已知失效接口的功能修复效果（那些接口本身已被 AKShare 库删除/损坏，
    不在本次任务范围内，只验证"超时保护外壳"正确接入）

⚠️ 关键设计差异（务必注意，避免像任务#9那次一样漏掉 mock.called 断言）：
  这5个文件用**模块级** `from infra.data_source.fallback import
  call_with_timeout`（而不是 market/stocks.py 那种函数体内 local import）。
  这意味着 mock 必须 patch **各模块自己的绑定名**（如
  "infra.data_source.macro.indicators.call_with_timeout"），
  patch "infra.data_source.fallback.call_with_timeout" 在这里不会生效
  ——因为 from-import 后模块内的名字已经是独立引用，patch 原始出处对
  已经 import 进来的名字没有任何效果。这个坑如果不注意，所有 mock 都会
  静默失效、走真实网络调用，重演任务#9里那次"4th occurrence"。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import infra.data_source.macro.indicators as indicators_module
import infra.data_source.alt.flows as flows_module
import infra.data_source.alt.ths_concepts as ths_module
import infra.data_source.fundamental.financials as financials_module
from infra.data_source.providers.akshare_provider import AkshareProvider


def _mock_returns_none(*args, **kwargs):
    """模拟 call_with_timeout 超时放弃：返回 None。"""
    return None


# ============================================================
# macro/indicators.py —— 22 处调用点全覆盖
# ============================================================

_INDICATORS_CASES = [
    ("get_china_money_supply", {}),
    ("get_china_social_financing", {}),
    ("get_china_lpr", {}),
    ("get_china_real_estate", {}),
    ("get_china_new_house_price", {}),
    ("get_china_cpi", {}),
    ("get_china_pmi", {}),
    ("get_china_ppi", {}),
    ("get_china_gdp", {}),
    ("get_china_industrial_value_added", {}),
    ("get_china_retail_sales", {}),
    ("get_china_fixed_asset_investment", {}),
    ("get_usa_interest_rate", {}),
    ("get_market_activity", {}),
    ("get_lhb_detail", {}),
    ("get_management_holding_detail", {}),
    ("get_us_index", {"symbol": ".DJI"}),
    ("get_fx_spot_quote", {}),
    ("get_global_market_pe", {"symbol": "美国"}),
    ("get_stock_news", {"symbol": "财经"}),
]


@pytest.mark.parametrize("func_name,call_kwargs", _INDICATORS_CASES)
def test_indicators_function_uses_call_with_timeout(func_name, call_kwargs):
    """macro/indicators.py 中的函数必须通过 call_with_timeout 调用。"""
    func = getattr(indicators_module, func_name)
    with patch("infra.data_source.macro.indicators.call_with_timeout") as mock_call:
        mock_call.return_value = None
        func(**call_kwargs)
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用"


@pytest.mark.parametrize("func_name,call_kwargs", _INDICATORS_CASES)
def test_indicators_function_degrades_on_timeout(func_name, call_kwargs):
    """超时（call_with_timeout 返回 None）时必须优雅返回 None，不抛异常。"""
    func = getattr(indicators_module, func_name)
    with patch(
        "infra.data_source.macro.indicators.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = func(**call_kwargs)
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_get_global_futures_snapshot_uses_call_with_timeout():
    """get_global_futures_snapshot 内部调用 futures_global_spot_em，
    超时时应返回 {"available": False, ...} 而不是抛异常。"""
    with patch(
        "infra.data_source.macro.indicators.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = indicators_module.get_global_futures_snapshot()
        assert mock_call.called
        assert result["available"] is False


def test_get_hsi_latest_uses_call_with_timeout():
    """get_hsi_latest 内部调用 stock_hk_index_daily_sina，超时时应返回 None。"""
    with patch(
        "infra.data_source.macro.indicators.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = indicators_module.get_hsi_latest()
        assert mock_call.called
        assert result is None


# ============================================================
# alt/flows.py —— 16 处调用点全覆盖
# ============================================================

_FLOWS_CASES = [
    ("get_hsgt_hist", {"symbol": "沪股通"}),
    ("get_hsgt_hold_stock", {}),
    ("get_margin_sse", {}),
    ("get_bond_zh_us_rate", {}),
    ("get_interbank_rate", {}),
    ("get_individual_fund_flow_rank", {}),
    ("get_individual_fund_flow", {"stock": "000001"}),
    ("get_zt_pool", {}),
    ("get_block_trade_daily", {}),
    ("get_insider_trade_xq", {}),
    ("get_sector_fund_flow_rank", {}),
    # 注意：get_industry_board_summary 从此列表移除——它有磁盘缓存兜底
    # （24h grace period），"超时"后不一定返回 None，可能命中缓存返回
    # 真实历史数据，见 test_get_industry_board_summary_uses_call_with_timeout。
    ("get_stock_individual_info", {"symbol": "600519"}),
    ("get_futures_news", {}),
]


@pytest.mark.parametrize("func_name,call_kwargs", _FLOWS_CASES)
def test_flows_function_uses_call_with_timeout(func_name, call_kwargs):
    """alt/flows.py 中的函数必须通过 call_with_timeout 调用。"""
    func = getattr(flows_module, func_name)
    with patch("infra.data_source.alt.flows.call_with_timeout") as mock_call:
        mock_call.return_value = None
        func(**call_kwargs)
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用"


@pytest.mark.parametrize("func_name,call_kwargs", _FLOWS_CASES)
def test_flows_function_degrades_on_timeout(func_name, call_kwargs):
    """超时时必须优雅返回 None，不抛异常。"""
    func = getattr(flows_module, func_name)
    with patch(
        "infra.data_source.alt.flows.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = func(**call_kwargs)
        assert mock_call.called, f"{func_name} 必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_get_north_net_flow_uses_call_with_timeout():
    """get_north_net_flow 内部两次调用 stock_hsgt_hist_em（沪股通+深股通），
    超时时任一次拿不到数据都应返回 None（该函数无 Tushare 降级，见函数
    docstring 里的口径说明——故意不加，防止把成交额伪装成净流入）。"""
    with patch(
        "infra.data_source.alt.flows.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = flows_module.get_north_net_flow()
        assert mock_call.called
        assert result is None


def test_get_industry_board_summary_uses_call_with_timeout():
    """get_industry_board_summary 必须通过 call_with_timeout 调用底层
    stock_board_industry_summary_ths。

    注意：这个函数有磁盘缓存兜底（24h grace period，见函数 docstring），
    "超时"（call_with_timeout 返回 None）后不一定返回 None——如果磁盘上
    有近24小时内的成功缓存，会命中缓存返回真实历史数据。这里只验证
    "确实调用了 call_with_timeout"（超时保护已接入），不断言返回值，
    避免测试结果依赖运行环境里是否恰好存在缓存文件（那是另一层独立
    行为，不属于本次任务#8 的验证范围）。
    """
    with patch(
        "infra.data_source.alt.flows.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        flows_module.get_industry_board_summary()
        assert mock_call.called, "get_industry_board_summary 必须通过 call_with_timeout 调用"



# ============================================================
# providers/akshare_provider.py —— 14 处调用点全覆盖
# ============================================================

_PROVIDER_METRIC_CASES = [
    ("macro_gdp", {}),
    ("macro_cpi", {}),
    ("macro_pmi", {}),
    ("macro_shibor", {}),
    ("macro_lpr", {}),
    ("macro_m1_m2", {}),
    ("stock_news", {"symbol": "财经"}),
    ("northbound_flow", {}),
    ("margin_detail", {}),
    ("block_trade", {}),
    # 注意：fund_rank 从此列表移除（见 test_fund_rank_metric_is_dead_code_path）
    # fund_name/fund_nav 同理，见下方专门的死代码路径说明测试。
]


@pytest.mark.parametrize("metric,params", _PROVIDER_METRIC_CASES)
def test_akshare_provider_metric_uses_call_with_timeout(metric, params):
    """AkshareProvider.fetch(metric) 必须通过 call_with_timeout 调用底层 ak.xxx()。

    fund_name/fund_rank/fund_nav 三个 metric 已从本参数化列表移除，各自
    有独立的测试覆盖其"死代码路径/死接口"的特殊情况，见
    test_fund_name_fund_rank_dead_interface_still_degrades_gracefully
    和 test_fund_nav_metric_is_pre_existing_dead_code_path。
    """
    provider = AkshareProvider()
    provider._available = True  # 跳过 is_available() 的真实 import 检查
    with patch("infra.data_source.providers.akshare_provider.call_with_timeout") as mock_call:
        mock_call.return_value = None
        provider.fetch(metric, **params)
        assert mock_call.called, f"metric={metric} 必须通过 call_with_timeout 调用"


@pytest.mark.parametrize("metric,params", _PROVIDER_METRIC_CASES)
def test_akshare_provider_metric_degrades_on_timeout(metric, params):
    """超时时 AkshareProvider.fetch(metric) 必须返回 None，不抛异常。"""
    provider = AkshareProvider()
    provider._available = True
    with patch(
        "infra.data_source.providers.akshare_provider.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = provider.fetch(metric, **params)
        assert mock_call.called, f"metric={metric} 必须通过 call_with_timeout 调用，否则测的是真实网络调用"
        assert result is None


def test_fund_name_fund_rank_dead_interface_still_degrades_gracefully():
    """fund_name/fund_rank 的底层接口（ak.fund_info_sz/sh, ak.fund_rank_ts）
    已被 AKShare 库删除（2026-09-01 任务#8 排查发现，见 akshare_provider.py
    对应 docstring）。

    这带来一个和其他 metric 不同的测试形态：`ak.fund_info_sz` 作为**属性
    访问**本身就会立即抛出 AttributeError（Python 对函数调用的参数求值
    发生在函数体执行之前），所以 `call_with_timeout(ak.fund_info_sz, 10)`
    这行代码里，`ak.fund_info_sz` 这个属性访问会在 call_with_timeout
    真正被调用之前就失败——mock.called 会是 False，这是符合预期的（不是
    超时保护没接上，是底层接口从物理上已经不存在，无法拿到函数引用）。

    这里验证的是"即使底层接口已死，函数依然优雅返回 None，不会让
    AttributeError 冒泡到调用方"——这才是这两个 metric 现在真正需要的
    保证。fund_rank 额外验证 metric 白名单确实包含它（dispatch 能走到
    _fetch_fund_rank），只是接口本身死了。
    """
    provider = AkshareProvider()
    provider._available = True

    result_name = provider.fetch("fund_name")
    assert result_name is None, "fund_name 底层接口已死，应优雅返回 None"

    result_rank = provider.fetch("fund_rank")
    assert result_rank is None, "fund_rank 底层接口已死，应优雅返回 None"


def test_fund_nav_metric_is_pre_existing_dead_code_path():
    """fund_nav 不在 _SUPPORTED_METRICS 白名单里（2026-09-01 任务#8 排查
    发现，pre-existing，与本次改动无关）——`fetch("fund_nav", ...)` 一进
    `fetch()` 就被 `if metric not in _SUPPORTED_METRICS: return None`
    拦截，根本走不到 `_fetch_fund_nav`。代码检索确认没有任何调用方通过
    `fetch("fund_nav", ...)` 触发这条路径（market/stocks.py 里的基金
    净值走的是别的路径 FallbackRunner(metric="fund_nav")，那是
    infra/data_source/fallback.py 自己的 DEFAULT_CHAINS 分发，和
    AkshareProvider.fetch() 是两套独立的 metric 命名空间，不会混淆）。

    这是死代码路径，本次任务#8 仅记录不处理（不在授权范围内，属于
    "该不该把 fund_nav 加入白名单" 的独立产品/架构决策）。"""
    provider = AkshareProvider()
    provider._available = True
    assert "fund_nav" not in __import__(
        "infra.data_source.providers.akshare_provider", fromlist=["_SUPPORTED_METRICS"]
    )._SUPPORTED_METRICS
    result = provider.fetch("fund_nav", symbol="110011")
    assert result is None, "fund_nav 不在白名单，fetch() 应直接返回 None"


# ============================================================
# alt/ths_concepts.py —— 2 处调用点全覆盖
# ============================================================

def test_get_hot_concepts_uses_call_with_timeout():
    with patch(
        "infra.data_source.alt.ths_concepts.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = ths_module.get_hot_concepts(limit=5)
        assert mock_call.called, "get_hot_concepts 必须通过 call_with_timeout 调用"
        assert result == []


def test_get_concept_stocks_dead_interface_still_degrades_gracefully():
    """get_concept_stocks 底层 ak.stock_board_concept_cons_ths 已被
    AKShare 库删除（2026-09-01 任务#8 排查发现，见 ths_concepts.py 对应
    docstring）。同 test_fund_name_fund_rank_dead_interface_still_
    degrades_gracefully 的原理：`ak.stock_board_concept_cons_ths` 作为
    属性访问会在 call_with_timeout 真正被调用之前就抛出 AttributeError，
    所以不能像其他函数一样断言 mock_call.called——这里改为验证"接口已死
    但依然优雅返回 [] 而不是让异常冒泡"这个更有意义的保证。"""
    result = ths_module.get_concept_stocks("数据安全")
    assert result == [], "get_concept_stocks 底层接口已死，应优雅返回 []"


# ============================================================
# fundamental/financials.py —— 3 处调用点全覆盖
# ============================================================

def test_get_financial_indicators_uses_call_with_timeout():
    with patch(
        "infra.data_source.fundamental.financials.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = financials_module.get_financial_indicators("000001")
        assert mock_call.called
        assert result is None


def test_get_stock_lg_indicator_uses_call_with_timeout():
    """get_stock_lg_indicator 已切换到新接口 ak.stock_a_gxl_lg（原
    ak.stock_a_lg_indicator 已被 AKShare 库删除），超时时应返回 None。"""
    with patch(
        "infra.data_source.fundamental.financials.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = financials_module.get_stock_lg_indicator("000300")
        assert mock_call.called
        assert result is None


def test_get_stock_lg_indicator_symbol_mapping():
    """验证 symbol 映射逻辑：旧调用习惯传 "000300"（沪深300指数代码）
    应被映射为新接口的市场分类 "上证A股"；直接传新接口合法值应原样透传；
    传未知值应兜底为 "上证A股"（不能让未知输入直接崩给上游接口）。"""
    calls_seen = []

    def _capture_symbol(func, timeout, **kwargs):
        calls_seen.append(kwargs.get("symbol"))
        return None

    with patch(
        "infra.data_source.fundamental.financials.call_with_timeout",
        side_effect=_capture_symbol,
    ):
        financials_module.get_stock_lg_indicator("000300")
        financials_module.get_stock_lg_indicator("深证A股")
        financials_module.get_stock_lg_indicator("some_unknown_value")

    assert calls_seen == ["上证A股", "深证A股", "上证A股"], (
        f"symbol 映射逻辑不符合预期: {calls_seen}"
    )


def test_get_fund_portfolio_holdings_uses_call_with_timeout():
    with patch(
        "infra.data_source.fundamental.financials.call_with_timeout",
        side_effect=_mock_returns_none,
    ) as mock_call:
        result = financials_module.get_fund_portfolio_holdings("110011")
        assert mock_call.called
        assert result is None


# ============================================================
# 静态扫描：5个文件不应再出现任何裸 ak.xxx() 调用（AST，非文本 grep）
# ============================================================

@pytest.mark.parametrize("module_path", [
    "infra/data_source/macro/indicators.py",
    "infra/data_source/alt/flows.py",
    "infra/data_source/providers/akshare_provider.py",
    "infra/data_source/alt/ths_concepts.py",
    "infra/data_source/fundamental/financials.py",
])
def test_no_bare_ak_calls_remain(module_path):
    """回归锁定：这5个文件不应再出现裸 ak.xxx() 调用（必须经过
    call_with_timeout）。用 AST 解析而非文本 grep，避免文档字符串里
    提到的函数名被误判为真实调用。"""
    import ast

    backend_root = Path(__file__).parent.parent
    full_path = backend_root / module_path
    with open(full_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    bare_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ak"
        ):
            bare_calls.append((node.lineno, node.func.attr))

    assert bare_calls == [], (
        f"{module_path} 中发现裸 ak.xxx() 调用（未经 call_with_timeout）: {bare_calls}"
    )
