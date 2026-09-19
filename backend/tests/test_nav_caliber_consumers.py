"""净值口径在**消费方**的两处误用回归测试（v9.9.57）

消费方 A：``api/chat_fc.py::_tool_get_fund_history``（用户直接可见）
------------------------------------------------------------------
旧实现把 ``fund_monitor.get_fund_nav_history`` 的 nav 当成「最新净值」念给用户，
而该函数三层**全是累计口径**（L1 累计走势 / L2 accum_nav / L3 LJJZ）。
002163 实测：单位净值 **2.9119**、累计净值 **4.1558** → 虚高 43%，且是
**L1 正常日就错**，不是降级才错。

修法分工（与项目权威口径一致）：
  * 最新净值 → **单位净值**，权威来源 ``services.market_data.get_fund_nav``
    （v9.9.54/56 已修好：显式拒绝累计列，宁可 "N/A" 也不给错口径）；
  * 区间涨跌 / 当前回撤 → **累计口径**（回撤是单序列内峰谷比，累计净值在分红
    除权日不跳空，不会把分红误判成暴跌）；
  * **不再输出「最大净值」** —— 一句话里同时出现单位口径和累计口径两个绝对数
    （002163 上 2.9119 vs 8.7192，差 3 倍），用户读完只会问"我到底赚了多少"。
    现在只保留**一个**绝对数（单位净值），其余一律百分比（比值与量纲无关）。

消费方 B：``api/signals.py::_fetch_nav_full`` L1 逐行混口径
-----------------------------------------------------------
旧写法 ``row.get("accum_nav") or row.get("unit_nav")`` 是**逐行**回退，会在同一条
序列里混进两个量纲。下游 ``_get_nav_series`` 对相邻两项做差求日收益率，于是这个
人造跳空被当成一次 -30% 的真实日收益，污染相关系数与净值百分位。
与 ``fund_monitor.get_fund_nav_history`` 同一条铁律：**整段升累计 or 整段退回单位**。

生产实测真值（002163，天天基金 f10/lsjz，2026-09-22）：单位 2.9119 / 累计 4.1558
（另有 adj_nav 复权口径 6.7802，**不是**"累计"，不可混用）。

本文件是"能变红"的行为测试：
  - 把 chat_fc 改回「最新净值取 history[-1]['nav']」→ A 组用例立刻红；
  - 删掉 chat_fc 的口径降级风险标注（``risk_txt``）
    → :func:`test_tool_get_fund_history_flags_unit_caliber_risk` 红；
  - 把 signals 改回逐行 ``or`` → :func:`test_fetch_nav_full_no_per_row_mixing` 红；
  - 把 signals 的取值写成 ``adj_nav`` → :func:`test_fetch_nav_full_never_picks_adj_nav` 红。

⚠️ 已清理的恒真断言：:func:`test_tool_get_fund_history_drawdown_uses_accum`
里曾有 ``assert "-40.00" not in out``，但该夹具只喂一条 accum 序列，任何突变
都产不出 -40.00（恒真），已删除并在该用例 docstring 里写明它对什么负责。
"""
import re
import sys
from pathlib import Path

import pytest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.chat_fc as chat_fc  # noqa: E402
import api.signals as signals  # noqa: E402

CODE = "002163"
UNIT_NAV = 2.9119      # 002163 单位净值（生产实测）
ACCUM_NAV = 4.1558     # 002163 累计净值（生产实测）
ADJ_NAV = 6.7802       # 002163 复权净值（**不是**累计，仅用于反向钉死）

# 4 位小数的绝对净值数字（如 2.9119 / 4.1558）
_ABS_NAV_RE = re.compile(r"\d+\.\d{4}")


# ============================================================
# A. chat_fc._tool_get_fund_history —— 最新净值必须单位口径
# ============================================================

