"""``services.fund_monitor.get_fund_nav_history`` 三层降级**口径一致**回归测试

背景（v9.9.57 定性 + 修复）
--------------------------
本函数服务「**最大回撤 / 波动率 / 回测**」（``calc_risk_metrics`` 与
``scripts/stock_monitor_cron.py:1389`` 的持仓预警），权威口径是**累计净值**：
累计净值在分红除权日不跳空，回撤才不会被"分红除权"误判成暴跌。

修复前三条降级路径的口径是劈叉的::

    L1 AKShare    累计净值走势 → 累计 ✅（取不到才降级单位）
    L2 Tushare    r["unit_nav"] → **单位** ❌  ← 唯一的偏离层
    L3 天天基金   LJJZ or DWJZ → 累计 ✅（LJJZ=累计，2026-09-22 实测）

而 ``cache_key = f"{code}_{days}"`` **不含口径维度**，调用方拿到一串 nav 后
无从分辨自己拿到的是哪一套 —— 同一只基金，L1 活着返回 4.1558、L1 挂掉 L2
顶上返回 2.9119（002163 实测 accum/unit = 1.427×），"最大回撤 / 波动率 /
最新净值"随数据源可用性漂移。

生产实测真值（天天基金 f10/lsjz，2026-09-22 拉取）::

    002163: DWJZ(单位) 2.9119   LJJZ(累计) 4.1558
    163406: DWJZ(单位) 2.2401   LJJZ(累计) 8.5191

⚠️ 另需与 ``adj_nav``（复权净值，分红再投资复利）区分：002163 的 adj_nav
= 6.7802，与「累计净值」不是一个量纲。若"统一成累计"时错取 adj_nav，会把
1.427× 的跳变放大成 2.35×。本文件用 :func:`test_l2_never_picks_adj_nav`
钉死这一点。

本文件是"能变红"的行为测试（不是自证绿的摆设）：
  - 把 ``fund_monitor.py`` L2 改回 ``nav = _safe_float(r.get("unit_nav"))``
    → :func:`test_l2_tushare_returns_accum_nav` 立刻红；
  - 把整段判定改成逐行 ``accum_nav or unit_nav``
    → :func:`test_l2_no_per_row_caliber_mixing` 立刻红；
  - 把 L2 的取值写成 ``r.get("adj_nav")``
    → :func:`test_l2_never_picks_adj_nav` 立刻红；
  - 把 L3 改回逐行 ``LJJZ or DWJZ``
    → :func:`test_l3_em_no_per_row_caliber_mixing` 立刻红；
  - 把 L3 的预扫从完整 ``all_items`` 缩到截取后的 ``result[-days:]``
    → :func:`test_l3_prescan_covers_whole_window` 立刻红。
"""
import sys
from pathlib import Path

import pytest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import fund_monitor  # noqa: E402

CODE = "002163"
# 002163 生产实测（2026-09-22，天天基金 f10/lsjz）
UNIT_NAV = 2.9119
ACCUM_NAV = 4.1558
ADJ_NAV = 6.7802


class _FakeDF:
    """最小 DataFrame 替身，满足 ``empty`` / ``tail(n)`` / ``iterrows()``。

    与 ``test_briefing_caliber_unit_nav.py`` 的同名替身一致：本函数只用到
    这三个成员，无需依赖 pandas。
    """

    def __init__(self, rows: list):
        self._rows = rows

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    def tail(self, n: int) -> "_FakeDF":
        return _FakeDF(self._rows[-n:])

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, r


class _FakeResponse:
    """``requests.get`` 的替身：只提供 EM 降级分支用到的 ``.text``。"""

    def __init__(self, payload: str):
        self.text = payload


def _em_jsonp(items: list) -> str:
    """构造天天基金 f10/lsjz 的 JSONP 响应体。"""
    import json
    return "x(" + json.dumps({"Data": {"LSJZList": items}}, ensure_ascii=False) + ")"


# ============================================================
# 夹具：把三条数据源完全接管
# ============================================================

def _l1_down(monkeypatch):
    """L1 AKShare 抛异常（模拟生产 akshare 超时/返回空）。"""
    monkeypatch.setattr(
        "infra.data_source.market.stocks.get_fund_nav_history",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("akshare down")))


