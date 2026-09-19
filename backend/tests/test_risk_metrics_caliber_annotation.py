"""``calc_risk_metrics`` / ``detect_fund_alerts`` **消费 caliber 字段**的回归测试

背景（v9.9.59）
--------------
``services.fund_monitor.get_fund_nav_history`` 自 v9.9.57 起给每条记录带
``caliber`` 字段（``"accum"`` / ``"unit"``），但**全仓只有它自己写、chat_fc.py
读**，其余调用方一律视而不见。后果是：口径降级到单位净值时，分红除权日会在
序列里留一个纯记账的假跳空，回撤被凭空放大，而调用方和最终用户都看不出来
—— 字段埋了等于没埋。

002163 实测三种净值不是一个量纲：单位 2.9119 / 累计 4.1558 / 复权 6.7802。
本轮铁律：只认 accum，**绝不认 adj**；也**绝不做 unit→accum 换算**（那需要
逐笔分红明细，我们没有）。所以下游能做的只有一件事：**如实标注**。

本文件锁的就是"标注真的出现、且只在该出现时出现"。

故障注入（必须能变红）
----------------------
  - 把 ``fund_monitor.resolve_nav_caliber`` 改成恒返回 ``("accum", None)``
    → ``test_unit_caliber_sequence_is_annotated`` /
      ``test_drawdown_alert_carries_note_only_when_degraded`` 立刻红；
  - 把 ``detect_fund_alerts`` 里的 ``main + sub + caliber_note`` 改回
    ``main + sub`` → ``test_drawdown_alert_carries_note_only_when_degraded``
    立刻红（口径降级时用户又看不到任何提示）；
  - 把 ``calc_risk_metrics`` 返回里的 ``navCaliber`` / ``caliberWarning``
    两行删掉 → 上面两条 + ``test_short_series_still_reports_caliber`` 立刻红；
  - 把 ``unknown`` 也配上告警（凭空告警）
    → ``test_unknown_caliber_is_not_claimed`` 立刻红。

反向守卫（避免"无条件标注"式恒真）
----------------------------------
``test_accum_caliber_sequence_has_no_warning`` 与
``test_drawdown_alert_carries_note_only_when_degraded`` 的 accum 分支共同钉死：
权威累计口径下 ``caliberWarning`` 必须是 ``None``、推送文案必须**逐字不变**。

运行方式（本地，务必用这条）::

    cd backend && env -u PYTHONPATH \\
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \\
        -m pytest tests/test_risk_metrics_caliber_annotation.py -v -rfEX
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import fund_monitor  # noqa: E402
from services.fund_monitor import (  # noqa: E402
    NAV_CALIBER_ACCUM,
    NAV_CALIBER_MIXED,
    NAV_CALIBER_UNIT,
    NAV_CALIBER_UNKNOWN,
    calc_risk_metrics,
    detect_fund_alerts,
    resolve_nav_caliber,
)

CODE = "002163"

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

# 含**大额分红除权**的走势（构造值，突出口径差异）：
#   09-17 单位净值 3.50 → 2.00（分红除权，账面掉 42.9%，但**不是亏损**）
#   09-17 累计净值 4.90 → 4.85（真实只跌 1.0%）
_DIVIDEND_UNIT = [3.30, 3.40, 3.50, 2.00, 2.05, 2.10]
_DIVIDEND_ACCUM = [4.70, 4.80, 4.90, 4.85, 4.90, 4.95]

# 自然回撤（无分红）：5.00 → 4.50 = -10%，越过 5% 预警门槛。
# 用于反向用例 —— accum 口径下**确实**会产出回撤预警，但不得带口径标注。
_PLAIN_VALUES = [5.00, 4.90, 4.80, 4.50, 4.60, 4.70]


def _build_series(values: list, caliber: str | None,
                  start_date: str = "2026-09-14") -> list:
    """按「跳过周末」规则摊出 calc_risk_metrics 期望的入参形态。

    Args:
        values: 按时间升序的净值。
        caliber: 写进每条记录的口径；``None`` 表示**不写**该键（模拟 v9.9.57
            之前的老缓存 / 第三方构造的序列）。
        start_date: 起始日期 "YYYY-MM-DD"。

    Returns:
        ``[{"date", "nav", "rate", "caliber"?}, ...]``

    ``rate`` 键**必须存在**：calc_risk_metrics 里是
    ``rates = [n["rate"] for n in nav_list if n["rate"] is not None]``，
    下标访问，缺键直接 KeyError。
    """
    out: list = []
    day = datetime.strptime(start_date, "%Y-%m-%d")
    prev: float | None = None
    for v in values:
        while day.weekday() >= 5:  # 5=周六 6=周日
            day += timedelta(days=1)
        rate = 0.0 if prev in (None, 0) else round((v - prev) / prev * 100, 4)
        item = {"date": day.strftime("%Y-%m-%d"), "nav": v, "rate": rate}
        if caliber is not None:
            item["caliber"] = caliber
        out.append(item)
        prev = v
        day += timedelta(days=1)
    return out


def _drawdown_alerts(risk: dict, code: str = CODE) -> list:
    """取 detect_fund_alerts 产出的回撤类预警。"""
    return [a for a in detect_fund_alerts(code, {}, risk)
            if a.get("type") == "drawdown"]


# ============================================================
# 1. 正向：unit 口径必须被如实标注
# ============================================================

def test_unit_caliber_sequence_is_annotated():
    """喂一条 caliber="unit" 的序列 → metrics 必须如实标注口径风险。

    这是本轮的核心缺口：修复前 calc_risk_metrics 拿到 unit 序列照算不误，
    分红除权日被当成一次真实暴跌（3.50→2.00，回撤 42.86%），而返回的 dict
    里**没有任何一个字段**能告诉调用方"这个数可能含除权"。
    """
    navs = _build_series(_DIVIDEND_UNIT, NAV_CALIBER_UNIT)
    risk = calc_risk_metrics(navs)

    assert risk["navCaliber"] == NAV_CALIBER_UNIT, (
        f"unit 序列必须自证 navCaliber=unit，实际 {risk['navCaliber']!r}")

    warning = risk["caliberWarning"]
    assert warning, (
        "unit 口径下 caliberWarning 不得为空 —— 回撤里那个 42.86% 是分红除权"
        "造出来的假暴跌，调用方必须能看出来")
    assert "除权" in warning, (
        f"告警文案必须点明「分红除权」，否则用户仍会以为是真亏：{warning!r}")


def test_unit_caliber_drawdown_is_indeed_inflated():
    """佐证：同一只基金的真实走势，unit 口径算出的回撤确实被除权放大。

    这条不是标注本身，而是**为什么必须标注** —— 累计口径 1.02%，单位口径
    42.86%，差 42 倍。不标注就等于把 1% 的波动当成 42% 的暴跌推给用户。
    """
    unit_risk = calc_risk_metrics(_build_series(_DIVIDEND_UNIT, NAV_CALIBER_UNIT))
    accum_risk = calc_risk_metrics(_build_series(_DIVIDEND_ACCUM, NAV_CALIBER_ACCUM))

    assert unit_risk["maxDrawdown"] == pytest.approx(0.4286, abs=1e-3), (
        f"单位口径回撤应≈42.86%（除权假暴跌），实际 {unit_risk['maxDrawdown']}")
    assert accum_risk["maxDrawdown"] == pytest.approx(0.0102, abs=1e-3), (
        f"累计口径回撤应≈1.02%（真实），实际 {accum_risk['maxDrawdown']}")


# ============================================================
# 2. 反向：accum 口径**不得**出现标注（防"无条件标注"式恒真）
# ============================================================

def test_accum_caliber_sequence_has_no_warning():
    """caliber="accum" 时 caliberWarning 必须是 None。

    反向守卫：只在 unit/mixed 时告警才是"守卫"，若 accum 也告警，说明写成了
    无条件标注 —— 那又是一个恒真断言，等于没守卫。
    """
    navs = _build_series(_PLAIN_VALUES, NAV_CALIBER_ACCUM)
    risk = calc_risk_metrics(navs)

    assert risk["navCaliber"] == NAV_CALIBER_ACCUM, (
        f"accum 序列应自证 navCaliber=accum，实际 {risk['navCaliber']!r}")
    assert risk["caliberWarning"] is None, (
        f"权威累计口径不应告警，实际却带了：{risk['caliberWarning']!r}")


def test_drawdown_alert_carries_note_only_when_degraded():
    """★ 推送文案：口径降级才带标注，accum 时文案逐字不变（向后兼容）。

    这是真正让用户看见的一层 —— stock_monitor_cron.py 的「🔔 持仓预警」把
    ``alert["message"]`` 原样推到企微。改之前，用户看到
    「🔻 最大回撤 42.9%」会以为自己亏了 42.9%。
    """
    # --- 降级口径：必须带标注 ---
    unit_risk = calc_risk_metrics(_build_series(_DIVIDEND_UNIT, NAV_CALIBER_UNIT))
    unit_alerts = _drawdown_alerts(unit_risk)
    assert len(unit_alerts) == 1, (
        f"42.86% 回撤应触发 1 条回撤预警，实际 {len(unit_alerts)} 条")
    unit_msg = unit_alerts[0]["message"]
    assert "42.9%" in unit_msg, f"文案应报出回撤幅度：{unit_msg}"
    assert "口径" in unit_msg and "除权" in unit_msg, (
        f"单位口径的回撤预警必须带上「口径 / 除权」标注，否则用户会把分红"
        f"除权当成真亏损。实际文案：{unit_msg}")

    # --- 权威口径：文案不得多出任何口径字样 ---
    accum_risk = calc_risk_metrics(_build_series(_PLAIN_VALUES, NAV_CALIBER_ACCUM))
    accum_alerts = _drawdown_alerts(accum_risk)
    assert len(accum_alerts) == 1, (
        f"10% 回撤应触发 1 条回撤预警，实际 {len(accum_alerts)} 条")
    accum_msg = accum_alerts[0]["message"]
    assert "10.0%" in accum_msg, f"文案应报出回撤幅度：{accum_msg}"
    assert "口径" not in accum_msg, (
        f"累计口径的回撤文案必须逐字不变（向后兼容），却多出了口径标注：{accum_msg}")
    assert "除权" not in accum_msg, (
        f"累计口径不该提除权：{accum_msg}")


# ============================================================
# 3. 向后兼容：标注是**纯新增**，不得动既有指标
# ============================================================

def test_annotation_does_not_alter_core_metrics():
    """同一串净值，只换 caliber 标注 → 所有既有指标必须**逐位相同**。

    钉死"标注只是标注"：不得因为加了 caliber 就顺手改数值（那是静默变口径）。
    """
    base = _build_series(_PLAIN_VALUES, None)
    unit = calc_risk_metrics(_build_series(_PLAIN_VALUES, NAV_CALIBER_UNIT))
    accum = calc_risk_metrics(_build_series(_PLAIN_VALUES, NAV_CALIBER_ACCUM))
    plain = calc_risk_metrics(base)

    for key in ("maxDrawdown", "volatility", "downDays", "distFromPeak",
                "reboundFromTrough", "weekReturn", "navWindowDays",
                "ddPeakDate", "ddTroughDate", "ddPeakNav", "ddTroughNav",
                "navStartDate", "navEndDate"):
        assert unit[key] == plain[key] == accum[key], (
            f"{key} 不应随 caliber 标注变化："
            f"unit={unit[key]!r} accum={accum[key]!r} 无标注={plain[key]!r}")

    # 只有这两个键允许不同，且必须如预期
    assert (unit["navCaliber"], accum["navCaliber"]) == (
        NAV_CALIBER_UNIT, NAV_CALIBER_ACCUM)
    assert unit["caliberWarning"] and not accum["caliberWarning"]


def test_unknown_caliber_is_not_claimed():
    """序列里没有 caliber 字段 → 报 unknown，且**不**凭空告警。

    老缓存（v9.9.57 之前写入）与手写构造的序列都没有这个键。"没有标注"≠
    "是单位口径"，凭空告警会改掉既有推送文案，属于无事实依据的行为变更。
    需要"必须确认是 accum"的调用方请自行判 navCaliber != accum。
    """
    navs = _build_series(_PLAIN_VALUES, None)
    assert all("caliber" not in n for n in navs)

    risk = calc_risk_metrics(navs)
    assert risk["navCaliber"] == NAV_CALIBER_UNKNOWN, (
        f"无 caliber 字段应报 unknown，实际 {risk['navCaliber']!r}")
    assert risk["caliberWarning"] is None, (
        f"无 caliber 字段不应凭空告警，实际：{risk['caliberWarning']!r}")

    # 老调用方（如既有回归测试里手写的 risk 形态）文案不受影响
    msg = _drawdown_alerts(risk)[0]["message"]
    assert "口径" not in msg, f"unknown 不应往文案里塞口径标注：{msg}"


def test_mixed_caliber_is_annotated():
    """同一段序列里混了两种口径 —— 比整段 unit 更糟，必须显式暴露。"""
    navs = _build_series(_PLAIN_VALUES, NAV_CALIBER_ACCUM)
    navs[-1]["caliber"] = NAV_CALIBER_UNIT

    risk = calc_risk_metrics(navs)
    assert risk["navCaliber"] == NAV_CALIBER_MIXED, (
        f"混入两种口径应报 mixed，实际 {risk['navCaliber']!r}")
    assert risk["caliberWarning"], "mixed 必须告警（量纲已混，数字不可信）"
    assert "混" in risk["caliberWarning"], (
        f"告警应点明口径混用：{risk['caliberWarning']!r}")


def test_short_series_still_reports_caliber():
    """不足 5 条的早退分支也要带 navCaliber —— 口径是**输入**的属性，
    与能不能算出回撤无关，调用方应当始终能读到。"""
    short = _build_series([3.50, 2.00, 2.05], NAV_CALIBER_UNIT)
    risk = calc_risk_metrics(short)

    assert risk["maxDrawdown"] is None, "不足 5 条时不算回撤（既有行为，勿改）"
    assert risk["navCaliber"] == NAV_CALIBER_UNIT, (
        f"早退分支也应报出 unit，实际 {risk['navCaliber']!r}")
    assert risk["caliberWarning"], "早退分支同样要标注口径风险"


def test_resolve_nav_caliber_ignores_navless_rows():
    """没有 nav 的行不参与回撤计算，也不该参与口径判定。

    get_fund_nav_history 的降级路径可能产出 nav=None 的行；把它们算进来会让
    一条实际 cum 的序列被误判成 mixed。
    """
    navs = _build_series(_PLAIN_VALUES, NAV_CALIBER_ACCUM)
    navs.append({"date": "2026-09-22", "nav": None, "rate": 0.0,
                 "caliber": NAV_CALIBER_UNIT})

    caliber, warning = resolve_nav_caliber(navs)
    assert caliber == NAV_CALIBER_ACCUM, (
        f"nav=None 的行不应参与口径判定，实际判成 {caliber!r}")
    assert warning is None


# ============================================================
# 4. 端到端：生产取数链路降级 → 标注真的到达下游
# ============================================================

class _FakeDF:
    """最小 DataFrame 替身，满足 L1 用到的 ``empty`` / ``tail(n)`` / ``iterrows()``。

    与 test_fund_nav_history_caliber_consistency.py 的同名替身一致：本函数只用
    到这三个成员，无需依赖 pandas。
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