def _patch_chat(monkeypatch, unit_nav, history):
    """接管 chat_fc 的两个数据来源。

    Args:
        unit_nav: ``market_data.get_fund_nav`` 的返回（None 表示数据源取不到）。
        history: ``fund_monitor.get_fund_nav_history`` 的返回（累计口径）。
    """
    info = ({"code": CODE, "nav": "N/A", "date": "N/A", "change": "0"}
            if unit_nav is None else
            {"code": CODE, "nav": str(unit_nav), "official_nav": str(unit_nav),
             "date": "2026-09-18", "change": "0"})
    monkeypatch.setattr("services.market_data.get_fund_nav",
                        lambda code, *a, **k: info)
    monkeypatch.setattr("services.fund_monitor.get_fund_nav_history",
                        lambda code, days=30, force_refresh=False: history)


def test_tool_get_fund_history_shows_unit_nav(monkeypatch):
    """★ 用户看到的「最新净值」必须是**单位净值** 2.9119，不是累计 4.1558。

    故障注入：把最新净值改回取 ``history[-1]["nav"]``（累计口径）→ 本用例红。
    """
    history = [
        {"date": "2026-09-16", "nav": 4.1639, "rate": 4.75, "caliber": "accum"},
        {"date": "2026-09-17", "nav": 4.1558, "rate": -0.28, "caliber": "accum"},
        {"date": "2026-09-18", "nav": 4.2824, "rate": 4.35, "caliber": "accum"},
    ]
    _patch_chat(monkeypatch, UNIT_NAV, history)

    out = chat_fc._tool_get_fund_history(CODE, days=30)
    assert "2.9119" in out, f"最新净值应为单位净值 2.9119，实际输出：{out}"
    assert "4.1558" not in out, f"累计净值 4.1558 泄漏给用户（虚高 43%）：{out}"
    assert "4.2824" not in out, f"累计净值泄漏给用户：{out}"


def test_tool_get_fund_history_never_substitutes_accum(monkeypatch):
    """★ 单位净值取不到时**绝不**拿累计净值顶替，宁可说"取不到"。

    故障注入：任何"没取到就回落到 history 的 nav"的写法 → 本用例红。
    """
    history = [{"date": "2026-09-17", "nav": 4.1558, "rate": 0.0,
                "caliber": "accum"}]
    _patch_chat(monkeypatch, None, history)   # market_data 返回 N/A

    out = chat_fc._tool_get_fund_history(CODE, days=30)
    assert "4.1558" not in out, f"单位净值缺失时不许拿累计顶替：{out}"
    assert "取不到" in out, f"应如实告知用户取不到，实际：{out}"


def test_tool_get_fund_history_prints_single_scale(monkeypatch):
    """★ 一句话里只能有**一个**绝对净值数（单位口径），不得两个尺度并存。

    旧文案「最新净值 2.9119，最大净值 8.7192」在 002163 上差 3 倍 —— 用户读完
    只会问"我到底赚了多少"。现在其余指标一律用百分比表达。
    """
    history = [{"date": f"2026-09-{d:02d}", "nav": 4.1000 + i * 0.01,
                "rate": 0.1, "caliber": "accum"} for i, d in enumerate(range(1, 31))]
    _patch_chat(monkeypatch, UNIT_NAV, history)

    out = chat_fc._tool_get_fund_history(CODE, days=30)
    abs_navs = _ABS_NAV_RE.findall(out)
    assert abs_navs == ["2.9119"], (
        f"输出里应只有最新净值这一个绝对净值数（且为 2.9119），实际：{abs_navs} → {out}")
    assert "最大净值" not in out, f"「最大净值」会引入第二个尺度：{out}"
    assert "%" in out, f"涨跌/回撤应以百分比表达：{out}"