def _l3_down(monkeypatch):
    """L3 天天基金网络不可用。"""
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))


def _only_l1(monkeypatch, rows: list):
    """只让 L1 可用（L2 未配置、L3 断网）。"""
    monkeypatch.setattr("infra.data_source.market.stocks.get_fund_nav_history",
                        lambda **kwargs: _FakeDF(rows))
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    _l3_down(monkeypatch)


def _only_l2(monkeypatch, navs: list):
    """只让 L2 Tushare 可用（L1 挂、L3 断网）。"""
    _l1_down(monkeypatch)
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: True, raising=False)
    monkeypatch.setattr("services.tushare_data.get_fund_nav",
                        lambda code, days=60: {"available": True, "navs": navs},
                        raising=False)
    _l3_down(monkeypatch)


def _only_l3(monkeypatch, items: list):
    """只让 L3 天天基金可用（L1 挂、L2 未配置）。"""
    _l1_down(monkeypatch)
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: _FakeResponse(_em_jsonp(items)))


def _fresh(code: str, days: int):
    """清掉该 cache_key 后强制刷新，避免模块级 ``_nav_cache`` 串味。"""
    fund_monitor._nav_cache._data.pop(f"{code}_{days}", None)
    return fund_monitor.get_fund_nav_history(code, days=days, force_refresh=True)


# ============================================================
# 1. L2 Tushare 必须返回累计口径
# ============================================================

def test_l2_tushare_returns_accum_nav(monkeypatch):
    """★ L2（Tushare 降级）必须返回**累计净值**，不是单位净值。

    故障注入：把 ``fund_monitor.py`` 的 ``accum_series`` 判定改回无条件
    ``_safe_float(r.get("unit_nav"))`` → 本用例红（拿到 2.9119）。
    """
    navs = [
        {"nav_date": "2026-09-16", "unit_nav": "2.9200", "accum_nav": "4.1639"},
        {"nav_date": "2026-09-17", "unit_nav": "2.9119", "accum_nav": "4.1558"},
        {"nav_date": "2026-09-18", "unit_nav": "3.0385", "accum_nav": "4.2824"},
    ]
    _only_l2(monkeypatch, navs)

    got = _fresh(CODE, 3)
    assert got, f"L2 应返回非空序列，实际：{got!r}"
    navs_out = [r["nav"] for r in got]
    assert navs_out == pytest.approx([4.1639, 4.1558, 4.2824]), (
        f"L2 必须返回累计净值 [4.1639, 4.1558, 4.2824]，实际：{navs_out}")
    assert UNIT_NAV not in navs_out, (
        f"L2 返回了单位净值 {UNIT_NAV}（口径退回修复前）：{navs_out}")
    assert all(r["caliber"] == "accum" for r in got), (
        f"L2 标注口径必须为 accum，实际：{[r.get('caliber') for r in got]}")


def test_l2_never_picks_adj_nav(monkeypatch):
    """★ L2 取的是 ``accum_nav``（累计），**绝不是** ``adj_nav``（复权）。

    002163：unit 2.9119 / accum 4.1558 / adj 6.7802。adj_nav 是分红再投资
    复利口径，与 L1/L3 的「累计净值」不同量纲；错取它会把 1.427× 的跳变
    放大成 2.35×，比修复前更糟。

    故障注入：把取值写成 ``r.get("adj_nav")`` → 本用例红（拿到 6.7802）。
    """
    navs = [
        {"nav_date": "2026-09-16", "unit_nav": "2.9200",
         "accum_nav": "4.1639", "adj_nav": "6.7900"},
        {"nav_date": "2026-09-17", "unit_nav": "2.9119",
         "accum_nav": "4.1558", "adj_nav": "6.7802"},
    ]
    _only_l2(monkeypatch, navs)

    got = _fresh(CODE, 2)
    navs_out = [r["nav"] for r in got]
    assert ADJ_NAV not in navs_out, (
        f"L2 取到了复权净值 adj_nav={ADJ_NAV}（应为累计 {ACCUM_NAV}）：{navs_out}")
    assert navs_out == pytest.approx([4.1639, 4.1558]), navs_out


