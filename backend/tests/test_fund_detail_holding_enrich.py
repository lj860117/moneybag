"""
fund_detail.py 持仓增强回归测试（v9.9.40 换源 + 字段名对齐）
==========================================================
背景：`_enrich_detail_with_holding` 一直读错持仓源 —— 它读
`load_user(uid).portfolio.holdings`，而这个字段对真实用户**恒为空**
（实测 LeiJiang 8 条真实持仓在 `data/fund_holdings_LeiJiang.json`，
但 `load_user('LeiJiang')['portfolio']['holdings']` 长度为 0）。
全仓 15+ 处都用规范读法 `services.fund_monitor.load_fund_holdings(uid)`
（api/holdings.py:726、api/shared_helpers.py:412/:840、services/weekly_report.py:34、
services/unified_networth.py:104、services/fund_rank.py:103、services/ds_enhance.py:765 等），
只有本处用错源 → 对真实用户永远在 `if not holding: return detail` 早退，
详情弹窗的持仓块（pages/_components.js:454/:461-468）恒不显示。

同时字段名也对不上真实 schema：`load_fund_holdings` 记录真实形状是
`{"code","name","costNav","shares","note","addedAt"}`，旧代码读的是
`cost_nav` / `buyDate` / `amount`（后者记录里根本没有）。

本文件测什么：
  ① mock load_fund_holdings 返回含该 code 的记录 → holding_relation 正确、
     my_holding.shares 正确、my_holding.avg_cost 存在且 == costNav、
     pnl_pct 等于按 (nav-costNav)/costNav*100 手算的值
  ② 返回不含该 code → 不改写 detail（无 holding_relation）
  ③ 反例钉死：load_user(...).portfolio.holdings 有数据但 load_fund_holdings
     为空 → 不应产生 holding_relation（证明真的换源了，不是两源并用）
  ④ costNav 为 0 时 my_holding 仍带数字型 avg_cost（防前端 .toFixed 崩）
  ⑤ 记录缺 shares/costNav 时，下发的 my_holding 也必须是数字型
  ⑥ 结构守门：增强函数内不得再出现 load_user 读取持仓的来源

离线：所有 tushare/akshare/网络调用均被 mock（参考 test_fund_detail_industry_tag.py）。
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKEND_DIR = Path(__file__).parent.parent

# 生产上真实持有的基金（也在 fixtures/fund_holdings_leijiang.json 里）
CODE = "006555"
NAME = "浦银全球智能科技股票(QDII)A"


@pytest.fixture
def fd(tmp_path, monkeypatch):
    """每个测试用独立 DATA_DIR + 重新加载 config/api.fund_detail，保证互不污染。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    import api.fund_detail as fd
    importlib.reload(fd)
    yield fd