def test_tool_get_fund_history_drawdown_uses_accum(monkeypatch):
    """★ 回撤按**累计口径**算，且标签写成「含分红」（不挂降级风险标注）。

    构造一段含大额分红除权的走势（累计口径）：
      累计 [4.70, 4.80, 4.90, 4.85, 4.90, 4.80] → 回撤 (4.80-4.90)/4.90 = -2.04%
      同期单位 [3.30, 3.40, 3.50, 2.00, 2.05, 2.10] → 会算出 -40.00%（假暴跌）

    ⚠️ 夹具自述（诚实版）：本用例**只喂了一条 accum 序列**，代码根本没有
    单位序列可切，所以旧版里那句 ``assert "-40.00" not in out`` 是**恒真**
    的 —— 穷举"取 min / 取 max / 取 navs[0] / 取均值 / ×0.7"五种突变都产不出
    -40.00（最大只到 -4.08%）。它永远不会红，已删除。

    本用例真正的约束力：
      * ``-2.04`` 必须在输出里 → 回撤若改用单位净值单点算、或峰值取错就红；
      * 序列是 accum，就**不得**挂口径降级的风险标注「仅供参考」。

    单位口径降级路径（history 的 caliber=unit）由
    :func:`test_tool_get_fund_history_flags_unit_caliber_risk` 覆盖。
    """
    accum = [4.70, 4.80, 4.90, 4.85, 4.90, 4.80]
    history = [{"date": f"2026-09-{14 + i}", "nav": v, "rate": 0.0,
                "caliber": "accum"} for i, v in enumerate(accum)]
    _patch_chat(monkeypatch, UNIT_NAV, history)

    out = chat_fc._tool_get_fund_history(CODE, days=30)
    assert "-2.04" in out, f"回撤应按累计口径算得 -2.04%，实际：{out}"
    assert "含分红" in out, f"序列是 accum，标签应为「含分红」，实际：{out}"
    assert "仅供参考" not in out, (
        f"序列是 accum，不该挂口径降级的风险标注：{out}")


def test_tool_get_fund_history_flags_unit_caliber_risk(monkeypatch):
    """★ history 降级成**单位口径**时必须**如实标注风险**，不能只换标签。

    ``fund_monitor.get_fund_nav_history`` 允许口径降级：L1 累计走势取不到会
    降到单位走势、L2/L3 缺累计字段也会整段退回单位（v9.9.57 起这些路径都
    标了 ``caliber="unit"``）。此时回撤**静默变成单位口径** —— 分红除权日在
    单位序列里是一个纯记账的跳空，会被显示成一次暴跌（本夹具：3.50 → 2.00
    除权，回撤算出 -40.00%，而真实只跌约 1%）。

    我们不换算（换算需要分红明细，没有），只如实标注。

    故障注入：删掉 ``risk_txt`` 那段标注逻辑 → 本用例红（输出里没有
    「分红除权」/「仅供参考」）。
    """
    unit = [3.30, 3.40, 3.50, 2.00, 2.05, 2.10]   # 09-17 分红除权：3.50 → 2.00
    history = [{"date": f"2026-09-{14 + i}", "nav": v, "rate": 0.0,
                "caliber": "unit"} for i, v in enumerate(unit)]
    _patch_chat(monkeypatch, UNIT_NAV, history)

    out = chat_fc._tool_get_fund_history(CODE, days=30)
    assert "分红除权" in out, f"单位口径必须如实标注分红除权风险，实际：{out}"
    assert "仅供参考" in out, f"单位口径必须提醒用户仅供参考，实际：{out}"
    assert "单位净值口径" in out, f"标签应写明是单位净值口径，实际：{out}"
    assert "含分红" not in out, (
        f"单位口径不该再标「含分红」（那是累计口径的标签）：{out}")


# ============================================================
# B. signals._fetch_nav_full —— L1 不得逐行混口径
# ============================================================

