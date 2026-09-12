"""P2-10 选基页补充决策指标（规模/回撤/卡玛/经理年限/换手率）测试。

核心是**防编造**：项目刚清理掉三处硬编码假统计（34.6% / +3.7% / 85%），
本文件断言「算不出来必须是 None + reason」，而不是 0 / 占位值 / 行业平均。

覆盖：
  1. 最大回撤：已知净值序列手算验证
  2. 卡玛比率：回撤为 0 / 负数 / 数据不足 → 三种都必须 None + reason
  3. 基金经理任职年限：mock fund_manager 验证；数据缺失 → None 不报错
  4. 负面控制：Tushare 未配置 → 所有新字段 None，不抛异常、不给 0
  5. 缓存生效：同一 code 连续两次取数只打一次 Tushare
  6. 换手率：无数据源，恒为 None + 原因说明
"""
from datetime import datetime, timedelta

import pytest

from services import fund_screen as fs
from services import tushare_data as ts


@pytest.fixture(autouse=True)
def _clean_metrics_cache():
    """每个用例前后清空指标缓存，避免用例间互相污染（缓存测试依赖此隔离）。"""
    fs._fund_metrics_cache.clear()
    yield
    fs._fund_metrics_cache.clear()


def _nav_rows(n: int = 80):
    """构造 n 个净值点：先涨到 1.5，再跌到 1.2（回撤 20%），其后持平。

    adj_nav 与 unit_nav 同值，方便走 adj_nav 分支。
    """
    rows = []
    for i in range(n - 1):
        nav = 1.0 + 0.5 * (i / (n - 2))     # 1.0 → 1.5
        rows.append({
            "nav_date": f"2024{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "unit_nav": nav,
            "accum_nav": nav,
            "adj_nav": nav,
        })
    rows.append({"nav_date": "20241231", "unit_nav": 1.2, "accum_nav": 1.2, "adj_nav": 1.2})
    return rows


# ───────────────────────── 1. 最大回撤手算验证 ─────────────────────────

def test_max_drawdown_hand_computed():
    # [1.0, 1.2, 0.9, 1.1]：峰值 1.2 → 谷 0.9，(1.2-0.9)/1.2 = 25%
    assert fs.compute_max_drawdown_pct([1.0, 1.2, 0.9, 1.1]) == 25.0
    # [1.0, 2.0, 1.0, 2.0]：峰值 2.0 → 谷 1.0 = 50%
    assert fs.compute_max_drawdown_pct([1.0, 2.0, 1.0, 2.0]) == 50.0
    # 后低点不参与：最后一次跌到 1.5 时峰值仍是 2.0 → 25%（不是 50%）
    assert fs.compute_max_drawdown_pct([1.0, 2.0, 1.0, 1.5]) == 50.0
    # 单调递增 → 回撤 0（由调用方判「非正 → 留空」）
    assert fs.compute_max_drawdown_pct([1.0, 1.1, 1.2]) == 0.0
    # 无效输入 → None，不是 0
    assert fs.compute_max_drawdown_pct([]) is None
    assert fs.compute_max_drawdown_pct([1.0]) is None
    assert fs.compute_max_drawdown_pct([None, "abc", -1]) is None


def test_build_drawdown_metrics_known_series():
    out = fs.build_drawdown_metrics([r["adj_nav"] for r in _nav_rows()], "adj_nav")
    assert out["nav_points"] == 80
    # 峰值 1.5 → 谷 1.2 = 20%，对外是负百分比
    assert out["max_drawdown"] == -20.0
    assert out["max_drawdown_reason"] is None
    assert out["calmar_ratio"] is not None
    assert out["nav_basis"] == "adj_nav"


# ───────────────────────── 2. 卡玛比率三态留空 ─────────────────────────

def test_calmar_null_when_drawdown_zero(monkeypatch):
    """回撤 = 0 → 分母为 0，卡玛必须留空（不许填 0 或 ∞）。"""
    monkeypatch.setattr(fs, "compute_max_drawdown_pct", lambda _v: 0.0)
    out = fs.build_drawdown_metrics([r["adj_nav"] for r in _nav_rows()], "adj_nav")
    assert out["calmar_ratio"] is None
    assert out["calmar_ratio_reason"]
    assert "0" in out["calmar_ratio_reason"]
    # 回撤为 0 时回撤本身也留空，避免显示「回撤 0%」这种误导
    assert out["max_drawdown"] is None
    assert out["max_drawdown_reason"]


def test_calmar_null_when_drawdown_negative(monkeypatch):
    """回撤为负（异常值）→ 同样留空，不把异常值喂给用户。"""
    monkeypatch.setattr(fs, "compute_max_drawdown_pct", lambda _v: -5.0)
    out = fs.build_drawdown_metrics([r["adj_nav"] for r in _nav_rows()], "adj_nav")
    assert out["calmar_ratio"] is None
    assert out["calmar_ratio_reason"]
    assert out["max_drawdown"] is None
    assert out["max_drawdown_reason"]