def test_l2_no_per_row_caliber_mixing(monkeypatch):
    """★ L2 不许**逐行** ``accum_nav or unit_nav`` —— 必须整段同口径。

    逐行回退会在同一条序列里混进两个量纲（前几行 4.x、后几行 2.x），
    ``calc_risk_metrics`` 会把这个人造跳空当成一次真实暴跌 —— 比"整段
    都用单位"更糟。故只要有一行缺 accum_nav，**整段**退回单位口径。

    故障注入：改成逐行 ``or`` → 本用例红（序列里同时出现 4.1639 和 2.9119）。
    """
    navs = [
        {"nav_date": "2026-09-16", "unit_nav": "2.9200", "accum_nav": "4.1639"},
        {"nav_date": "2026-09-17", "unit_nav": "2.9119", "accum_nav": None},   # 缺累计
        {"nav_date": "2026-09-18", "unit_nav": "3.0385", "accum_nav": "4.2824"},
    ]
    _only_l2(monkeypatch, navs)

    got = _fresh(CODE, 3)
    navs_out = [r["nav"] for r in got]
    assert navs_out == pytest.approx([2.9200, 2.9119, 3.0385]), (
        f"缺累计的行必须让**整段**退回单位口径，实际：{navs_out}")
    assert 4.1639 not in navs_out, (
        f"同一序列里混进了两种口径（逐行回退）：{navs_out}")
    assert {r["caliber"] for r in got} == {"unit"}, (
        f"整段口径标注必须一致为 unit，实际：{[r['caliber'] for r in got]}")


# ============================================================
# 2. 三层同口径：同一只基金三条路径必须给出同一个量纲
# ============================================================

def test_l3_em_no_per_row_caliber_mixing(monkeypatch):
    """★ L3（天天基金）不许**逐行** ``LJJZ or DWJZ`` —— 必须整段同口径。

    造一批 EM 记录，其中**恰好一条**缺 LJJZ（累计净值）。逐行回退会让这一条
    退回 DWJZ（2.9119）、其余仍是 LJJZ（4.x），同一条序列里混进两个量纲 ——
    下游 ``_get_nav_series`` / ``calc_risk_metrics`` 对相邻项做差求日收益，
    这个人造跳空（4.1639 → 2.9119，约 -30%）会被当成一次真实暴跌，污染
    相关系数、波动率、回撤。比"整段都用单位"更糟。

    故障注入：把 L3 改回逐行 ``nav_accum if ... else DWJZ`` → 本用例红
    （序列里同时出现 4.1639 和 2.9119）。
    """
    # EM 返回新→旧（最新在前），本函数内部 reversed 成升序
    items = [
        {"FSRQ": "2026-09-18", "DWJZ": "3.0385", "LJJZ": "4.2824", "JZZZL": "4.35"},
        {"FSRQ": "2026-09-17", "DWJZ": "2.9119", "LJJZ": None, "JZZZL": "-0.28"},  # 缺累计
        {"FSRQ": "2026-09-16", "DWJZ": "2.9200", "LJJZ": "4.1639", "JZZZL": "4.75"},
    ]
    _only_l3(monkeypatch, items)

    got = _fresh(CODE, 3)
    assert got, f"L3 应返回非空序列，实际：{got!r}"
    navs_out = [r["nav"] for r in got]
    assert navs_out == pytest.approx([2.9200, 2.9119, 3.0385]), (
        f"缺累计的那条必须让**整段**退回单位口径，实际：{navs_out}")
    assert 4.1639 not in navs_out and 4.2824 not in navs_out, (
        f"同一序列里混进了两种口径（逐行回退）：{navs_out}")
    assert {r["caliber"] for r in got} == {"unit"}, (
        f"整段口径标注必须一致为 unit，实际：{[r['caliber'] for r in got]}")