def _rows(n: int = 25, missing_accum_idx: set | None = None,
          with_adj: bool = False) -> list:
    """构造 Tushare fund_nav 的行（升序）。

    Args:
        n: 行数（须 ≥ ``_NAV_FULL_MIN``，否则会被当成"未命中"继续降级）。
        missing_accum_idx: 这些下标的 accum_nav 置 None（模拟 Tushare 缺字段）。
        with_adj: 是否附带 adj_nav（复权口径，应为干扰项）。
    """
    missing = missing_accum_idx or set()
    out = []
    for i in range(n):
        row = {"nav_date": f"202608{20 + i:02d}",
               "unit_nav": str(round(2.9000 + i * 0.0010, 4)),
               "accum_nav": None if i in missing else str(round(4.1000 + i * 0.0010, 4))}
        if with_adj:
            row["adj_nav"] = str(round(6.7802 + i * 0.0010, 4))
        out.append(row)
    return out


def _patch_signals_l1(monkeypatch, rows: list):
    """只让 L1（Tushare）可用，并清掉模块级按天缓存。"""
    monkeypatch.setattr(signals, "_nav_full_cache", {})
    monkeypatch.setattr(signals, "_nav_full_date", "")
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: True, raising=False)
    monkeypatch.setattr("services.tushare_data.get_fund_nav",
                        lambda code, days=60: {"available": True, "navs": rows},
                        raising=False)
    # L2/L3 断掉，确保测的确实是 L1
    monkeypatch.setattr("services.fund_monitor.get_fund_nav_history",
                        lambda *a, **k: [])
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))


def test_fetch_nav_full_prefers_accum_when_complete(monkeypatch):
    """每行都有 accum_nav → **整段**用累计口径（与 L2 AKShare / L3 EM LJJZ 一致）。"""
    _patch_signals_l1(monkeypatch, _rows())
    vals = signals._fetch_nav_full(CODE)

    assert len(vals) == 25, f"应取到 25 条，实际 {len(vals)}"
    assert vals[0] == pytest.approx(4.1000), f"应为累计口径 4.1000 起，实际 {vals[0]}"
    assert all(v > 4.0 for v in vals), f"整段应为累计口径（>4.0），实际：{vals[:5]}"
    assert 2.9 not in vals, f"不该出现单位净值：{vals[:5]}"


def test_fetch_nav_full_no_per_row_mixing(monkeypatch):
    """★ 只要有一行缺 accum_nav → **整段**退回单位口径，绝不逐行混。

    逐行 ``accum_nav or unit_nav`` 会让序列前半段 4.x、缺的那行变成 2.x，
    下游 ``_get_nav_series`` 把这个人造跳空当成一次真实的暴跌 —— 比"整段
    都用单位"更糟。

    故障注入：改成逐行 ``or`` → 本用例红（序列里同时出现 4.1 和 2.9）。
    """
    rows = _rows(missing_accum_idx={10})
    _patch_signals_l1(monkeypatch, rows)
    vals = signals._fetch_nav_full(CODE)

    assert len(vals) == 25, f"应取到 25 条，实际 {len(vals)}"
    assert all(v < 3.5 for v in vals), (
        f"缺累计的行必须让整段退回单位口径，实际：{vals[:12]}")
    assert vals[10] == pytest.approx(2.9100), f"第 11 行应为单位净值 2.9100，实际 {vals[10]}"
    assert not any(v > 4.0 for v in vals), (
        f"同一序列里混进了两种口径（逐行回退）：{vals}")


def test_fetch_nav_full_never_picks_adj_nav(monkeypatch):
    """★ 取的是 accum_nav（累计），**绝不**是 adj_nav（复权，6.7802）。

    故障注入：把取值写成 ``row.get("adj_nav")`` → 本用例红（拿到 6.78x）。
    """
    _patch_signals_l1(monkeypatch, _rows(with_adj=True))
    vals = signals._fetch_nav_full(CODE)

    assert not any(abs(v - ADJ_NAV) < 0.5 for v in vals), (
        f"取到了复权净值 adj_nav≈{ADJ_NAV}（应为累计 {ACCUM_NAV}）：{vals[:5]}")
    assert vals[0] == pytest.approx(4.1000), vals[:5]


# ============================================================
# C. signals._fetch_nav_full_em —— L3（天天基金）不得逐行混口径
# ============================================================

