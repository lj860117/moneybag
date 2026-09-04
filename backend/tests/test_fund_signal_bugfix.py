"""fund_signal 前置 Bug 修复（B1 / B6 / B4）的独立回归。

全部离线：monkeypatch tushare_data._call_tushare / urllib.request.urlopen，
不发起任何真实网络请求。
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def td():
    import services.tushare_data as tushare_data
    tushare_data._ts_cache.clear()
    yield tushare_data
    tushare_data._ts_cache.clear()


def _fake_call(rows_by_api):
    """按 api_name 分发的 _call_tushare 打桩。"""
    def _call(api_name, params, fields=""):
        return list(rows_by_api.get(api_name, []))
    return _call


# ============================================================
# B1：get_fund_manager 在任判定必须 strip 掉 end_date 的空格
# ============================================================

def test_b1_manager_active_detection_strips_whitespace(td, monkeypatch):
    """end_date 为空是【单个空格 ' '】，不是 ''/None —— 不 strip 则在任恒为 0。"""
    rows = [
        {"name": "屠环宇", "begin_date": "20240704", "end_date": "20240704",
         "ann_date": "20240705"},
        {"name": "现任经理", "begin_date": "20240704", "end_date": " ",
         "ann_date": "20240705"},
    ]
    monkeypatch.setattr(td, "_call_tushare",
                        _fake_call({"fund_manager": rows}))

    result = td.get_fund_manager("013107.OF")

    assert result["available"] is True
    names = [m["name"] for m in result["managers"]]
    assert "现任经理" in names
    assert "屠环宇" not in names, "已离任的屠环宇被误判为在任"
    assert len(result["all_managers"]) == 2, "all_managers 必须含离任记录供 diff"


def test_b1_manager_tenure_uses_end_date_for_departed(td, monkeypatch):
    """离任经理任期算到 end_date，不能算到今天。"""
    rows = [
        {"name": "张胤", "begin_date": "20210927", "end_date": "20260611",
         "ann_date": "20260612"},
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_manager": rows}))

    result = td.get_fund_manager("008984.OF")

    # 唯一记录已离任 → 无在任 → fallback 会选中它，但 tenure 必须用 end_date。
    mgr = result["managers"][0]
    assert mgr["name"] == "张胤"
    assert 4.5 < mgr["tenure_years"] < 5.0, f"任期算到今天了: {mgr['tenure_years']}"


def test_b1_fallback_tie_warns_when_no_active(td, monkeypatch, capsys):
    """无在任且 begin_date 并列时显式告警，避免「静默选错人」。"""
    rows = [
        {"name": "A", "begin_date": "20240101", "end_date": "20240601", "ann_date": "20240602"},
        {"name": "B", "begin_date": "20240101", "end_date": "20240601", "ann_date": "20240602"},
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_manager": rows}))

    td.get_fund_manager("000001.OF")

    assert "begin_date 并列" in capsys.readouterr().out


def test_b1_manager_active_returns_nonempty_for_qdii_fund(td, monkeypatch):
    """回归：006555.OF 此前在任数为 0，修复后必须非空。"""
    rows = [
        {"name": "某在任经理", "begin_date": "20220101", "end_date": " ",
         "ann_date": "20220102"},
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_manager": rows}))

    result = td.get_fund_manager("006555.OF")

    assert result["available"] is True
    assert result["managers"], "006555 在任经理应为非空"


# ============================================================
# B6：get_fund_portfolio 港股代码归一化 + 同标的去重
# ============================================================

def _portfolio_row(symbol, end_date="20260630", ratio=5.83, mkv=1000.0):
    return {
        "symbol": symbol, "end_date": end_date, "ann_date": "20260721",
        "mkv": mkv, "amount": 100.0,
        "stk_mkv_ratio": ratio, "stk_float_ratio": 0.5,
    }


def test_b6_hk_symbol_normalization_and_dedupe(td, monkeypatch, capsys):
    """00981.HK 与 0981.HK 同一标的 → 归一化 + 去重，权重合并。"""
    rows = [
        _portfolio_row("00981.HK", ratio=5.83, mkv=1000.0),
        _portfolio_row("0981.HK", ratio=5.83, mkv=1000.0),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_portfolio": rows}))

    result = td.get_fund_portfolio("016501.OF", "20260630")

    assert result["available"] is True
    hk = [h for h in result["top_holdings"] if h["symbol"] == "00981.HK"]
    assert len(hk) == 1, f"00981.HK 应只出现 1 次，实际 {len(hk)}"
    assert abs(hk[0]["stk_mkv_ratio"] - 11.66) < 0.01, hk[0]["stk_mkv_ratio"]
    assert abs(hk[0]["mkv"] - 2000.0) < 0.01, hk[0]["mkv"]
    assert "港股代码去重" in capsys.readouterr().out


def test_b6_does_not_merge_across_report_periods(td, monkeypatch):
    """不同 end_date 不得合并（不传 period 时 rows 可能横跨多个报告期）。"""
    rows = [
        _portfolio_row("00981.HK", end_date="20260331", ratio=5.83, mkv=1000.0),
        _portfolio_row("00981.HK", end_date="20260630", ratio=5.83, mkv=1000.0),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_portfolio": rows}))

    result = td.get_fund_portfolio("016501.OF")

    # 只返回最新报告期，且权重不被上一期污染。
    assert result["end_date"] == "20260630"
    assert len(result["top_holdings"]) == 1
    assert abs(result["top_holdings"][0]["stk_mkv_ratio"] - 5.83) < 0.01


def test_b6_a_share_symbol_keeps_suffix(td, monkeypatch):
    """A 股代码经归一化补后缀（002371 → 002371.SZ），去重键正确。"""
    rows = [
        _portfolio_row("002371", ratio=9.89, mkv=1000.0),
        _portfolio_row("002371.SZ", ratio=9.89, mkv=1000.0),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_call({"fund_portfolio": rows}))

    result = td.get_fund_portfolio("016501.OF", "20260630")

    sz = [h for h in result["top_holdings"] if h["symbol"] == "002371.SZ"]
    assert len(sz) == 1
    assert abs(sz[0]["stk_mkv_ratio"] - 19.78) < 0.01


# ============================================================
# B4：_call_tushare 非 0 code 显式告警（零行为变更）
# ============================================================

def test_b4_non_zero_code_warns_and_returns_empty(td, monkeypatch, capsys):
    """code=40203（无权限）→ 打印告警、返回 []，不抛异常。"""
    class FakeResp:
        def read(self):
            return json.dumps({"code": 40203, "msg": "无权限", "data": None}).encode("utf-8")

    monkeypatch.setattr(td, "_get_token", lambda: "dummy-token")
    monkeypatch.setattr(td.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    td._ts_cache.clear()

    out = td._call_tushare("fund_manager", {}, "ts_code,name")

    captured = capsys.readouterr().out
    assert "code=40203" in captured
    assert out == []
