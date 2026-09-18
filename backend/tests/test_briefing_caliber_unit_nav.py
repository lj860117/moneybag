"""晨报「组合温度计」口径回归测试 —— 现净值必须用**单位净值**（P0）

事故背景（已 100% 定位根因）
---------------------------
企微晨报的「📊 组合温度计」把 ``get_fund_nav_history`` 返回的**累计净值**
当成"现净值"去算市值和涨跌幅，而用户的**成本净值 costNav 是单位净值口径**
（用户按「金额 / 份额」录入，如 200 / 45.23 = 4.4218）。对有分红历史的基金，
累计净值远高于单位净值，市值被**虚增 accum/unit 倍**。

生产实测真值（天天基金 / Tushare fund_nav，unit_nav / accum_nav）::

    基金                          单位净值   累计净值   accum/unit
    163406 兴全合润混合A          2.2401    8.5191     3.803×
    100038 富国沪深300指数增强A   1.8750    2.4610     1.3125×
    002163 东方惠新灵活配置混合C   2.9119    4.1558     1.4272×
    009708 工银新兴制造混合C      4.5892    4.5892     1.0×

生产持仓（``fund_holdings_BuLuoGeLi.json``，costNav × shares）::

    009708 costNav=4.4218 shares=45.23（成本 200.0）
    100038 costNav=2.04   shares=49.02（成本 100.0）
    163406 costNav=2.6273 shares=117.99（成本 310.0）

  - 用**累计净值**复算 = 成本 ¥609.99 / 市值 ¥1333.38 / **+118.59%**
    ← 与 2026-09-18 晨报存档逐字一致（"总投入 ¥610  当前市值 ¥1333
      整体浮盈 📈 +118.6%"）
  - 用**单位净值**复算 = 成本 ¥609.99 / 市值 ¥563.79 / **−7.57%**
    ← 与 ``/api/portfolio/overview?userId=BuLuoGeLi`` 实测
      ``totalMarketValue:563.79, totalCost:610.0, totalPnl:-46.21,
      totalPnlPct:-7.58`` 完全一致

即：**这个组合真实是亏损 7.58%，晨报却每天告诉用户浮盈 118.6%，虚增
¥769.58。**

修法与边界
----------
- 修**晨报生成层**（``scripts/night_worker.py::_fetch_unit_nav``）：现净值改取
  ``services.market_data.get_fund_nav`` 的**单位净值**（优先 ``official_nav``，
  回落 ``nav``），与 Web 端口径一致。
- **不动** ``services/fund_monitor.py::get_fund_nav_history`` —— 它用累计净值
  对「回撤 / 波动率 / 回测」是**正确**的（见本文件末尾的反向保护用例）。
- **不动** Web 端（``market_data`` / ``unified_networth`` / ``portfolio_overview``）
  —— 它们口径本来就是对的。

本文件是"能变红"的行为测试（不是自证绿的摆设）：
  - :func:`test_thermometer_uses_unit_nav_pin` 在修改前**必红**（改动前现净值
    取到 8.5191 → 市值 ¥1005.2）；
  - :func:`test_bulugei_real_holdings_end_to_end` 是**与 /api/portfolio/overview
    对齐的验收线**；
  - :func:`test_missing_nav_guardrail_still_intact` 证明改动**没拆掉**旧护栏；
  - :func:`test_get_fund_nav_history_still_returns_accum_nav` 反向钉住累计口径
    未被"顺手统一"改坏。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scripts.night_worker as nw  # noqa: E402

# 生产实测真值：{code: (单位净值, 累计净值)}
UNIT_NAV = {
    "163406": 2.2401,
    "100038": 1.8750,
    "002163": 2.9119,
    "009708": 4.5892,
}
ACCUM_NAV = {
    "163406": 8.5191,
    "100038": 2.4610,
    "002163": 4.1558,
    "009708": 4.5892,  # 无分红，单位==累计
}

# 生产持仓 BuLuoGeLi：{code: (成本金额, 份额, 成本净值)}
BULUOGELI_TXNS = [
    {"type": "BUY", "code": "009708", "name": "工银新兴制造混合C",
     "amount": 200.0, "shares": 45.23, "nav": 4.4218},
    {"type": "BUY", "code": "100038", "name": "富国沪深300指数增强A",
     "amount": 100.0, "shares": 49.02, "nav": 2.04},
    {"type": "BUY", "code": "163406", "name": "兴全合润混合A",
     "amount": 310.0, "shares": 117.99, "nav": 2.6273},
]


def _fake_unit_nav(unit_nav_by_code: dict):
    """构造 ``services.market_data.get_fund_nav`` 的替身。

    取值规则：``unit_nav_by_code`` 里没有该 code（或值为 None）→ 返回 ``"N/A"``
    （模拟数据源全挂）；值为 dict → 原样返回（用于注入 official_nav/date）；
    值为数值 → 包装成 ``{"nav", "official_nav", "date"}``。

    Args:
        unit_nav_by_code: {code: 单位净值} 或 {code: 完整 dict}。

    Returns:
        可直接替换 get_fund_nav 的 callable。
    """
    def _fn(code, *args, **kwargs):
        entry = unit_nav_by_code.get(code)
        if entry is None:
            return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}
        if isinstance(entry, dict):
            return entry
        return {"code": code, "nav": str(entry), "official_nav": str(entry),
                "date": "2026-09-17", "change": "0"}
    return _fn


def _fake_hist_accum(accum_nav_by_code: dict):
    """构造 ``services.fund_monitor.get_fund_nav_history`` 的替身（返回累计净值）。

    用途：即使这个"错误来源"仍然可用（返回累计净值），修正后的温度计也**不应**
    再采信它 —— 用它来证明温度计确实改走了单位净值通道。
    """
    def _fn(code, days=3, force_refresh=False):
        nv = accum_nav_by_code.get(code)
        if not nv:
            return []
        return [{"date": "2026-09-16", "nav": nv, "rate": 0.0},
                {"date": "2026-09-17", "nav": nv, "rate": 0.0}]
    return _fn


def _render_thermometer(uid, txns, unit_nav_by_code, accum_nav_by_code,
                        tmp_path, monkeypatch):
    """在隔离的 USERS_DIR 下用内存用户文件驱动 ``_build_portfolio_thermometer``。

    Args:
        uid: 用户名。
        txns: transactions 列表。
        unit_nav_by_code: ``services.market_data.get_fund_nav`` 的返回值映射。
        accum_nav_by_code: ``services.fund_monitor.get_fund_nav_history`` 的返回映射。
        tmp_path: pytest 临时目录。
        monkeypatch: pytest monkeypatch fixture。

    Returns:
        _build_portfolio_thermometer 的输出文本。
    """
    safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
    users_dir = tmp_path / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / f"{safe}.json").write_text(
        json.dumps({"userId": uid, "portfolio": {"transactions": txns}},
                   ensure_ascii=False),
        encoding="utf-8")

    monkeypatch.setenv("USERS_DIR", str(users_dir))
    monkeypatch.setattr("services.market_data.get_fund_nav",
                        _fake_unit_nav(unit_nav_by_code))
    monkeypatch.setattr("services.fund_monitor.get_fund_nav_history",
                        _fake_hist_accum(accum_nav_by_code))
    return nw._build_portfolio_thermometer(uid)


# ============================================================
# 1. 口径钉子（修改前必红）
# ============================================================

def test_thermometer_uses_unit_nav_pin(tmp_path, monkeypatch):
    """163406：现净值必须是**单位净值 2.240**，不是累计净值 8.519。

    生产真值：单位 2.2401 / 累计 8.5191（3.803×）。
    持仓：成本 310.0，份额 117.99，成本净值 2.6273。

    正确（单位口径）: 市值 = 2.2401 × 117.99 = ¥264.3，浮盈 ≈ −14.7%
    错误（累计口径）: 市值 = 8.5191 × 117.99 = ¥1005.2，浮盈 ≈ +224%

    故障注入：把 ``_build_portfolio_thermometer`` 的现净值来源改回
    ``get_fund_nav_history(...)[-1]["nav"]``，本用例立刻红 —— 本文件里
    ``get_fund_nav_history`` 的替身仍返回累计 8.5191，正是用来钉死"不许用它"。
    """
    txns = [{"type": "BUY", "code": "163406", "name": "兴全合润混合A",
             "amount": 310.0, "shares": 117.99, "nav": 2.6273}]
    out = _render_thermometer(
        "qa_caliber_pin", txns,
        {"163406": UNIT_NAV["163406"]}, {"163406": ACCUM_NAV["163406"]},
        tmp_path, monkeypatch)

    # 现净值 = 单位净值
    assert "现2.240" in out, f"现净值应为单位净值 2.240，实际：\n{out}"
    # 市值 = 单位净值 × 份额
    assert "¥264.3" in out, f"市值应为 ¥264.3（=2.2401×117.99），实际：\n{out}"
    # 绝不能出现累计口径的市值
    assert "¥1005" not in out, f"出现累计净值口径市值 1005 —— 口径没改对：\n{out}"
    # 整体浮盈用单位口径
    assert "-14.7%" in out, f"整体浮盈应为 −14.7%，实际：\n{out}"
    assert "+224" not in out, f"出现累计口径浮盈 +224% —— 口径没改对：\n{out}"


# ============================================================
# 2. 生产真实持仓端到端（与 /api/portfolio/overview 对齐的验收线）
# ============================================================

def test_bulugei_real_holdings_end_to_end(tmp_path, monkeypatch):
    """BuLuoGeLi 三只真实持仓 → 总投入 ¥610 / 市值 ¥564 / 浮盈 📉 −7.6%。

    验收线：与 ``/api/portfolio/overview?userId=BuLuoGeLi`` 实测
    ``totalCost:610.0, totalMarketValue:563.79, totalPnlPct:-7.58`` 对齐。

    错误（累计口径）会得到 成本 ¥610 / 市值 ¥1333 / +118.6% —— 与晨报
    存档里的错误数字一致，本用例据此判红。
    """
    out = _render_thermometer(
        "BuLuoGeLi", BULUOGELI_TXNS, UNIT_NAV, ACCUM_NAV, tmp_path, monkeypatch)

    assert "总投入 ¥610" in out, f"总投入应为 ¥610，实际：\n{out}"
    assert "当前市值 ¥564" in out, f"当前市值应为 ¥564，实际：\n{out}"
    assert "整体浮盈 📉 -7.6%" in out, f"整体浮盈应为 📉 −7.6%，实际：\n{out}"
    # 累计口径的错误数字绝不能出现
    assert "¥1333" not in out, f"出现累计口径市值 1333（虚增）—— 口径没改对：\n{out}"
    assert "+118.6%" not in out, f"出现累计口径浮盈 +118.6%（虚增）—— 口径没改对：\n{out}"


# ============================================================
# 3. 净值缺失护栏不被破坏
# ============================================================

def test_missing_nav_guardrail_still_intact(tmp_path, monkeypatch, capsys):
    """get_fund_nav 返回 "N/A" 时，仍走「净值缺失 ⚠️」分支，不静默顶替。

    这是 v9.9.12 FIX-H2 的护栏：净值取不到 → 显式标注 + 告警日志 + 市值按
    成本计 + float_pct 置 None。本次改动只换了净值**来源**，不得拆掉它。
    """
    txns = [{"type": "BUY", "code": "000004", "name": "净值缺失基金",
             "amount": 1000.0, "shares": 1000.0, "nav": 1.0}]
    # get_fund_nav 返回 "N/A"（unit_nav_by_code 里没有 000004）
    out = _render_thermometer(
        "qa_caliber_missing", txns, {}, {"000004": 1.0}, tmp_path, monkeypatch)

    assert "净值缺失" in out, f"净值取不到时必须显式标注缺失，实际：\n{out}"
    assert "⚠️ 1 只基金净值缺失" in out, f"缺失汇总行应出现，实际：\n{out}"
    # 市值按成本计（1000.0）
    assert "¥1000.0（按成本计）" in out, f"缺失时应按成本计市值，实际：\n{out}"
    # 不得把"取不到数"显示成"持平 0.0%"
    assert "▲0.0%" not in out, f"不得把取不到数伪装成持平，实际：\n{out}"

    captured = capsys.readouterr()
    assert "[HOLDINGS]" in captured.out and "000004" in captured.out, (
        f"净值缺失必须打 [HOLDINGS] 告警日志，实际 stdout：\n{captured.out}")


def test_official_nav_preferred_over_nav(tmp_path, monkeypatch):
    """优先取 official_nav（dwjz 单位净值），回落 nav。

    交易时段 ``get_fund_nav`` 的 ``nav`` 可能是盘中估值，而 ``official_nav``
    永远是 T-1 官方单位净值 —— 温度计应优先后者。这里让两者不同，断言用了
    official_nav。
    """
    txns = [{"type": "BUY", "code": "100038", "name": "富国沪深300指数增强A",
             "amount": 100.0, "shares": 49.02, "nav": 2.04}]
    out = _render_thermometer(
        "qa_caliber_official", txns,
        {"100038": {"code": "100038", "nav": "9.99", "official_nav": "1.875",
                    "date": "2026-09-17", "change": "0"}},
        {"100038": ACCUM_NAV["100038"]},
        tmp_path, monkeypatch)

    assert "现1.875" in out, f"应优先用 official_nav=1.875，实际：\n{out}"
    assert "现9.99" not in out, f"不应使用 nav=9.99（盘中估值），实际：\n{out}"


# ============================================================
# 4. 反向保护：累计口径（回撤/波动率/回测）未被改动
# ============================================================

class _FakeDF:
    """最小 DataFrame 替身，满足 ``df.empty`` / ``df.tail(n)`` / ``iterrows()``。

    ``fund_monitor.get_fund_nav_history`` 只用到这三个成员，无需依赖 pandas。
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


