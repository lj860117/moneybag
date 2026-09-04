"""端到端 + 预算守门 + 接缝条件式语义 + 文案渲染 的独立回归。

覆盖设计 T01 验收 1~4（build_signal_pool 条件式语义）与 T05 验收
（预算守门、纯基金账户端到端、4 类文案纯文本 ≤8 行）。
全部离线：不发起任何网络请求，状态用内存态，持仓/采集器全部打桩。
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import services.signal_scout as signal_scout
import services.fund_signal as fund_signal
from services.fund_signal import budget, dca, drawdown, manager, render, xray
from services.fund_signal import state as real_state
from services.fund_signal.portfolio import FundPosition


class _FakeState:
    """内存版 state.load / state.save，隔离测试间的落盘副作用。

    ⚠️ 只 monkeypatch 真实 state 模块的 load/save，不替换整个 state 模块引用，
    这样 budget/manager/dca/drawdown 里的 `state.PUSH_LOG` 等常量仍来自真实模块。
    """
    def __init__(self):
        self.store = {}

    def load(self, user_id, name):
        return self.store.get((user_id, name), {})

    def save(self, user_id, name, data):
        self.store[(user_id, name)] = dict(data)


def _patch_state(monkeypatch, fs):
    """把真实 state 模块的 load/save 指向内存态（保持常量不变）。"""
    monkeypatch.setattr(real_state, "load", fs.load)
    monkeypatch.setattr(real_state, "save", fs.save)
    return fs


def _pf(code, name, weight_mv=50.0):
    return FundPosition(
        code=code, name=name, shares=1000.0, cost_nav=1.0, unit_nav=1.0,
        adj_nav=1.0, is_qdii=False, market_value=1000.0, weight_mv=weight_mv,
        nav_date="20260930", nav_history=[],
    )


def _sig(sig_type, relevance=100):
    return {
        "type": sig_type, "title": sig_type, "content": "",
        "codes": [], "level": "info", "tags": [],
        "relevance": relevance, "related_holding": "",
    }


# ============================================================
# T01：build_signal_pool 条件式语义（纯基金 / 持股 / 混合 / 动态）
# ============================================================

def _stub_three_collectors(monkeypatch, calls):
    """把三个个股事件采集器打桩成「计数 + 返回一条同类型信号」。"""
    def make(name, sig_type):
        def _f():
            calls[name] += 1
            return [_sig(sig_type)]
        return _f
    monkeypatch.setattr(signal_scout, "_collect_unlock_signals", make("unlock", "unlock"))
    monkeypatch.setattr(signal_scout, "_collect_holder_changes", make("holder", "holder_change"))
    monkeypatch.setattr(signal_scout, "_collect_fund_flow_signals", make("fund_flow", "fund_flow"))
    monkeypatch.setattr(signal_scout, "_collect_news_signals", lambda: [])
    monkeypatch.setattr(signal_scout, "_collect_technical_signals", lambda: [])


def test_build_signal_pool_pure_fund_skips_three_collectors(monkeypatch):
    calls = {"unlock": 0, "holder": 0, "fund_flow": 0}
    _stub_three_collectors(monkeypatch, calls)
    monkeypatch.setattr(fund_signal, "_collect_fund_signals", lambda uid: [])

    pool = fund_signal.build_signal_pool("u1", {}, {"013107": "华夏先进制造"})

    assert calls == {"unlock": 0, "holder": 0, "fund_flow": 0}, calls
    assert all(s["type"] not in ("unlock", "holder_change", "fund_flow") for s in pool)


def test_build_signal_pool_stock_account_uses_collect_unchanged(monkeypatch):
    unlock_sig = _sig("unlock")
    calls = {"collect": 0}

    def fake_collect():
        calls["collect"] += 1
        return [unlock_sig]

    monkeypatch.setattr(signal_scout, "collect", fake_collect)

    pool = fund_signal.build_signal_pool("u1", {"301563": "云汉芯城"}, {})

    assert calls["collect"] == 1
    assert unlock_sig in pool, "持股账户的 unlock 信号被静默丢弃（防功能退化）"


def test_build_signal_pool_mixed_account_no_fund_signals_appended(monkeypatch):
    news = _sig("news_market")
    monkeypatch.setattr(signal_scout, "collect", lambda: [news])

    def boom(uid):
        raise AssertionError("混合账户不应调用 _collect_fund_signals")
    monkeypatch.setattr(fund_signal, "_collect_fund_signals", boom)

    pool = fund_signal.build_signal_pool("u1", {"301563": "云汉芯城"}, {"013107": "华夏"})

    assert pool == [news]


def test_build_signal_pool_branch_is_evaluated_dynamically(monkeypatch):
    """同一进程内先纯基金、再持股，两次行为必须不同（分支不能是模块级常量）。"""
    calls = {"unlock": 0, "holder": 0, "fund_flow": 0, "collect": 0, "fund_signals": 0}
    _stub_three_collectors(monkeypatch, calls)

    def fake_collect():
        calls["collect"] += 1
        return []
    monkeypatch.setattr(signal_scout, "collect", fake_collect)

    def fake_fund_signals(uid):
        calls["fund_signals"] += 1
        return []
    monkeypatch.setattr(fund_signal, "_collect_fund_signals", fake_fund_signals)

    fund_signal.build_signal_pool("u1", {}, {"013107": "华夏"})          # 纯基金
    fund_signal.build_signal_pool("u1", {"301563": "云汉芯城"}, {})      # 持股

    assert calls["collect"] == 1, calls
    assert calls["fund_signals"] == 1, calls
    assert calls["unlock"] == 0 and calls["holder"] == 0 and calls["fund_flow"] == 0


# ============================================================
# T05：端到端 —— 纯基金账户 match() 不含个股事件类
# ============================================================

def test_e2e_pure_fund_match_excludes_stock_event_types(monkeypatch):
    import services.stock_monitor as stock_monitor
    import services.fund_monitor as fund_monitor

    calls = {"unlock": 0, "holder": 0, "fund_flow": 0}
    _stub_three_collectors(monkeypatch, calls)
    monkeypatch.setattr(fund_signal, "_collect_fund_signals", lambda uid: [])

    monkeypatch.setattr(stock_monitor, "load_stock_holdings", lambda uid: [], raising=True)
    monkeypatch.setattr(
        fund_monitor, "load_fund_holdings",
        lambda uid: [{"code": "013107", "name": "华夏先进制造"}], raising=True,
    )
    monkeypatch.setattr(signal_scout, "_save_matched", lambda uid, sigs: None)

    matched = signal_scout.match("u_fund_only")

    assert calls == {"unlock": 0, "holder": 0, "fund_flow": 0}, calls
    assert all(m["type"] not in ("unlock", "holder_change", "fund_flow") for m in matched)


# ============================================================
# T05：预算守门（日限 2 / 月限 4，按优先级砍）
# ============================================================

def test_budget_daily_limit_keeps_top_two_priority(monkeypatch):
    fs = _patch_state(monkeypatch, _FakeState())

    sigs = [
        _sig("fund_xray_concentration"),
        _sig("fund_drawdown_rung"),
        _sig("fund_manager_change"),
        _sig("dca_preflight"),
        _sig("fund_manager_change"),
        _sig("dca_preflight"),
    ]
    out = budget.gate("u1", sigs, now=datetime(2026, 9, 4, 8, 0, 0))

    rel = [s["relevance"] for s in out]
    assert rel.count(100) == 2, rel
    assert rel.count(40) == 4, rel
    pushed = [s for s in out if s["relevance"] == 100]
    assert all(s["type"] == "dca_preflight" for s in pushed), pushed


def test_budget_monthly_limit_across_days(monkeypatch):
    fs = _patch_state(monkeypatch, _FakeState())

    def run(day):
        sigs = [_sig("fund_drawdown_rung") for _ in range(2)]
        return budget.gate("u1", sigs, now=datetime(2026, 9, day, 8, 0, 0))

    d1, d2, d3 = run(4), run(5), run(6)

    assert sum(1 for s in d1 if s["relevance"] == 100) == 2
    assert sum(1 for s in d2 if s["relevance"] == 100) == 2
    # 第 3 天：月额度 4 已用满 → 全部降级
    assert all(s["relevance"] == 40 for s in d3), [s["relevance"] for s in d3]


# ============================================================
# T05：预算守门 → match() 集成回归（守门失效修复）
# ============================================================
# 历史 bug：budget.gate 只把超预算信号 relevance 100→40，但 match() 里
# `relevance=0` 无条件重算，codes 命中基金代码后被覆盖回 100，_should_push
# 放行 → 超预算信号照推。修复后 match() 对 _FUND_SIGNAL_TYPES 直接沿用
# 自带 relevance，不再重算。下面两个用例用 codes=[真实基金代码] 走完整
# match()，专门堵住「codes=[] 死测试测不出」的缺口。

def _inject_pure_fund_pool(monkeypatch, sig):
    """纯基金账户 + 注入单条基金信号，绕过采集器直击 match() 特判分支。"""
    import services.fund_monitor as fund_monitor
    import services.stock_monitor as stock_monitor

    monkeypatch.setattr(stock_monitor, "load_stock_holdings", lambda uid: [], raising=True)
    monkeypatch.setattr(
        fund_monitor, "load_fund_holdings",
        lambda uid: [{"code": "013107", "name": "华夏先进制造"}], raising=True,
    )
    monkeypatch.setattr(signal_scout, "_save_matched", lambda uid, sigs: None)
    monkeypatch.setattr(fund_signal, "build_signal_pool", lambda uid, sc, fc: [sig])


def test_e2e_budget_cut_fund_signal_stays_unpushed(monkeypatch):
    cut_sig = {
        "type": "fund_drawdown_rung", "title": "回撤", "content": "",
        "codes": ["013107"], "level": "warning", "tags": [],
        "relevance": 40, "related_holding": "华夏先进制造",
    }
    _inject_pure_fund_pool(monkeypatch, cut_sig)

    matched = signal_scout.match("u_fund_only")

    assert len(matched) == 1, matched
    assert matched[0]["relevance"] < 50, matched          # (a) 仍低于推送阈值
    assert matched[0]["relevance"] == 40, matched         # 未被重算回 100
    assert signal_scout._should_push(matched[0]) is False  # (c) 不推送
    assert matched[0]["related_holding"] == "华夏先进制造"  # (b) 保留前端渲染所需字段


def test_e2e_budget_kept_fund_signal_still_pushes(monkeypatch):
    kept_sig = {
        "type": "fund_drawdown_rung", "title": "回撤", "content": "",
        "codes": ["013107"], "level": "warning", "tags": [],
        "relevance": 100, "related_holding": "华夏先进制造",
    }
    _inject_pure_fund_pool(monkeypatch, kept_sig)

    matched = signal_scout.match("u_fund_only")

    assert len(matched) == 1, matched
    assert matched[0]["relevance"] == 100, matched
    assert signal_scout._should_push(matched[0]) is True


# ============================================================
# T04：经理变更（冷启动 + 配对）
# ============================================================

def test_manager_cold_start_then_pairing(monkeypatch):
    import services.tushare_data as td

    fs = _patch_state(monkeypatch, _FakeState())
    positions = [_pf("008984", "财通科技创新混合C", 12.5)]

    # 用相对当前时间近的日期，绕过 30 天冷却。
    ann = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")

    # 冷启动：张胤在任
    monkeypatch.setattr(td, "get_fund_manager", lambda code: {
        "available": True, "all_managers": [
            {"name": "张胤", "begin_date": "20210927", "end_date": " ", "ann_date": ann},
        ]})
    assert manager.collect("u1", positions) == []

    # 变更：张胤离任 + 袁泽强接任（begin_date 与 end_date 相差 0 天 → 配对）
    monkeypatch.setattr(td, "get_fund_manager", lambda code: {
        "available": True, "all_managers": [
            {"name": "张胤", "begin_date": "20210927", "end_date": end, "ann_date": ann},
            {"name": "袁泽强", "begin_date": end, "end_date": " ", "ann_date": ann},
        ]})
    out = manager.collect("u1", positions)

    assert len(out) == 1
    assert out[0]["type"] == "fund_manager_change"
    assert "张胤" in out[0]["content"] and "袁泽强" in out[0]["content"]


# ============================================================
# T05：DCA 触发日顺延 + 幂等 + 冷启动宽限
# ============================================================

def test_dca_resolve_trigger_day_shifts_forward_over_weekend():
    assert dca.resolve_trigger_day(date(2026, 9, 24)) == date(2026, 9, 24)   # 周四
    assert dca.resolve_trigger_day(date(2026, 10, 24)) == date(2026, 10, 23)  # 周六→周五


def test_dca_collect_returns_empty_on_non_trigger_day(monkeypatch):
    fs = _patch_state(monkeypatch, _FakeState())
    out = dca.collect("u1", [_pf("013107", "华夏先进制造")], None, today=date(2026, 9, 20))
    assert out == []


def test_dca_cold_start_on_trigger_day_is_skipped(monkeypatch):
    fs = _patch_state(monkeypatch, _FakeState())
    # 上线首月距 24 日不足 3 天 → 跳过本月（写 last_push_month）
    out = dca.collect("u1", [_pf("013107", "华夏先进制造")], None, today=date(2026, 9, 24))
    assert out == []
    assert fs.load("u1", "dca_state").get("last_push_month") == "2026-09"


def test_dca_idempotent_within_same_month(monkeypatch):
    fs = _patch_state(monkeypatch, _FakeState())
    fs.save("u1", "dca_state", {"last_push_month": "2026-09"})
    out = dca.collect("u1", [_pf("013107", "华夏先进制造")], None, today=date(2026, 9, 24))
    assert out == []


# ============================================================
# T05：4 类文案纯文本、≤8 行、无原始代码
# ============================================================

def _sample_xray_result():
    positions = [
        _pf("013107", "华夏先进制造", 50.0),
        _pf("006555", "浦银全球智能科技QDII", 25.0),
        _pf("007356", "汇添富科技创新", 25.0),
    ]
    portfolios = {
        "013107": {"ok": True, "end_date": "20260630", "ann_date": "20260721",
                   "holdings": [{"symbol": "002371.SZ", "stk_mkv_ratio": 40.0},
                                {"symbol": "300502.SZ", "stk_mkv_ratio": 30.0}]},
        "006555": {"ok": False},
        "007356": {"ok": True, "end_date": "20260630", "ann_date": "20260721",
                   "holdings": [{"symbol": "002371.SZ", "stk_mkv_ratio": 40.0},
                                {"symbol": "688037.SZ", "stk_mkv_ratio": 10.0}]},
    }
    sw = {
        "002371.SZ": {"l2": "半导体", "name": "北方华创"},
        "300502.SZ": {"l2": "通信设备", "name": "新易盛"},
        "688037.SZ": {"l2": "半导体", "name": "芯源微"},
    }
    return xray.compute_exposure(positions, portfolios, sw, "sw_l2"), positions


def test_render_xray_is_plain_text_within_8_lines():
    result, positions = _sample_xray_result()
    sig = render.render_xray(result, positions)

    assert sig is not None
    assert len(sig["content"].split("\n")) <= 8, sig["content"]
    assert "002371.SZ" not in sig["content"], sig["content"]  # 必须「中文名(6位代码)」
    assert "北方华创(002371)" in sig["content"], sig["content"]
    assert "穿透覆盖" in sig["content"] and "持仓截止" in sig["content"]
    assert "浦银全球智能科技" in sig["content"], "盲区基金未点名"


def test_render_drawdown_is_plain_text_within_8_lines():
    items = [{
        "code": "005698", "name": "华夏全球科技先锋QDII", "dd_cost_pct": -31.2,
        "rung": 1, "dd_roll_pct": -20.0, "high_date": "08-15", "high_unit_nav": 3.9,
        "nav_date": "09-24", "cost_nav": 3.5298, "unit_nav": 2.4285,
        "weight_mv": 10.5, "is_qdii": True,
    }]
    sig = render.render_drawdown(items, [_pf("005698", "华夏全球科技先锋QDII", 10.5)])

    assert sig is not None
    assert len(sig["content"].split("\n")) <= 8, sig["content"]
    assert "005698" in sig["content"]  # 基金 6 位代码允许出现
    assert "净值日" in sig["content"]


def test_render_manager_is_plain_text_within_8_lines():
    changes = [{
        "code": "008984", "fund_name": "财通科技创新混合C",
        "departed": {"name": "张胤", "begin_date": "2021-09-27", "end_date": "2026-06-11",
                     "ann_date": "2026-06-12"},
        "joined": {"name": "袁泽强", "begin_date": "2026-06-11", "end_date": "",
                   "ann_date": "2026-06-12"},
    }]
    sig = render.render_manager(changes, [_pf("008984", "财通科技创新混合C", 12.5)])

    assert sig is not None
    assert len(sig["content"].split("\n")) <= 8, sig["content"]
    assert "张胤" in sig["content"] and "袁泽强" in sig["content"]


def test_render_dca_is_plain_text_within_8_lines():
    snap = {
        "combo_cost_pct": -2.2,
        "deepest": {"code": "005698", "name": "华夏全球科技先锋QDII",
                    "dd_cost_pct": -26.9, "weight_mv": 10.5},
        "best": {"code": "013107", "name": "华夏先进制造",
                 "dd_cost_pct": 18.8, "weight_mv": 17.1},
        "gainers": [{"code": "013107", "name": "华夏先进制造", "dd_cost_pct": 18.8}],
        "losers": [
            {"code": "005698", "name": "华夏全球科技先锋QDII", "cost_nav": 3.5298,
             "unit_nav": 2.5786, "dd_cost_pct": -26.9},
            {"code": "008984", "name": "财通科技创新混合C", "cost_nav": 2.0263,
             "unit_nav": 1.7557, "dd_cost_pct": -13.4},
        ],
        "xray": _sample_xray_result()[0],
        "nav_date": "20260924",
    }
    sig = render.render_dca(snap, [_pf("013107", "华夏先进制造", 17.1), _pf("005698", "华夏全球科技先锋QDII", 10.5)])

    assert sig is not None
    assert len(sig["content"].split("\n")) <= 8, sig["content"]
    assert "相对成本净值" in sig["content"]
