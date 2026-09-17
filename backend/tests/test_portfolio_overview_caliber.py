"""
配置口径一致性守卫：基金分配基数必须是**市值**口径（FIX 2026-09-18）。

背景（原 TODO 在 portfolio_overview.py:128-131）：
  `get_portfolio_overview()` 曾把两种口径加进同一个分母：
    - 股票：**市值**口径 —— 实时价 × 股数，取不到实时价才退回成本价；
    - 基金分类分配：**成本**口径 —— fund_classifier.classify_and_allocate
      内部基数 `total_cost = nav_cost * shares`。
  后果有两层：
    1. 资产涨了，基金那部分仍按买入成本计价，配置占比被系统性低估
       （浮盈越大低估越多）；
    2. 同一份 overview 里 `totalMarketValue` 是市值，而配置分母是成本，
       两个口径自相矛盾。

修复：
  给 `classify_and_allocate` 增加**可选**参数 `nav_current`（当前净值，元/份），
  显式传入正数时基数切到市值口径 `nav_current * shares`；不传 / 非正数时
  仍为成本口径（完全保留旧语义，既有调用点与断言不受影响）。
  `portfolio_overview` 把上方已经算好的 `current_nav` 传进去（**不新增行情请求**）。

本文件守卫三条不变量：
  A. 市值口径下分配金额随净值变化，且给出具体断言数字（比例也跟着变）；
  B. 实时净值取不到时 `portfolio_overview` 传入的 `current_nav == nav_cost`，
     此时新旧口径结果**数值等价** —— 防止"改口径顺手改坏了退化路径"；
  C. `get_portfolio_overview` 的配置分母与市值口径自洽：
     基金四桶之和 ≈ 基金市值之和（不再恒等于基金成本之和）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.market_data as market_data  # noqa: E402
import services.portfolio_overview as portfolio_overview  # noqa: E402
from services.fund_classifier import classify_and_allocate  # noqa: E402

# 命中 KNOWN_FUND_TYPES 短路的四个纯类型（code → 期望桶）
PURE_EQUITY = ("510300", "华泰柏瑞沪深300ETF")
PURE_BOND = ("217022", "")
PURE_MONEY = ("000198", "")
PURE_GOLD = ("000216", "华泰柏瑞黄金ETF")
# 不在表内、靠名称推断为 mixed 的持仓（code 002163 实测不在 KNOWN_FUND_TYPES）
MIXED_FUND = ("002163", "东方惠新灵活配置混合C")


# ============================================================
# 0. 未传 nav_current 时语义不变（向后兼容基线）
# ============================================================


def test_default_caliber_is_cost() -> None:
    """不传 nav_current → basis == "cost"，分配额 == 成本额（旧语义原样）。"""
    result = classify_and_allocate(
        code=PURE_EQUITY[0], name=PURE_EQUITY[1], nav_cost=2.0, shares=100
    )
    assert result["basis"] == "cost"
    assert result["navCurrent"] is None
    assert result["totalCost"] == pytest.approx(200.0)
    assert result["totalValue"] == pytest.approx(200.0)
    assert result["equity"] == pytest.approx(200.0)


@pytest.mark.parametrize("bad_nav", [None, 0, 0.0, -1.5])
def test_non_positive_or_missing_nav_current_degrades_to_cost(bad_nav) -> None:
    """nav_current 为 None / 0 / 负数 → 一律退化成本口径（非法输入不得静默放大金额）。"""
    result = classify_and_allocate(
        code=PURE_EQUITY[0],
        name=PURE_EQUITY[1],
        nav_cost=2.0,
        shares=100,
        nav_current=bad_nav,
    )
    assert result["basis"] == "cost"
    assert result["totalValue"] == pytest.approx(200.0)
    assert result["equity"] == pytest.approx(200.0)


def test_zero_shares_yields_zero_buckets_either_caliber() -> None:
    """shares == 0 时两种口径都必须是 0，不允许凭空生出金额。"""
    for nav_current in (None, 3.0):
        result = classify_and_allocate(
            code=PURE_EQUITY[0],
            name=PURE_EQUITY[1],
            nav_cost=2.0,
            shares=0,
            nav_current=nav_current,
        )
        assert result["basis"] == "cost", "shares<=0 时必须走成本口径（金额为 0）"
        assert result["equity"] == 0
        assert result["totalValue"] == 0


# ============================================================
# A. 分配随净值涨跌而变（具体数字）
# ============================================================


def test_market_caliber_scales_pure_equity_fund_with_nav() -> None:
    """510300 整笔进 equity 桶：基数随净值同比例缩放。"""
    cost = classify_and_allocate(
        code=PURE_EQUITY[0], name=PURE_EQUITY[1], nav_cost=1.0, shares=1000
    )
    assert cost["equity"] == pytest.approx(1000.0)

    # 净值 +20%
    up = classify_and_allocate(
        code=PURE_EQUITY[0], name=PURE_EQUITY[1], nav_cost=1.0, shares=1000,
        nav_current=1.2,
    )
    assert up["basis"] == "market"
    assert up["totalCost"] == pytest.approx(1000.0), "成本口径金额不因市值口径而改变"
    assert up["totalValue"] == pytest.approx(1200.0)
    assert up["equity"] == pytest.approx(1200.0)

    # 净值 -50%
    down = classify_and_allocate(
        code=PURE_EQUITY[0], name=PURE_EQUITY[1], nav_cost=1.0, shares=1000,
        nav_current=0.5,
    )
    assert down["totalValue"] == pytest.approx(500.0)
    assert down["equity"] == pytest.approx(500.0)


def test_market_caliber_shifts_mixed_fund_buckets() -> None:
    """
    混合基金（名称含「灵活」→ equity/bond/money = 0.60/0.30/0.10）：
    净值变化会让**三桶金额同时按比例变**，比例不变但金额变。

    这条同时锁住 mixed 分支用的也是 total_value 而不是残留的 total_cost。
    """
    cost = classify_and_allocate(
        code=MIXED_FUND[0], name=MIXED_FUND[1], nav_cost=1.0, shares=1000
    )
    assert cost["type"] == "mixed", "002163 应靠名称推断为 mixed"
    assert (cost["equity"], cost["bond"], cost["money"]) == (600.0, 300.0, 100.0)
    assert cost["gold"] == 0

    up = classify_and_allocate(
        code=MIXED_FUND[0], name=MIXED_FUND[1], nav_cost=1.0, shares=1000,
        nav_current=1.2,
    )
    assert (up["equity"], up["bond"], up["money"]) == (720.0, 360.0, 120.0)
    assert sum(up[b] for b in ("equity", "bond", "money", "gold")) == pytest.approx(
        up["totalValue"]
    ), "四桶之和必须等于参与分配的基数"


# ============================================================
# B. 退化路径等价（实时净值取不到）
# ============================================================


@pytest.mark.parametrize(
    "code,name",
    [PURE_EQUITY, PURE_BOND, PURE_MONEY, PURE_GOLD, MIXED_FUND],
)
def test_fallback_nav_equal_to_cost_is_equivalent(code: str, name: str) -> None:
    """
    实时净值取不到时，portfolio_overview 传入的 current_nav 就等于 cost_nav
    （:86 初始化 + :92 条件赋值）。此时新旧口径必须**数值等价**。

    这是本次改动最容易踩坏的地方：如果有人把退化分支写成
    `nav_current or nav_cost` 之外的其它判定，或让 market 分支多做一次
    四舍五入，这条会立刻变红。
    """
    nav_cost = 2.419549963706751
    shares = 41.33

    legacy = classify_and_allocate(code=code, name=name, nav_cost=nav_cost, shares=shares)
    fallback = classify_and_allocate(
        code=code, name=name, nav_cost=nav_cost, shares=shares, nav_current=nav_cost
    )

    for bucket in ("equity", "bond", "money", "gold"):
        assert fallback[bucket] == legacy[bucket], (
            f"{code} 退化路径下 {bucket} 桶不等价："
            f"legacy={legacy[bucket]} fallback={fallback[bucket]}"
        )
    assert fallback["totalValue"] == legacy["totalValue"]
    assert fallback["totalCost"] == legacy["totalCost"]
    # 判据字段不同（一个显式走了市值分支），但金额必须一样
    assert fallback["basis"] == "market"


# ============================================================
# C. get_portfolio_overview 的整体口径自洽
# ============================================================


def _patch_overview_inputs(monkeypatch, funds, navs):
    """把 overview 的两个持仓加载器与行情源替换成固定输入（不触真实数据/网络）。"""
    monkeypatch.setattr(
        portfolio_overview, "unified_load_stock_holdings",
        lambda user_id="default": [],
    )
    monkeypatch.setattr(
        portfolio_overview, "unified_load_fund_holdings",
        lambda user_id="default": list(funds),
    )

    def _fake_get_fund_nav(code, *args, **kwargs):
        nav = navs.get(str(code))
        if nav is None:
            # 模拟行情取不到：真实路径下 portfolio_overview 会 fallback 到 cost_nav
            return None
        return {"code": str(code), "nav": str(nav), "date": "test",
                "change": "0", "source": "test"}

    monkeypatch.setattr(market_data, "get_fund_nav", _fake_get_fund_nav)


# 两只基金：成本各 200，合计成本 400；市值 300 + 140 = 440（非等比，能暴露口径差异）
_PROBE_FUNDS = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "costNav": 2.0, "shares": 100},
    {"code": "002163", "name": "东方惠新灵活配置混合C", "costNav": 2.0, "shares": 100},
]
_PROBE_NAVS = {"510300": 3.0, "002163": 1.4}


def _capture_buckets(monkeypatch):
    """包裹生产路径上的 classify_and_allocate，累计四桶真值（不另写一套分配逻辑）。"""
    captured = {"equity": 0.0, "bond": 0.0, "money": 0.0, "gold": 0.0}
    real = portfolio_overview.classify_and_allocate

    def _wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        for key in captured:
            captured[key] += result.get(key, 0.0)
        return result

    monkeypatch.setattr(portfolio_overview, "classify_and_allocate", _wrapped)
    return captured


def test_overview_denominator_equals_fund_market_value(monkeypatch) -> None:
    """
    有实时净值时：配置分母必须跟着市值走。

    手算（成本各 200，净值 3.0 / 1.4）：
      510300（equity）：市值 300 → equity 桶 300
      002163（mixed 0.6/0.3/0.1）：市值 140 → equity 84 / bond 42 / money 14
      四桶和 = 300 + 84 + 42 + 14 = 440 = 基金市值和
      占比 = 384/440 = 87.3% / 42/440 = 9.5% / 14/440 = 3.2% / gold 0%
    """
    _patch_overview_inputs(monkeypatch, _PROBE_FUNDS, _PROBE_NAVS)
    captured = _capture_buckets(monkeypatch)

    ov = portfolio_overview.get_portfolio_overview("caliber_probe")

    assert ov["fundCount"] == 2
    assert ov["stockCount"] == 0
    assert ov["fundValue"] == pytest.approx(440.0)
    assert ov["totalMarketValue"] == pytest.approx(440.0)
    assert ov["totalCost"] == pytest.approx(400.0), "成本口径字段应保持成本值不变"

    # 核心不变量：配置分母 == 基金市值之和（而非成本之和 400）
    bucket_sum = sum(captured.values())
    assert bucket_sum == pytest.approx(ov["totalMarketValue"], abs=0.01 * ov["fundCount"])
    assert bucket_sum != pytest.approx(ov["totalCost"], abs=0.01), (
        "分母若仍等于成本 400，说明市值口径没生效"
    )

    assert ov["allocation"] == {
        "equity": 87.3, "bond": 9.5, "cash": 3.2, "gold": 0.0,
    }


def test_overview_fallback_path_matches_legacy_caliber(monkeypatch) -> None:
    """
    行情全部取不到 → 全部退化成本口径：分母 == 成本 == 市值，占比回到旧值。

    同一持仓在"取不到行情"下必须是 (80.0 / 15.0 / 5.0 / 0.0)，
    这正是改动前版本在**任何**情况下的输出 —— 也就是说退化时行为连续。
    """
    _patch_overview_inputs(monkeypatch, _PROBE_FUNDS, {})
    captured = _capture_buckets(monkeypatch)

    ov = portfolio_overview.get_portfolio_overview("caliber_probe_fallback")

    assert ov["fundValue"] == pytest.approx(400.0)
    assert ov["totalMarketValue"] == pytest.approx(400.0)
    assert ov["totalCost"] == pytest.approx(400.0)
    assert sum(captured.values()) == pytest.approx(400.0)
    assert ov["allocation"] == {
        "equity": 80.0, "bond": 15.0, "cash": 5.0, "gold": 0.0,
    }