def test_get_fund_nav_history_still_returns_accum_nav(monkeypatch):
    """反向保护：``get_fund_nav_history`` 仍返回**累计净值**，勿"顺手统一"。

    ``get_fund_nav_history`` 用累计净值对「回撤 / 波动率 / 回测」是正确的
    （``scripts/stock_monitor_cron.py:1389`` 就是这种用法），本次 P0 修复
    **只换晨报侧的来源**，不得改它。这里喂一份同时含「累计净值」与「单位净值」
    的数据，断言取到的是**累计净值**。

    故障注入：把 ``fund_monitor.py:493`` 改成只取「单位净值」，本用例立刻红。
    """
    from services import fund_monitor

    rows = [
        {"净值日期": "2026-09-16", "单位净值": "2.10", "累计净值": "3.10",
         "日增长率": "0.5"},
        {"净值日期": "2026-09-17", "单位净值": "2.2401", "累计净值": "8.5191",
         "日增长率": "0.8"},
    ]

    # 函数内 `from infra.data_source.market.stocks import get_fund_nav_history`
    # 是**函数级 import**，patch 数据源本体才生效（与既有测试同法）。
    monkeypatch.setattr(
        "infra.data_source.market.stocks.get_fund_nav_history",
        lambda **kwargs: _FakeDF(rows))
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    # 用独立 cache_key 且 force_refresh，避免模块级 _nav_cache 串味
    fund_monitor._nav_cache._data.pop("163406_3", None)
    got = fund_monitor.get_fund_nav_history("163406", days=3, force_refresh=True)

    assert got, f"应返回非空序列，实际：{got!r}"
    navs = [r["nav"] for r in got]
    assert 8.5191 in navs, f"必须返回累计净值 8.5191，实际：{navs}"
    assert 2.2401 not in navs, f"不得返回单位净值 2.2401（口径被改坏）：{navs}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rfEX"]))
