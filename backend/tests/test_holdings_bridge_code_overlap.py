"""
6 位代码命名空间重叠守卫 —— 002163 双算回归测试。

背景（生产实测，用户 LeiJiang）：
  A 股股票代码与场外基金代码**都是 6 位数字且空间重叠**：
    `002163` 既是深市股票「海南发展」，也是基金「东方惠新灵活配置混合C」。

事故路径：
  1. 独立 `fund_holdings` 文件里有基金 002163（真值 112.25）；
  2. 独立 `stock_holdings` 文件为空 → `unified_load_stock_holdings` 回退到
     `holdings_bridge._holdings_from_transactions_stock`（V4 流水派生）；
  3. 旧代码**只按代码前缀**判类型（`002` → A 股），把这支基金又派生了一份
     **股票**持仓（303.00）；
  4. `portfolio_overview.get_portfolio_overview()` 里
     `total_mv = stock_total_mv + fund_total_mv` → 同一笔算两次，
     `totalMarketValue` 虚高 +303（真值 719.59）。

修复：V4 派生时对照**另一侧的独立持仓**（唯一可信的资产类型来源），
代码已被认领为另一种资产类型时不再派生。判据见
`services/holdings_bridge.py` 顶部注释。

本文件守卫四条：
  A. 双算被消除：基金 002163 只以基金身份出现一次，股票派生为空；
  B. `get_portfolio_overview` 的 totalMarketValue / stockValue / stockCount
     不再含那笔凭空造出来的股票；
  C. 不得过度拦截：V4 里确实是**另一支**股票 002163（海南发展，名称与基金
     不同）时，仍必须正常派生（这是双算的反面 —— 少算，同样错）；
  D. 无独立持仓 / 独立持仓为空时守卫是 no-op，不得改变原行为。

故障注入方向（每个用例下方 `故障注入` 注释给出实测命令与预期）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.fund_monitor as fund_monitor  # noqa: E402
import services.market_data as market_data  # noqa: E402
import services.persistence as persistence  # noqa: E402
import services.portfolio_overview as portfolio_overview  # noqa: E402
import services.stock_monitor as stock_monitor  # noqa: E402
from services.holdings_bridge import (  # noqa: E402
    unified_load_fund_holdings,
    unified_load_stock_holdings,
)

# 事故主角：同一串 6 位数字，两种资产类型
OVERLAP_CODE = "002163"
FUND_NAME = "东方惠新灵活配置混合C"   # 基金（002163.OF）
STOCK_NAME = "海南发展"                # 深市股票（002163.SZ）

# 基金持仓（独立文件）：8000 份 × 净值 4.1558 = 33246.4
FUND_HOLDING = {
    "code": OVERLAP_CODE,
    "name": FUND_NAME,
    "costNav": 2.5940,
    "shares": 8000.0,
}
FUND_NAV = 4.1558

# V4 流水：同一支基金的买入记录（事故里它被前缀启发式误判成 A 股）
FUND_TX = {
    "id": "tx_fund_002163",
    "type": "BUY",
    "code": OVERLAP_CODE,
    "name": FUND_NAME,
    "amount": 20752.0,
    "shares": 8000.0,
    "nav": 2.5940,
    "fee": 0,
    "date": "2026-01-05",
}

# 股票实时价：若 bug 存在，被误派的股票会按这个价计价（1000 股 × 8.30）
STOCK_PRICE = 8.30
STOCK_TX = {
    "id": "tx_stock_002163",
    "type": "BUY",
    "code": OVERLAP_CODE,
    "name": STOCK_NAME,
    "amount": 8300.0,
    "shares": 1000.0,
    "nav": 8.30,
    "fee": 0,
    "date": "2026-02-11",
}


def _patch_stores(monkeypatch, stocks, funds, txs, assets=None) -> None:
    """把两套独立持仓 + V4 流水 + 账户资产全部桩掉（不碰真实 data 目录）。"""
    monkeypatch.setattr(stock_monitor, "load_stock_holdings",
                        lambda uid: list(stocks), raising=True)
    monkeypatch.setattr(fund_monitor, "load_fund_holdings",
                        lambda uid: list(funds), raising=True)
    monkeypatch.setattr(persistence, "load_user",
                        lambda uid, *a, **k: {
                            "portfolio": {
                                "transactions": list(txs),
                                "assets": list(assets or []),
                            }
                        },
                        raising=True)
    # 账户现金：桩成空，避免用例去读真实 data 目录（portfolio_overview 在 import
    # 期就把 load_cash_assets 绑进了自己的命名空间，故 patch 该模块属性）。
    monkeypatch.setattr(portfolio_overview, "load_cash_assets",
                        lambda uid: [], raising=True)


def _patch_quotes(monkeypatch) -> None:
    """桩掉行情：股票给实时价、基金给净值（都可被故障注入改坏）。"""
    monkeypatch.setattr(stock_monitor, "get_stock_realtime",
                        lambda code: {"price": STOCK_PRICE}, raising=True)
    monkeypatch.setattr(market_data, "get_fund_nav",
                        lambda code: {"code": code, "nav": str(FUND_NAV),
                                      "date": "2026-09-18", "change": "0"},
                        raising=True)


# ============================================================
# A. 双算被消除
# ============================================================

def test_overlap_code_fund_not_derived_as_stock(monkeypatch) -> None:
    """A: 独立基金持仓已有 002163 → V4 派生**不得**再产出同名股票持仓。

    故障注入：把 `holdings_bridge._claimed_by_other_kind` 桩成恒 False
    （等价于回退到只按前缀判类型的旧行为）→ 本用例必须变红：
        monkeypatch.setattr(holdings_bridge, "_claimed_by_other_kind",
                            lambda *a, **k: False)
    """
    # 独立股票持仓为空（触发 V4 回退），独立基金持仓有 002163
    _patch_stores(monkeypatch, stocks=[], funds=[FUND_HOLDING], txs=[FUND_TX])

    derived = unified_load_stock_holdings("u_overlap")
    codes = [h.get("code") for h in derived]

    assert OVERLAP_CODE not in codes, (
        f"002163 已在独立基金持仓里，不得再被 V4 派生为股票（双算）。实际派生: {codes}"
    )
    assert derived == []


def test_overlap_code_not_double_counted_in_overview(monkeypatch) -> None:
    """B: 总览里 002163 只算一次（基金口径），股票侧不得凭空多出一笔。

    故障注入：同上，把 `_claimed_by_other_kind` 桩成恒 False →
    stockValue 会从 0 变成 8000×8.30=66400，totalMarketValue 同步虚高，
    本用例变红。
    """
    _patch_stores(monkeypatch, stocks=[], funds=[FUND_HOLDING], txs=[FUND_TX])
    _patch_quotes(monkeypatch)

    out = portfolio_overview.get_portfolio_overview("u_overlap")

    expected_fund_mv = FUND_NAV * FUND_HOLDING["shares"]  # 33246.4
    assert out["stockValue"] == pytest.approx(0.0), (
        f"股票侧不应出现 002163（它是基金）。stockValue={out['stockValue']}"
    )
    assert out["stockCount"] == 0
    assert out["fundValue"] == pytest.approx(expected_fund_mv)
    assert out["totalMarketValue"] == pytest.approx(expected_fund_mv), (
        f"总市值应只含一次基金市值 {expected_fund_mv}，实得 {out['totalMarketValue']}"
    )


def test_overlap_code_fund_side_not_derived_as_stock_either(monkeypatch) -> None:
    """A-2: 反向场景 —— 独立股票持仓已有 AAPL，V4 派生不得产出同名**基金**。

    ⚠️ 本用例刻意用**非 6 位数字**代码 AAPL（不是 002163）：
    前缀启发式对 "AAPL" / "00700" 这类代码一律判「非 A 股 → 基金」，
    于是在「独立股票持仓已持有它 + 基金文件为空」时会被再派生一份基金（双算）。
    用 AAPL 钉死这条分支，避免守卫只覆盖 6 位数字。

    6 位数字代码的真实重叠场景（002163 = 深市股票「海南发展」vs 基金
    「东方惠新灵活配置混合C」）由另外三条覆盖，本条不重复：
      - A  test_overlap_code_fund_not_derived_as_stock      （基金侧已认领 → 股票侧不派生）
      - B  test_overlap_code_not_double_counted_in_overview（总览只算一次）
      - C  test_same_code_different_instrument_still_derived（两只确为不同标的 → 正常派生）

    故障注入：把 `holdings_bridge._claimed_by_other_kind` 桩成恒 False →
    AAPL 会被再派生为基金，本用例变红。
    """
    _patch_stores(
        monkeypatch,
        stocks=[{"code": "AAPL", "name": "Apple Inc", "costPrice": 100.0, "shares": 10}],
        funds=[],  # 基金文件为空 → 触发 fund 侧 V4 回退
        txs=[{"id": "t1", "type": "BUY", "code": "AAPL", "name": "Apple Inc",
              "amount": 1000.0, "shares": 10, "nav": 100.0, "fee": 0,
              "date": "2026-03-01"}],
    )

    derived = unified_load_fund_holdings("u_overlap_rev")
    codes = [h.get("code") for h in derived]

    assert "AAPL" not in codes, (
        f"AAPL 已在独立股票持仓里，不得再被 V4 派生为基金（双算）。实际派生: {codes}"
    )


# ============================================================
# C. 不得过度拦截（双算的反面：按 code 误并导致少算）
# ============================================================

def test_same_code_different_instrument_still_derived(monkeypatch) -> None:
    """C: 真持有两只 002163（股票 海南发展 + 基金 东方惠新）→ 股票侧必须保留。

    这是同一根因的另一面：只按 code 去重会把两个不同标的并成一个（少算）。
    守卫靠**名称互不包含**区分，故股票 002163 仍要正常派生。

    故障注入：把 `holdings_bridge._looks_like_same_instrument` 桩成恒 True
    （等价于「只看代码」的粗暴去重）→ 股票 002163 被吞掉，本用例变红：
        monkeypatch.setattr(holdings_bridge, "_looks_like_same_instrument",
                            lambda *a, **k: True)
    """
    _patch_stores(monkeypatch, stocks=[], funds=[FUND_HOLDING], txs=[STOCK_TX])

    derived = unified_load_stock_holdings("u_both")
    codes = [h.get("code") for h in derived]

    assert OVERLAP_CODE in codes, (
        "V4 里的 002163 名称是「海南发展」（股票），与基金「东方惠新…」不是同一支，"
        f"必须正常派生。实际派生: {codes}"
    )
    assert derived[0]["name"] == STOCK_NAME
    assert derived[0]["shares"] == 1000.0


def test_empty_name_treated_as_same_instrument(monkeypatch) -> None:
    """C-2: V4 记录缺 name 时无从证伪 → 按同一支处理，不派生（宁可不虚增）。

    故障注入：把 `_looks_like_same_instrument` 桩成恒 False（只看代码也不是、
    缺名就算不同）→ 会重新派生出 phantom 股票，本用例变红。
    """
    nameless_tx = dict(FUND_TX)
    nameless_tx["name"] = ""
    _patch_stores(monkeypatch, stocks=[], funds=[FUND_HOLDING], txs=[nameless_tx])

    assert unified_load_stock_holdings("u_nameless") == []


# ============================================================
# D. 守卫在无独立持仓时必须是 no-op
# ============================================================

def test_guard_is_noop_when_other_store_empty(monkeypatch) -> None:
    """D: 另一侧独立持仓为空（无权威类型信息）→ 行为与修复前一致，照常派生。

    钉死「守卫不是一刀切地屏蔽 002xxx」：纯 V4 用户（两套独立文件都没有）
    的股票持仓必须照旧出现在总览里。

    故障注入：把 `_known_instrument_names` 桩成恒 `{"002163": {""}}`
    （伪造一条权威基金持仓）→ 002163 被误屏蔽，本用例变红。
    """
    _patch_stores(monkeypatch, stocks=[], funds=[], txs=[STOCK_TX])
    _patch_quotes(monkeypatch)

    derived = unified_load_stock_holdings("u_v4only")
    assert [h["code"] for h in derived] == [OVERLAP_CODE]

    out = portfolio_overview.get_portfolio_overview("u_v4only")
    assert out["stockValue"] == pytest.approx(STOCK_PRICE * 1000.0)
    assert out["stockCount"] == 1


def test_fund_side_guard_noop_for_real_fund_code(monkeypatch) -> None:
    """D-2: 真正的基金代码（519xxx）在无人认领时仍照常从 V4 派生为基金。

    故障注入：同上伪造 `_known_instrument_names` 返回含 519736 的索引 →
    基金被吞掉，本用例变红。
    """
    fund_tx = {
        "id": "t_fund", "type": "BUY", "code": "519736",
        "name": "交银新成长混合", "amount": 5000.0, "shares": 1000.0,
        "nav": 5.0, "fee": 0, "date": "2026-04-01",
    }
    _patch_stores(monkeypatch, stocks=[], funds=[], txs=[fund_tx])

    derived = unified_load_fund_holdings("u_fund_only")
    assert [h["code"] for h in derived] == ["519736"]
    assert derived[0]["name"] == "交银新成长混合"