def test_l3_prescan_covers_whole_window(monkeypatch):
    """★ 口径判定必须预扫**完整的 ``all_items``**，不能只扫截取后的 days 条。

    构造：EM 拉到 3 条，``days=2`` 只保留最近 2 条（这两条都有 LJJZ），但
    **最早那条缺 LJJZ**。若只按截取后的 2 条判定 → 整段误升累计（4.x）；
    按完整拉取窗口判定 → 整段退回单位（2.x）。

    故障注入：把预扫挪到 ``result[-days:]`` 上做 → 本用例红（拿到 4.x）。
    """
    items = [
        {"FSRQ": "2026-09-18", "DWJZ": "3.0385", "LJJZ": "4.2824", "JZZZL": "4.35"},
        {"FSRQ": "2026-09-17", "DWJZ": "2.9119", "LJJZ": "4.1558", "JZZZL": "-0.28"},
        {"FSRQ": "2026-09-16", "DWJZ": "2.9200", "LJJZ": None, "JZZZL": "4.75"},  # 窗口外缺累计
    ]
    _only_l3(monkeypatch, items)

    got = _fresh(CODE, 2)
    navs_out = [r["nav"] for r in got]
    assert navs_out == pytest.approx([2.9119, 3.0385]), (
        f"窗口外缺累计也必须整段退回单位口径，实际：{navs_out}")
    assert {r["caliber"] for r in got} == {"unit"}, (
        f"整段口径标注必须一致为 unit，实际：{[r['caliber'] for r in got]}")


def test_three_layers_agree_on_caliber(monkeypatch):
    """★ 同一只基金（002163，2026-09-17）走 L1/L2/L3，nav 必须都≈4.1558。

    这是本次修复要钉死的**最终性质**：调用方拿到的 nav 不再取决于"那天
    哪个数据源活着"。任一层退回单位口径 → 本用例红。
    """
    date = "2026-09-17"

    # L1：AKShare 累计净值走势帧
    _only_l1(monkeypatch, [{"净值日期": date, "单位净值": "2.9119",
                            "累计净值": "4.1558", "日增长率": "-0.28"}])
    l1 = _fresh(CODE, 3)

    # L2：Tushare
    _only_l2(monkeypatch, [{"nav_date": date, "unit_nav": "2.9119",
                            "accum_nav": "4.1558"}])
    l2 = _fresh(CODE, 3)

    # L3：天天基金（EM 返回新→旧，本函数 reversed 成升序）
    _only_l3(monkeypatch, [{"FSRQ": date, "DWJZ": "2.9119",
                            "LJJZ": "4.1558", "JZZZL": "-0.28"}])
    l3 = _fresh(CODE, 3)

    for name, got in (("L1", l1), ("L2", l2), ("L3", l3)):
        assert got, f"{name} 应返回非空序列，实际：{got!r}"
        assert float(got[-1]["nav"]) == pytest.approx(ACCUM_NAV), (
            f"{name} 的最新净值应为累计口径 {ACCUM_NAV}，实际：{got[-1]['nav']}")
        assert got[-1]["caliber"] == "accum", (
            f"{name} 标注口径应为 accum，实际：{got[-1]['caliber']}")


def test_l1_accum_preferred_over_unit(monkeypatch):
    """正向保护：L1 累计走势帧同时含两列时，取**累计**（勿被"统一"成单位）。

    与 ``test_briefing_caliber_unit_nav.py::
    test_get_fund_nav_history_still_returns_accum_nav`` 同向，本文件再钉一次，
    防止本次修复把 L1 一起改坏。
    """
    _only_l1(monkeypatch, [
        {"净值日期": "2026-09-16", "单位净值": "2.9200", "累计净值": "4.1639",
         "日增长率": "4.75"},
        {"净值日期": "2026-09-17", "单位净值": "2.9119", "累计净值": "4.1558",
         "日增长率": "-0.28"},
    ])
    got = _fresh(CODE, 3)
    navs_out = [r["nav"] for r in got]
    assert ACCUM_NAV in navs_out, f"L1 应返回累计净值，实际：{navs_out}"
    assert UNIT_NAV not in navs_out, f"L1 被改成单位口径：{navs_out}"


