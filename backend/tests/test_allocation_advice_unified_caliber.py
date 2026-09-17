"""`/api/allocation-advice` 与 `/api/portfolio/overview` 口径一致的回归守卫
（FIX 2026-09-18「口径分裂」）。

背景：
  旧 `/api/allocation-advice` 用 `calc_unified_networth` 的粗分自己算一套配置占比：
    - 把**全部投资**（股票 + 基金）整体当成 `stock`；
    - `bond` **硬编码 0**；字段名还是 `stock` 而非 `equity`。
  而「持仓页」走 `get_portfolio_overview()`，输出的是四档 equity/bond/cash/gold。
  同一个用户在两个页面看到互相矛盾的答案（例：LeiJiang 在本端点看到
  「股票 100% · 债券 0%」，在持仓页看到 69.4/21.9/8.7/0）。

修复：主路径改为复用 `get_portfolio_overview()["allocation"]`，并把 overview 的
  `equity` 映射为本端点前端消费的 `stock` 键（字段名兼容），额外补 `gold`。
  仅当 overview 拿不到有效配置分母时，才退回旧的 unified_networth 粗分。

本文件守卫：
  A. 本端点的 current.stock/bond/cash 与 overview 的 equity/bond/cash **逐项相等**
     （这是「两端点口径一致」的可执行证据）；
  B. 字段名仍是前端消费的 stock/bond/cash（不破坏 portfolio.js / landing.js）；
  C. overview 无有效分母时，旧的 unified_networth 粗分降级路径仍在；
  D. landing.js 的 `user_id`（snake_case）键名也能吃到统一口径。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import api.portfolio as portfolio_api  # noqa: E402
import services.market_data as market_data  # noqa: E402
import services.portfolio_overview as portfolio_overview  # noqa: E402
import services.stock_monitor as stock_monitor  # noqa: E402
import services.unified_networth as unified_networth  # noqa: E402

TOL = 0.05


# 一 equity 基金 + 一 bond 基金 + 一 money 基金，各 1000 市值（行情桩掉 ⇒ 用成本）
FUNDS = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "costNav": 2.0, "shares": 500.0},   # 1000 equity
    {"code": "217022", "name": "招商产业债券A", "costNav": 1.0, "shares": 1000.0},       # 1000 bond
    {"code": "000198", "name": "天弘余额宝货币", "costNav": 1.0, "shares": 1000.0},       # 1000 money
]
CASH_ASSETS = [{"id": "a1", "type": "cash", "name": "工行活期", "value": 2000.0}]


def _patch(monkeypatch, funds=FUNDS, navs=None, cash_assets=CASH_ASSETS):
    """桩掉 overview 输入 + 本端点的估值/恐贪/市场上下文/增强依赖。"""
    monkeypatch.setattr(portfolio_overview, "unified_load_stock_holdings",
                        lambda user_id="default": [])
    monkeypatch.setattr(portfolio_overview, "unified_load_fund_holdings",
                        lambda user_id="default": [dict(h) for h in funds])
    monkeypatch.setattr(portfolio_overview, "load_cash_assets",
                        lambda user_id="default": [dict(a) for a in cash_assets])

    def _fake_nav(code, *args, **kwargs):
        nav = (navs or {}).get(str(code))
        if nav is None:
            return None
        return {"code": str(code), "nav": str(nav), "date": "test",
                "change": "0", "source": "test"}

    monkeypatch.setattr(market_data, "get_fund_nav", _fake_nav)
    monkeypatch.setattr(stock_monitor, "get_stock_realtime", lambda code: None)

    monkeypatch.setattr(portfolio_api, "get_valuation_percentile",
                        lambda: {"percentile": 50})
    monkeypatch.setattr(portfolio_api, "get_fear_greed_index",
                        lambda: {"score": 50})
    monkeypatch.setattr(portfolio_api, "_build_market_context", lambda: {})
    monkeypatch.setattr(portfolio_api, "enhance_allocation_advice",
                        lambda result, market_ctx=None: result)


# ============================================================
# A + B. 两端点口径一致 + 字段名兼容
# ============================================================


def test_advice_current_equals_overview_allocation(monkeypatch) -> None:
    """核心断言：本端点 current 的三个键与 overview allocation 逐项相等。

    构造（equity 1000 / bond 1000 / money 1000 + 账户现金 2000）：
      overview：equity 20.0 / bond 20.0 / cash 60.0 / gold 0.0（分母 5000）
      本端点：stock 20.0 / bond 20.0 / cash 60.0（stock ← equity）
    """
    _patch(monkeypatch)

    ov = portfolio_overview.get_portfolio_overview("caliber_user")
    res = portfolio_api.get_allocation_advice_api({"userId": "caliber_user"})

    assert res["allocation_source"] == "portfolio_overview"
    assert res["current"]["stock"] == pytest.approx(ov["allocation"]["equity"], abs=TOL)
    assert res["current"]["bond"] == pytest.approx(ov["allocation"]["bond"], abs=TOL)
    assert res["current"]["cash"] == pytest.approx(ov["allocation"]["cash"], abs=TOL)
    assert res["current"]["gold"] == pytest.approx(ov["allocation"]["gold"], abs=TOL)
    # 分母一致
    assert res["total_market"] == pytest.approx(ov["totalForAllocation"], abs=0.01)

    # 具体数字（防两端一起错成同一套坏口径）
    assert res["current"]["stock"] == pytest.approx(20.0, abs=TOL)
    assert res["current"]["bond"] == pytest.approx(20.0, abs=TOL)
    assert res["current"]["cash"] == pytest.approx(60.0, abs=TOL)
    assert res["total_market"] == pytest.approx(5000.0, abs=0.01)


def test_advice_no_longer_hardcodes_bond_zero(monkeypatch) -> None:
    """旧实现 bond 恒为 0、stock 恒为 100。修复后必须有非零 bond 且 stock != 100。"""
    _patch(monkeypatch)
    res = portfolio_api.get_allocation_advice_api({"userId": "caliber_user"})

    assert res["current"]["bond"] > 0, "有债权基金时 bond 不应为 0（旧实现硬编码 0）"
    assert res["current"]["stock"] < 100, "有债/现金时股票不应是 100%（旧实现把全部投资当股票）"


def test_advice_keeps_frontend_field_names(monkeypatch) -> None:
    """前端读 stock/bond/cash，字段名不得改动（否则 portfolio.js/landing.js 破图）。"""
    _patch(monkeypatch)
    res = portfolio_api.get_allocation_advice_api({"userId": "caliber_user"})

    assert set(res["current"]) == {"stock", "bond", "cash", "gold"}
    for bucket in ("target", "deviation"):
        assert {"stock", "bond", "cash"}.issubset(set(res[bucket])), (
            f"{bucket} 缺少前端消费的 stock/bond/cash 键"
        )


def test_advice_and_overview_do_not_contain_old_caliber(monkeypatch) -> None:
    """证伪旧口径：若 current == {stock:100, bond:0} 说明又走回了粗分。"""
    _patch(monkeypatch)
    res = portfolio_api.get_allocation_advice_api({"userId": "caliber_user"})

    old = {"stock": 100.0, "bond": 0.0}
    got = {k: res["current"][k] for k in old}
    assert got != old, f"本端点又给出旧的粗分口径 {old}"


# ============================================================
# C. overview 无有效分母时，降级路径仍在
# ============================================================


def test_fallback_to_unified_networth_when_overview_empty(monkeypatch) -> None:
    """只有房产等非投资资产时：overview 无配置分母 → 退回 unified_networth 粗分。"""
    _patch(monkeypatch, funds=[], navs={}, cash_assets=[])

    fake_nw = {"netWorth": 100000, "breakdown": {
        "investment": {"total": 0},
        "cash": {"total": 100000},
        "liability": {"total": 0},
    }}
    monkeypatch.setattr(unified_networth, "calc_unified_networth",
                        lambda user_id, force=False: fake_nw)

    res = portfolio_api.get_allocation_advice_api({"userId": "manual_assets_user"})

    assert res["allocation_source"] == "unified_networth_fallback"
    assert res["current"]["cash"] == pytest.approx(100.0, abs=TOL)
    assert res["current"]["stock"] == pytest.approx(0.0, abs=TOL)


# ============================================================
# D. landing.js 的 snake_case 键名
# ============================================================


def test_advice_accepts_snake_case_user_id(monkeypatch) -> None:
    """landing.js 用 {user_id} 调用；修复前只认 userId，首页一直走降级路径。"""
    _patch(monkeypatch)
    res = portfolio_api.get_allocation_advice_api({"user_id": "caliber_user"})

    assert res["allocation_source"] == "portfolio_overview", (
        "snake_case user_id 未被识别，首页仍会落到降级路径"
    )
    assert res["current"]["stock"] == pytest.approx(20.0, abs=TOL)