def test_calmar_null_when_drawdown_unavailable(monkeypatch):
    """最大回撤算不出来 → 卡玛跟着留空，并写清原因。"""
    monkeypatch.setattr(fs, "compute_max_drawdown_pct", lambda _v: None)
    out = fs.build_drawdown_metrics([r["adj_nav"] for r in _nav_rows()], "adj_nav")
    assert out["calmar_ratio"] is None
    assert out["calmar_ratio_reason"]
    assert out["max_drawdown"] is None


def test_drawdown_and_calmar_null_when_insufficient_nav():
    """净值序列不足 60 个交易日 → 回撤与卡玛都留空，且不抛异常。"""
    out = fs.build_drawdown_metrics([1.0, 1.2, 0.9], "unit_nav")
    assert out["nav_points"] == 3
    assert out["max_drawdown"] is None
    assert out["calmar_ratio"] is None
    assert "不足" in out["max_drawdown_reason"]
    assert out["calmar_ratio_reason"]


# ───────────────────────── 3. 基金经理任职年限 ─────────────────────────

def test_manager_tenure_years_pure_calc():
    assert fs.compute_manager_tenure_years("20200101", now=datetime(2024, 1, 1)) == pytest.approx(4.0, abs=0.05)
    # 带分隔符 / 带时间戳的脏格式同样可解析
    assert fs.compute_manager_tenure_years("2020-01-01", now=datetime(2024, 1, 1)) == pytest.approx(4.0, abs=0.05)
    # 已离任：算到 end_date
    assert fs.compute_manager_tenure_years("20200101", "20220101") == pytest.approx(2.0, abs=0.05)
    # 非法 / 缺失 / 结束早于起始 → None（不是 0）
    assert fs.compute_manager_tenure_years("", now=datetime(2024, 1, 1)) is None
    assert fs.compute_manager_tenure_years("未知") is None
    assert fs.compute_manager_tenure_years("20220101", "20200101") is None


def test_manager_tenure_from_mock_fund_manager(monkeypatch):
    begin = (datetime.now() - timedelta(days=int(365.25 * 4))).strftime("%Y%m%d")
    # end_date 是单个空格（Tushare 实测脏数据）→ 必须识别为「在任」
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {
        "available": True,
        "source": "tushare",
        "managers": [{"name": "张三", "begin_date": begin, "end_date": " "}],
    })
    out = fs.build_manager_metrics("000001")
    assert out["manager_name"] == "张三"
    assert out["manager_tenure_years"] == pytest.approx(4.0, abs=0.1)
    assert out["manager_begin_date"] == begin
    assert out["manager_tenure_reason"] is None


def test_manager_tenure_picks_longest_serving(monkeypatch):
    now = datetime.now()
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {
        "available": True,
        "managers": [
            {"name": "新经理", "begin_date": (now - timedelta(days=365)).strftime("%Y%m%d"), "end_date": ""},
            {"name": "老经理", "begin_date": (now - timedelta(days=int(365.25 * 6))).strftime("%Y%m%d"), "end_date": ""},
        ],
    })
    out = fs.build_manager_metrics("000001")
    assert out["manager_name"] == "老经理"
    assert out["manager_tenure_years"] == pytest.approx(6.0, abs=0.1)


def test_manager_tenure_null_when_manager_missing(monkeypatch):
    """经理数据缺失 → None + reason，绝不报错、绝不用默认值填。"""
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {"available": False, "source": "tushare"})
    out = fs.build_manager_metrics("000001")
    assert out["manager_tenure_years"] is None
    assert out["manager_name"] is None
    assert out["manager_tenure_reason"]

    # 有记录但 begin_date 全脏 → 同样 None
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {
        "available": True,
        "managers": [{"name": "王五", "begin_date": "——", "end_date": ""}],
    })
    out = fs.build_manager_metrics("000001")
    assert out["manager_tenure_years"] is None
    assert out["manager_tenure_reason"]


# ───────────────────────── 4. 负面控制：Tushare 不可用 ─────────────────────────

_VALUE_KEYS = (
    "shares_billion",
    "shares_date",
    "net_asset_billion",
    "max_drawdown",
    "calmar_ratio",
    "manager_name",
    "manager_tenure_years",
    "manager_begin_date",
    "turnover_rate",
)
_REASON_KEYS = (
    "shares_reason",
    "net_asset_reason",
    "max_drawdown_reason",
    "calmar_ratio_reason",
    "manager_tenure_reason",
    "turnover_rate_reason",
)