class _FakeEMResponse:
    """``requests.get`` 的替身：只提供 EM 降级分支用到的 ``.text``。"""

    def __init__(self, payload: str):
        self.text = payload


def _em_pages(items: list):
    """天天基金 f10/lsjz 翻页替身：第 1 页给数据，第 2 页给空（终止翻页）。

    Args:
        items: 第 1 页返回的 LSJZList（EM 顺序：最新在前）。
    """
    import json as _json

    def _get(*a, **k):
        page = (k.get("params") or {}).get("pageIndex", 1)
        payload = items if page == 1 else []
        return _FakeEMResponse(
            "x(" + _json.dumps({"Data": {"LSJZList": payload}}, ensure_ascii=False) + ")")

    return _get


def test_fetch_nav_full_em_prefers_accum_when_complete(monkeypatch):
    """每条都有 LJJZ → 整段用**累计**口径，与 L1（accum_nav）/ L2 一致。"""
    monkeypatch.setattr("requests.get", _em_pages([
        {"FSRQ": "2026-09-18", "DWJZ": "3.0385", "LJJZ": "4.2824"},
        {"FSRQ": "2026-09-17", "DWJZ": "2.9119", "LJJZ": "4.1558"},
    ]))
    vals = signals._fetch_nav_full_em(CODE)
    assert vals == pytest.approx([4.1558, 4.2824]), (
        f"EM 应整段返回累计口径（升序），实际：{vals}")
    assert not any(v < 3.0 for v in vals), f"混进了单位口径：{vals}"


def test_fetch_nav_full_em_no_per_row_mixing(monkeypatch):
    """★ 恰好一条缺 LJJZ → **整段**退回单位口径，不是"那一条退回、其余仍是累计"。

    逐行 ``LJJZ or DWJZ`` 会让序列变成 [4.1639, 2.9119, 4.2824]，下游
    ``_get_nav_series`` 对相邻项做差，那两个约 -30% / +47% 的人造跳空会被
    当成真实日收益，污染相关系数与净值百分位。

    故障注入：把 ``_fetch_nav_full_em`` 改回逐行 ``or`` → 本用例红
    （序列里同时出现 4.1639 和 2.9119）。
    """
    monkeypatch.setattr("requests.get", _em_pages([
        {"FSRQ": "2026-09-18", "DWJZ": "3.0385", "LJJZ": "4.2824"},
        {"FSRQ": "2026-09-17", "DWJZ": "2.9119", "LJJZ": None},   # 恰好一条缺累计
        {"FSRQ": "2026-09-16", "DWJZ": "2.9200", "LJJZ": "4.1639"},
    ]))
    vals = signals._fetch_nav_full_em(CODE)
    assert vals == pytest.approx([2.9200, 2.9119, 3.0385]), (
        f"缺累计的那条必须让整段退回单位口径，实际：{vals}")
    assert not any(v > 4.0 for v in vals), (
        f"同一序列里混进了两种口径（逐行回退）：{vals}")


def test_nav_series_has_no_fake_gap(monkeypatch):
    """★ 下游日收益率序列里不得出现人造跳空（相关系数/百分位的输入）。

    缺一整行 accum_nav 时整段退回单位 → 日收益率是平滑的单位净值日涨跌，
    不会出现 -30% 这种纯属混口径的极值。
    """
    rows = _rows(missing_accum_idx={10})
    _patch_signals_l1(monkeypatch, rows)
    monkeypatch.setattr(signals, "_nav_series_cache", {})
    monkeypatch.setattr(signals, "_nav_series_cache_date", "")

    returns = signals._get_nav_series(CODE, days=60)
    assert len(returns) >= 20, f"日收益率序列过短：{len(returns)}"
    worst = min(returns)
    assert worst > -0.10, (
        f"日收益率出现 {worst:.2%} 的极值 —— 疑似逐行混口径造成的人造跳空")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rfEX"]))