def test_l1_unit_degradation_is_annotated(monkeypatch):
    """L1 累计走势取不到、降级到单位走势时，必须**如实标注** caliber="unit"。

    本修复**不改**这一层的数值（取不到累计就是取不到，硬凑才是错），
    只补标注，让调用方知道自己拿到的是单位口径。
    """

    def _fake_stocks(**kwargs):
        if kwargs.get("indicator") == "累计净值走势":
            return _FakeDF([])          # 累计取不到 → 触发降级
        return _FakeDF([{"净值日期": "2026-09-17", "单位净值": "2.9119",
                         "日增长率": "-0.28"}])

    monkeypatch.setattr("infra.data_source.market.stocks.get_fund_nav_history",
                        _fake_stocks)
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    _l3_down(monkeypatch)

    got = _fresh(CODE, 3)
    assert got, f"L1 降级到单位走势应仍返回数据，实际：{got!r}"
    assert float(got[-1]["nav"]) == pytest.approx(UNIT_NAV), got
    assert got[-1]["caliber"] == "unit", (
        f"降级到单位走势必须标注 caliber=unit，实际：{got[-1]['caliber']}")


# ============================================================
# 3. 下游影响：回撤不再被降级路径改变
# ============================================================

def test_drawdown_identical_across_layers(monkeypatch):
    """★ 同一段真实走势，走 L1 / L2 / L3 算出的**最大回撤**必须一致。

    这是本次修复的**下游意义**：修复前 L2 顶上时 nav 换成单位口径，分红
    除权日会在单位序列里留一个纯记账口径的假跳空，回撤凭空多出一截。
    三层同口径后，回撤不再随数据源漂移。
    """
    # 一段含**大额分红除权**的走势（构造值，突出口径差异）：
    #   09-17 单位净值 3.50 → 2.00（分红除权，账面掉 42.9%，但**不是亏损**）
    #   09-17 累计净值 4.90 → 4.85（真实只跌 1.0%）
    # 用单位口径算回撤 = 42.86%（假暴跌）；用累计口径 = 1.02%（真实）。
    seq = [
        ("2026-09-14", "3.3000", "4.7000"),
        ("2026-09-15", "3.4000", "4.8000"),
        ("2026-09-16", "3.5000", "4.9000"),   # 窗口高点
        ("2026-09-17", "2.0000", "4.8500"),   # 分红除权日
        ("2026-09-18", "2.0500", "4.9000"),
        ("2026-09-21", "2.1000", "4.9500"),
    ]

    def _risk_of(got: list) -> float:
        risk = fund_monitor.calc_risk_metrics(got)
        assert risk.get("maxDrawdown") is not None, f"回撤不应为 None：{risk}"
        return round(float(risk["maxDrawdown"]) * 100, 2)

    _only_l1(monkeypatch, [{"净值日期": d, "单位净值": u, "累计净值": a,
                            "日增长率": "0.0"} for d, u, a in seq])
    dd_l1 = _risk_of(_fresh(CODE, 10))

    _only_l2(monkeypatch, [{"nav_date": d, "unit_nav": u, "accum_nav": a}
                           for d, u, a in seq])
    dd_l2 = _risk_of(_fresh(CODE, 10))

    _only_l3(monkeypatch, [{"FSRQ": d, "DWJZ": u, "LJJZ": a, "JZZZL": "0.0"}
                           for d, u, a in reversed(seq)])
    dd_l3 = _risk_of(_fresh(CODE, 10))

    assert dd_l1 == pytest.approx(dd_l2), (
        f"L1 回撤 {dd_l1}% 与 L2 回撤 {dd_l2}% 不一致（L2 口径仍偏离）")
    assert dd_l1 == pytest.approx(dd_l3), (
        f"L1 回撤 {dd_l1}% 与 L3 回撤 {dd_l3}% 不一致")
    # 单位口径会算出 ~42.86% 的假回撤（3.50→2.00 只是除权），累计口径 1.02%
    assert dd_l1 == pytest.approx(1.02, abs=0.05), (
        f"累计口径的真实回撤应≈1.02%，实际 {dd_l1}%")
    assert dd_l1 < 5.0, (
        f"分红除权被误判成暴跌：回撤 {dd_l1}%（单位口径会给出 ~42.86%）")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rfEX"]))