def test_end_to_end_unit_degraded_path_reaches_downstream(monkeypatch):
    """打通生产者→消费者：L1 降级到「单位净值走势」→ 下游必须带上标注。

    前几条用例是**直接构造** unit 序列，只能证明"标注逻辑对"。这条走真实
    生产链路 —— ``get_fund_nav_history`` 自己降级产出 caliber=unit，再喂给
    calc_risk_metrics —— 证明的是"字段真的被接住了"，即本轮要补的那一环。
    """
    rows = [{"净值日期": d, "单位净值": str(v), "日增长率": "0.0"}
            for d, v in zip(
                ["2026-09-14", "2026-09-15", "2026-09-16",
                 "2026-09-17", "2026-09-18", "2026-09-21"],
                _DIVIDEND_UNIT)]

    def _fake_l1(code: str = "", indicator: str = "") -> _FakeDF:
        # 累计走势取不到（生产上就是 akshare 该帧为空）→ 触发降级到单位走势
        if indicator == "累计净值走势":
            return _FakeDF([])
        return _FakeDF(rows)

    monkeypatch.setattr("infra.data_source.market.stocks.get_fund_nav_history",
                        lambda **kwargs: _fake_l1(**kwargs))

    fund_monitor._nav_cache._data.pop(f"{CODE}_10", None)
    hist = fund_monitor.get_fund_nav_history(CODE, days=10, force_refresh=True)

    assert hist, "降级到单位走势应仍有数据"
    assert {h["caliber"] for h in hist} == {NAV_CALIBER_UNIT}, (
        f"生产者应标注 unit，实际 {[h.get('caliber') for h in hist]}")

    risk = calc_risk_metrics(hist)
    assert risk["navCaliber"] == NAV_CALIBER_UNIT, (
        f"消费者必须接住生产者的 unit 标注，实际 {risk['navCaliber']!r}")
    assert risk["caliberWarning"], "端到端 unit 链路下游必须看到口径告警"

    msg = _drawdown_alerts(risk)[0]["message"]
    assert "除权" in msg, f"端到端推送文案必须带除权提示：{msg}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rfEX"]))