def _install_offline_mocks(monkeypatch, fd, name_lookup, nav=1.234):
    """把 fund_detail() 会碰到的一切网络来源都替换成安全的离线桩。

    name_lookup: code -> 基金名；nav: 该基金最新净值（决定 pnl 计算）。
    """
    import services.tushare_data as tushare_data
    monkeypatch.setattr(tushare_data, "get_fund_manager", lambda code: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_portfolio", lambda code: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_share", lambda ts_code, days=10: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_extra_info_ak", lambda code: {})
    monkeypatch.setattr(tushare_data, "is_configured", lambda: False)
    monkeypatch.setattr(tushare_data, "get_fund_nav", lambda *a, **k: {})
    monkeypatch.setattr(tushare_data, "_call_tushare", lambda *a, **k: [])

    import services.fund_rank as fund_rank
    monkeypatch.setattr(
        fund_rank, "get_fund_dynamic_info",
        lambda code: {"code": code, "name": name_lookup.get(code, code),
                      "nav": nav, "returns": {}, "fee": ""},
    )
    monkeypatch.setattr(fund_rank, "_load_fund_rank_data", lambda *a, **k: None)

    try:
        import services.fund_risk_adjusted as fra
        monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", lambda *a, **k: None)
        monkeypatch.setattr(fra, "set_risk_adjusted_cache", lambda *a, **k: None)
    except Exception:
        pass

    try:
        import services.utils as utils
        monkeypatch.setattr(utils, "ak_call", lambda *a, **k: None)
    except Exception:
        pass

    monkeypatch.setattr(fd, "_get_nav_history_cached", lambda *a, **k: [])

    try:
        import api.signals as signals
        monkeypatch.setattr(signals, "_enrich_trend_forecast", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(signals, "_get_fund_nav_percentile", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(signals, "_fund_timing_label", lambda *a, **k: None, raising=False)
    except Exception:
        pass


def _mock_fund_holdings(monkeypatch, records):
    """把规范持仓源 services.fund_monitor.load_fund_holdings 桩成给定记录。

    注意：`_enrich_detail_with_holding` 在函数内做 `from services.fund_monitor
    import load_fund_holdings`，会在调用时读取模块属性，故 monkeypatch 模块
    上的 load_fund_holdings 即可生效。
    """
    import services.fund_monitor as fm
    monkeypatch.setattr(fm, "load_fund_holdings", lambda uid: list(records), raising=True)


# ============================================================
# ① 换源 + 字段名对齐：含该 code → 正确产出持仓关系
# ============================================================

def test_holding_enrich_uses_fund_holdings_and_costNav(fd, monkeypatch):
    """mock load_fund_holdings 返回含该 code 的记录，详情必须带完整持仓数据。"""
    _install_offline_mocks(monkeypatch, fd, {CODE: NAME}, nav=1.234)
    _mock_fund_holdings(monkeypatch, [
        {"code": CODE, "name": NAME, "costNav": 2.0, "shares": 9000.0},
    ])

    result = fd.fund_detail(CODE, userId="LeiJiang")

    assert result.get("holding_relation") == "🔵 已持仓", result.get("holding_relation")

    my = result["my_holding"]
    assert my["shares"] == 9000.0
    # avg_cost 必须存在，且等于 costNav（回撤基准 = 成本净值，全仓口径一致）
    assert "avg_cost" in my
    assert my["avg_cost"] == 2.0

    # pnl_pct = (nav_now - costNav) / costNav * 100 = (1.234 - 2.0) / 2.0 * 100
    expected = round((1.234 - 2.0) / 2.0 * 100, 2)
    assert result["pnl_pct"] == pytest.approx(expected, abs=1e-9)
    assert result["pnl_pct"] == pytest.approx(-38.3, abs=0.01)


# ============================================================
# ② 不含该 code → 不改写 detail
# ============================================================

def test_no_holding_relation_when_code_absent(fd, monkeypatch):
    """持仓列表里没有该基金 → 不应设置 holding_relation / my_holding。"""
    _install_offline_mocks(monkeypatch, fd, {CODE: NAME})
    _mock_fund_holdings(monkeypatch, [
        {"code": "999999", "name": "别的基金", "costNav": 1.0, "shares": 1.0},
    ])

    result = fd.fund_detail(CODE, userId="LeiJiang")

    assert "holding_relation" not in result
    assert "my_holding" not in result
    assert "pnl_pct" not in result


# ============================================================
# ③ 反例钉死：证明换源，而不是两源并用
# ============================================================

def test_load_user_portfolio_holdings_is_not_used_as_source(fd, monkeypatch):
    """反例：`load_user(...).portfolio.holdings` 里有该 code，但
    `load_fund_holdings` 返回空 → **不应**产生 holding_relation。

    这钉死了「持仓来源真的换到了 fund_monitor.load_fund_holdings」这一事实：
    如果实现回退成两源并用（或又读回 portfolio.holdings），本条会变红。
    """
    _install_offline_mocks(monkeypatch, fd, {CODE: NAME})
    _mock_fund_holdings(monkeypatch, [])  # 规范源为空

    import services.persistence as persistence
    monkeypatch.setattr(
        persistence, "load_user",
        lambda uid: {"portfolio": {"holdings": [
            {"code": CODE, "shares": 123.0, "cost_nav": 9.9},
        ]}},
        raising=True,
    )

    result = fd.fund_detail(CODE, userId="LeiJiang")

    assert "holding_relation" not in result, (
        "holding_relation 来自 load_user 老源 —— 说明没有真正换源")
    assert "my_holding" not in result


# ============================================================
# ④ costNav 为 0 → my_holding 仍带数字型 avg_cost（防前端 .toFixed 崩）
# ============================================================

def test_avg_cost_is_numeric_when_costnav_zero(fd, monkeypatch):
    """costNav 为 0 时，只要下发 my_holding，avg_cost 就必须是数字（给 0），
    否则 pages/_components.js:466 的 `my.avg_cost.toFixed(4)` 会抛异常。"""
    _install_offline_mocks(monkeypatch, fd, {CODE: NAME})
    _mock_fund_holdings(monkeypatch, [
        {"code": CODE, "name": NAME, "costNav": 0, "shares": 500.0},
    ])

    result = fd.fund_detail(CODE, userId="LeiJiang")

    assert result.get("holding_relation") == "🔵 已持仓"
    my = result["my_holding"]
    assert "avg_cost" in my, "costNav 为 0 时不得省略 avg_cost 键"
    assert isinstance(my["avg_cost"], (int, float))
    assert my["avg_cost"] == 0
    assert isinstance(my["shares"], (int, float))
    # costNav=0 无法算盈亏 → 不应下发 pnl_pct
    assert "pnl_pct" not in result


# ============================================================
# ⑤ 记录缺 shares/costNav → 下发的字段仍是数字型
# ============================================================

def test_my_holding_always_has_numeric_shares_and_avg_cost(fd, monkeypatch):
    """记录缺 shares / costNav 时，my_holding 仍应带数字型 shares/avg_cost，
    保证前端 toFixed 不会崩。"""
    _install_offline_mocks(monkeypatch, fd, {CODE: NAME})
    _mock_fund_holdings(monkeypatch, [{"code": CODE, "name": NAME}])  # 缺 shares/costNav

    result = fd.fund_detail(CODE, userId="LeiJiang")

    my = result["my_holding"]
    assert isinstance(my["shares"], (int, float))
    assert isinstance(my["avg_cost"], (int, float))


# ============================================================
# ⑥ 结构守门：增强函数内不得再读 load_user 拿持仓
# ============================================================

def test_holding_enrich_source_is_load_fund_holdings_not_load_user():
    """回归锁定：`_enrich_detail_with_holding` 里必须用 load_fund_holdings
    读持仓，且不得再出现 `from services.persistence import load_user`。"""
    src = (BACKEND_DIR / "api" / "fund_detail.py").read_text(encoding="utf-8")
    idx = src.index("def _enrich_detail_with_holding(")
    helper_and_after = src[idx:]

    assert "load_fund_holdings(" in helper_and_after, (
        "增强函数应使用 load_fund_holdings 读取持仓")
    assert "from services.persistence import load_user" not in helper_and_after, (
        "增强函数内不得再 import load_user 读持仓（已换源）")
