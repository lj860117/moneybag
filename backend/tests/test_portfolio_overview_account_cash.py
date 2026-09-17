"""账户现金计入现金桶与配置分母的回归守卫（FIX 2026-09-18「现金漏计」）。

背景：
  `get_portfolio_overview()` 的配置分母里，`cash` 桶长期来自 `cash = fund_money`
  —— 只统计**基金持仓内部**的货币类份额。账户里真正的现金余额
  （`user.portfolio.assets[]` 中 `type == "cash"`）完全没进 allocation，
  于是现金被系统性低估、误报「现金欠配」并给出错误的增持建议。

修复：
  `cash = fund_money + account_cash`，且 `total_for_alloc` **同步** +
  account_cash（只加分子不加分母 = 又一次口径分裂，本项目刚踩过）。

本文件守卫四条不变量：
  A. 账户现金并入 cash 桶，**且**分母同步增加：
       total_for_alloc == totalMarketValue + accountCash
       ⇒ 占比按「投资市值 + 账户现金」重算；
  B. 账户现金与基金内货币份额**按基金代码去重**，不双算；
  C. 名称命中的通用现金名（< 3 字的「现金」等）**不得**被误去重（防吞掉真实存款）；
  D. `value` / `balance` 两种金额字段都认（旧 OCR 数据只写 balance）。

所有用例都桩掉真实行情与数据加载，结果完全确定，不依赖网络/真实数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.market_data as market_data  # noqa: E402
import services.portfolio_overview as portfolio_overview  # noqa: E402
import services.stock_monitor as stock_monitor  # noqa: E402
import services.unified_networth as unified_networth  # noqa: E402

TOL = 0.05


# 一只纯货币基金（000198 → KNOWN_FUND_TYPES = "money"）：市值 1000
MONEY_FUND = {"code": "000198", "name": "天弘余额宝货币", "costNav": 1.0, "shares": 1000.0}


def _patch(monkeypatch, funds, navs, cash_assets):
    """桩掉持仓加载器 / 行情 / 账户现金加载器。

    ⚠️ 账户现金的桩**忠实复刻** `unified_networth.load_cash_assets` 的契约：
    只返回 `type == "cash"` 的项（真实 loader 就是这么过滤的）。若不这样做，
    桩就会把房产/负债也塞进现金桶，测出一个生产上不存在的假象。
    """
    monkeypatch.setattr(portfolio_overview, "unified_load_stock_holdings",
                        lambda user_id="default": [])
    monkeypatch.setattr(portfolio_overview, "unified_load_fund_holdings",
                        lambda user_id="default": [dict(h) for h in funds])
    monkeypatch.setattr(
        portfolio_overview, "load_cash_assets",
        lambda user_id="default": [dict(a) for a in cash_assets if a.get("type") == "cash"],
    )

    def _fake_nav(code, *args, **kwargs):
        nav = navs.get(str(code))
        if nav is None:
            return None
        return {"code": str(code), "nav": str(nav), "date": "test",
                "change": "0", "source": "test"}

    monkeypatch.setattr(market_data, "get_fund_nav", _fake_nav)
    # 货币基金如果被当成股票路径取价，这里兜底为 None（本用例无股票持仓）
    monkeypatch.setattr(stock_monitor, "get_stock_realtime", lambda code: None)


# ============================================================
# A. 账户现金并入 cash 桶，且分母同步增加
# ============================================================


def test_bank_deposit_joins_cash_bucket_and_denominator(monkeypatch) -> None:
    """工行活期 ¥5000：cash 桶 = 1000(货基) + 5000，分母 = 1000 + 5000 = 6000。

    修复前：cash 桶只有 1000（基金内货币份额），现金占比 100%、
    分母 1000；账户里的 5000 根本没参与配置 —— 这就是「现金漏计」。
    """
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a1", "type": "cash", "name": "工行活期", "value": 5000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_probe")

    assert ov["totalMarketValue"] == pytest.approx(1000.0), "totalMarketValue 只含投资市值"
    assert ov["accountCash"] == pytest.approx(5000.0)
    assert ov["totalForAllocation"] == pytest.approx(6000.0)
    # 不变量：分母 == 投资市值 + 账户现金
    assert ov["totalForAllocation"] == pytest.approx(
        ov["totalMarketValue"] + ov["accountCash"], abs=0.01
    )
    assert ov["allocation"] == {"equity": 0.0, "bond": 0.0, "cash": 100.0, "gold": 0.0}

    # cash 桶金额 = 分母（100%）
    cash_amount = ov["allocation"]["cash"] / 100 * ov["totalForAllocation"]
    assert cash_amount == pytest.approx(6000.0, abs=0.5)


def test_account_cash_dilutes_equity_percentage(monkeypatch) -> None:
    """账户现金让分母变大 → 权益占比被稀释（修复前账户现金不进分母，权益被高估）。

    纯股票型基金（510300 全额 equity）市值 1000：
      修复前：分母 1000 → equity 100%
      修复后：账户现金 9000 进分母 → 分母 10000 → equity 10%
    """
    _patch(monkeypatch, [{"code": "510300", "name": "华泰柏瑞沪深300ETF",
                          "costNav": 2.0, "shares": 500.0}], {}, [
        {"id": "a1", "type": "cash", "name": "招行活期", "value": 9000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_dilute")

    assert ov["totalMarketValue"] == pytest.approx(1000.0)
    assert ov["totalForAllocation"] == pytest.approx(10000.0)
    assert ov["allocation"]["equity"] == pytest.approx(10.0, abs=TOL)
    assert ov["allocation"]["cash"] == pytest.approx(90.0, abs=TOL)


# ============================================================
# B. 按基金代码去重（不双算）
# ============================================================


def test_account_cash_deduped_by_fund_code(monkeypatch) -> None:
    """账户现金带 code 且命中基金持仓 → 视为重复，不计入（防止双算）。

    000198（货币基金）市值 1000 已在 fund_money 里；若再录一笔 code=000198
    的账户现金 1000，修复后应被去重，accountCash=0、分母仍 1000。
    """
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a1", "type": "cash", "name": "余额宝", "code": "000198", "value": 1000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_dedup_code")

    assert ov["accountCash"] == pytest.approx(0.0), "同代码的账户现金应被去重"
    assert ov["accountCashDeduped"] == pytest.approx(1000.0)
    assert ov["totalForAllocation"] == pytest.approx(1000.0), "去重后分母不应被放大"
    assert ov["allocation"]["cash"] == pytest.approx(100.0, abs=TOL)


def test_account_cash_deduped_by_name_containment(monkeypatch) -> None:
    """资产名「余额宝」⊂ 基金名「天弘余额宝货币」→ 去重（真实且常见的双录场景）。"""
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a1", "type": "cash", "name": "余额宝", "value": 1000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_dedup_name")

    assert ov["accountCash"] == pytest.approx(0.0)
    assert ov["accountCashDeduped"] == pytest.approx(1000.0)
    assert ov["totalForAllocation"] == pytest.approx(1000.0)


def test_non_matching_cash_is_not_deduped(monkeypatch) -> None:
    """与货币基金无关的现金（工行活期）不得被去重。"""
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a1", "type": "cash", "name": "工行活期", "value": 5000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_nomatch")

    assert ov["accountCash"] == pytest.approx(5000.0)
    assert ov["accountCashDeduped"] == pytest.approx(0.0)


# ============================================================
# C. 通用短现金名不得被误去重（防吞掉真实存款）
# ============================================================


def test_generic_two_char_cash_name_not_deduped(monkeypatch) -> None:
    """资产名「现金」(2 字) 与「华夏现金增利货币」(含「现金」) 不得误判为重复。

    这是名称启发式最危险的假阳性：若不加 3 字下限，containment 会把一笔
    真实存款整笔吞掉。
    """
    money_fund_with_cash_in_name = {
        "code": "000999", "name": "华夏现金增利货币A", "costNav": 1.0, "shares": 1000.0,
    }
    _patch(monkeypatch, [money_fund_with_cash_in_name], {}, [
        {"id": "a1", "type": "cash", "name": "现金", "value": 8000.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_generic")

    assert ov["accountCash"] == pytest.approx(8000.0), "通用现金名不得被误去重"
    assert ov["accountCashDeduped"] == pytest.approx(0.0)
    assert ov["totalForAllocation"] == pytest.approx(9000.0)


# ============================================================
# D. value / balance 双字段兼容
# ============================================================


def test_balance_only_asset_is_counted(monkeypatch) -> None:
    """旧 OCR 数据只写 balance（无 value）也必须被计入账户现金。"""
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a_ocr_1", "type": "cash", "name": "建行卡", "balance": 2500.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_balance_only")

    assert ov["accountCash"] == pytest.approx(2500.0)
    assert ov["totalForAllocation"] == pytest.approx(3500.0)


def test_value_takes_precedence_over_stale_balance(monkeypatch) -> None:
    """同时有 value 与 balance 时以 value 为准（与 unified_networth 口径一致）。"""
    _patch(monkeypatch, [MONEY_FUND], {}, [
        {"id": "a1", "type": "cash", "name": "工行活期", "value": 3000.0, "balance": 123.0},
    ])

    ov = portfolio_overview.get_portfolio_overview("acct_cash_value_wins")

    assert ov["accountCash"] == pytest.approx(3000.0)


# ============================================================
# 非现金资产不得进入现金桶
# ============================================================


def test_load_cash_assets_filters_type_cash(monkeypatch) -> None:
    """真实的 `load_cash_assets` 只返回 type == "cash"，房产/负债被过滤掉。

    金额口径也要一致：value 优先、balance 回落，都由 asset_amount 统一。
    """
    fake_user = {"portfolio": {"assets": [
        {"id": "c1", "type": "cash", "name": "工行活期", "value": 5000.0},
        {"id": "p1", "type": "property", "name": "房产", "value": 3_000_000.0},
        {"id": "l1", "type": "liability", "name": "房贷", "value": 1_200_000.0},
        {"id": "c2", "type": "cash", "name": "余额宝", "balance": 800.0},
    ]}}
    monkeypatch.setattr(unified_networth, "_load_user_data", lambda user_id: fake_user)

    assets = unified_networth.load_cash_assets("u")
    assert [a["id"] for a in assets] == ["c1", "c2"], "只应有现金类资产"
    assert unified_networth.asset_amount(assets[0]) == pytest.approx(5000.0)
    assert unified_networth.asset_amount(assets[1]) == pytest.approx(800.0), "balance 回落"


def test_end_to_end_property_and_liability_excluded(monkeypatch) -> None:
    """端到端：overview 走**真实** load_cash_assets，房产/负债不得进现金桶与分母。

    只有现金 5000 进桶 → 货基 1000 + 现金 5000 = 6000；房产 300 万、房贷 120 万
    既不进现金桶也不进配置分母。
    """
    _patch(monkeypatch, [MONEY_FUND], {}, [])
    # 让 overview 使用真实的 load_cash_assets（只把底层用户数据读取桩掉）
    monkeypatch.setattr(portfolio_overview, "load_cash_assets",
                        unified_networth.load_cash_assets)
    monkeypatch.setattr(unified_networth, "_load_user_data", lambda user_id: {
        "portfolio": {"assets": [
            {"id": "c1", "type": "cash", "name": "工行活期", "value": 5000.0},
            {"id": "p1", "type": "property", "name": "房产", "value": 3_000_000.0},
            {"id": "l1", "type": "liability", "name": "房贷", "value": 1_200_000.0},
        ]}
    })

    ov = portfolio_overview.get_portfolio_overview("acct_cash_e2e")

    assert ov["accountCash"] == pytest.approx(5000.0)
    assert ov["totalForAllocation"] == pytest.approx(6000.0)
    assert ov["totalMarketValue"] == pytest.approx(1000.0)