def test_all_metrics_null_when_tushare_unconfigured(monkeypatch):
    monkeypatch.setattr(ts, "is_configured", lambda: False)

    def _boom(*a, **kw):
        raise AssertionError("Tushare 未配置时不该发起任何数据请求")

    monkeypatch.setattr(ts, "get_fund_nav", _boom)
    monkeypatch.setattr(ts, "get_fund_share", _boom)
    monkeypatch.setattr(ts, "get_fund_manager", _boom)

    out = fs.build_fund_metrics("000001")   # 不抛异常
    for k in _VALUE_KEYS:
        assert out[k] is None, f"{k} 应留空，实际 {out[k]!r}"
    for k in _REASON_KEYS:
        assert out[k], f"{k} 必须写清留空原因"
    # 任何一个数值字段都不许是 0 / 占位数字
    for k in _VALUE_KEYS:
        assert not isinstance(out[k], (int, float)), f"{k} 不得是数字占位值"


def test_enrich_fund_metrics_null_when_unconfigured(monkeypatch):
    """列表富化路径同样：未配置时字段全 None，不动已有字段、不抛异常。"""
    monkeypatch.setattr(ts, "is_configured", lambda: False)
    funds = [{"code": "000001", "name": "测试基金", "score": 50.0}]
    fs.enrich_fund_metrics(funds)           # 不抛异常
    assert funds[0]["score"] == 50.0
    assert funds[0]["max_drawdown"] is None
    assert funds[0]["max_drawdown_reason"]
    assert funds[0]["turnover_rate"] is None


# ───────────────────────── 5. 缓存生效 ─────────────────────────

def test_metrics_cache_prevents_second_tushare_call(monkeypatch):
    calls = {"nav": 0, "share": 0, "manager": 0}
    rows = _nav_rows()

    monkeypatch.setattr(ts, "is_configured", lambda: True)

    def _fake_nav(code, days=60):
        calls["nav"] += 1
        return {"available": True, "source": "tushare", "code": code,
                "unit_nav": 1.2, "nav_date": "20241231", "navs": rows}

    def _fake_share(ts_code, days=30):
        calls["share"] += 1
        return {"available": True, "source": "tushare",
                "shares_latest": 12.5, "data_date": "20240630"}

    def _fake_manager(code):
        calls["manager"] += 1
        return {"available": True, "source": "tushare",
                "managers": [{"name": "李四", "begin_date": "20190101", "end_date": " "}]}

    monkeypatch.setattr(ts, "get_fund_nav", _fake_nav)
    monkeypatch.setattr(ts, "get_fund_share", _fake_share)
    monkeypatch.setattr(ts, "get_fund_manager", _fake_manager)

    first = fs.get_fund_metrics("000001")
    second = fs.get_fund_metrics("000001")

    assert calls == {"nav": 1, "share": 1, "manager": 1}, "第二次取数必须命中缓存"
    assert first == second
    # 派生值核对：净资产 = 份额(亿份) × 单位净值 = 12.5 × 1.2
    assert first["net_asset_billion"] == 15.0
    assert first["shares_billion"] == 12.5
    assert first["max_drawdown"] == -20.0
    assert first["calmar_ratio"] is not None
    assert first["manager_name"] == "李四"


def test_metrics_cache_is_per_code(monkeypatch):
    calls = {"nav": 0}
    rows = _nav_rows()
    monkeypatch.setattr(ts, "is_configured", lambda: True)
    monkeypatch.setattr(ts, "get_fund_nav", lambda code, days=60: (calls.__setitem__("nav", calls["nav"] + 1), {
        "available": True, "code": code, "unit_nav": 1.2, "navs": rows})[1])
    monkeypatch.setattr(ts, "get_fund_share", lambda ts_code, days=30: {"available": False})
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {"available": False})

    fs.get_fund_metrics("000001")
    fs.get_fund_metrics("000001")
    fs.get_fund_metrics("000002")
    assert calls["nav"] == 2, "不同 code 各自取数，但同 code 第二次应命中缓存"


# ───────────────────────── 6. 换手率：无数据源，如实留空 ─────────────────────────

def test_turnover_rate_always_null_with_reason(monkeypatch):
    monkeypatch.setattr(ts, "is_configured", lambda: True)
    monkeypatch.setattr(ts, "get_fund_nav", lambda code, days=60: {"available": False})
    monkeypatch.setattr(ts, "get_fund_share", lambda ts_code, days=30: {"available": False})
    monkeypatch.setattr(ts, "get_fund_manager", lambda code: {"available": False})

    out = fs.build_fund_metrics("000001")
    assert out["turnover_rate"] is None
    assert "换手率" in out["turnover_rate_reason"]
    # 其余拿不到的字段同样留空（没有数据源时不许编数）
    assert out["max_drawdown"] is None
    assert out["manager_tenure_years"] is None
