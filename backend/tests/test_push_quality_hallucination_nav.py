#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报「净值核对 / 内部一致性」检查回归测试（v9.9.54，2026-09-18 修订）。

## 背景（见 daily_push_quality_check.py 顶部注释，这里只摘要点）

1. `check_hallucination()` 从上线起**每天空转**：调用处 `actual_data = {}`
   恒空 → `actual_pct is not None` 永不成立，只被如实记进 `checks_skipped`。
2. 原「检查1（基金涨跌幅）」语义本身也错：它抓的是**持仓浮盈亏率**（不是当日
   涨跌幅）。⇒ 本轮整体换成**净值核对**。
3. 原「检查2（板块涨跌幅）」正则永远匹配不上（实测 0 命中）⇒ **删除**，
   替换为纯**内部一致性**检查。

## 口径（含 2026-09-18 QA 复核修正）

- A 股/境内：晨报日期 D 的「现净值」= **严格早于 D 的最后一个交易日**的净值
  （晨报 08:30 生成时最新可得）。
- **QDII 是 T+2**：晨报 08:30 只能拿到 **D-2** 的净值，质检 22:00 却能拿到
  D-1 → 拿 D-1 硬比会**天天误报**。实测证据（生产快照，见下）：
    9-16 晨报 006555 现3.435 = 9-14 净值3.4354（不是 9-15 的 3.4270）
    9-17 晨报 005698 现2.579 = 9-15 净值2.5786（不是 9-16 的 2.6467）
  ⇒ 因此：值不等「严格早于 D 的最后一条」但命中**最近 K 个交易日**窗口时，
  判「时点差」→ 记 skipped（**不是** issue）。窗口外的值才是真·净值不符。

## fixture（全部是生产原样，未脱敏未裁剪）

- `2026-09-1{6,7,8}_briefing_LeiJiang.txt`：服务器存档。
- `nav_series_snapshot.json`：**生产** `services.fund_monitor.get_fund_nav_history`
  对 8 只各取 days=30 的**真实序列快照**（2026-09-18 只读拉取）。
  ⇒ 回归基线覆盖 **8/8 只**（含两只 QDII），不是「只手写 3 只 A 股」的假基线。

## 硬要求

- **必须能精确红**：故障注入方向见每条 docstring。
- **绝不打真实数据源**：净值一律通过 `actual_data` / `actual_data_provider`
  注入；取数函数用 monkeypatch 假序列测。
"""
import inspect
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import daily_push_quality_check as qc  # noqa: E402
import services.fund_monitor as fund_monitor  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# 生产真实净值序列快照（get_fund_nav_history days=30，8 只全量）。
NAV_SERIES = json.loads(
    (FIXTURES / "nav_series_snapshot.json").read_text(encoding="utf-8")
)
ALL_CODES = list(NAV_SERIES)
QDII_CODES = ["006555", "005698"]  # 浦银安盛全球智能科技 / 华夏全球科技先锋混合
DATES = ["2026-09-16", "2026-09-17", "2026-09-18"]


def _fixture_path(day: str) -> pathlib.Path:
    return FIXTURES / f"{day}_briefing_LeiJiang.txt"


def _fixture_text(day: str) -> str:
    return _fixture_path(day).read_text(encoding="utf-8")


def _write(tmp_path, content: str, name: str = "2026-09-18_briefing_LeiJiang.txt") -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _real_actual_data(day: str, *, recent: bool = True) -> dict:
    """用生产快照 + 真实选日逻辑构建 actual_data（等价 cron 口径）。

    Args:
        day: 晨报日期。
        recent: False 时把 ``recent`` 置空 —— 用于**故障注入**，证明「时点差
            窗口」确实是防误报的承重墙。
    """
    out = {}
    for code, rows in NAV_SERIES.items():
        expect = qc._nav_strictly_before(rows, day)
        rec = qc._recent_navs(rows, day, qc.RECENT_NAV_WINDOW) if recent else []
        if expect is None and not rec:
            continue
        out[code] = {"expect": expect, "recent": rec}
    return out


def _code_only(src: str) -> str:
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))


def _net_issues(issues: list) -> list:
    return [i for i in issues if "净值不符" in i]


def _inconsistency_issues(issues: list) -> list:
    return [i for i in issues if "内部不一致" in i]


# ============================================================
# ① 真实存档回归：对的晨报必须 0 issue，且覆盖 8/8
# ============================================================

@pytest.mark.parametrize("day", DATES)
def test_real_archive_has_zero_issue(day):
    """★ 真实晨报（数字都对）跑新逻辑必须 **0 issue**。

    这是 QA 复核定案的正确性基线：净值核对口径若与生成层不同源（比如对 QDII
    硬套「严格早于 D」），9-16/9-17 会各误报 2 条（006555 / 005698）。
    """
    issues, _skipped = qc.check_hallucination_classified(
        str(_fixture_path(day)), _real_actual_data(day)
    )
    assert issues == [], f"{day} 真实晨报不该误报，实际 {issues}"


@pytest.mark.parametrize("day", DATES)
def test_real_archive_full_coverage(day):
    """★ 覆盖必须是 **8/8**（不是「只手写 3 只 A 股」的 3/8）。

    假基线会把两只 QDII 藏进 skipped，从而永远绿 —— 这正是上一版翻车的根因。
    """
    ad = _real_actual_data(day)
    assert set(ad) == set(ALL_CODES), f"{day} 应覆盖全部 8 只，实际 {sorted(ad)}"
    assert len(ad) == 8


def test_qdii_rows_are_recorded_as_timegap_not_dropped():
    """★ QDII 的「时点差」必须**留痕**（记 skipped），不是被无声吞掉。

    9-16/9-17 里 006555 / 005698 晨报值 = D-2 净值 → 命中 recent 窗口 →
    timegap。若被静默忽略（既不 skip 也不 issue），本用例红。
    """
    for day in ("2026-09-16", "2026-09-17"):
        issues, skipped = qc.check_hallucination_classified(
            str(_fixture_path(day)), _real_actual_data(day)
        )
        assert not _net_issues(issues), issues
        for code in QDII_CODES:
            assert f"hallucination_nav_timegap:{code}" in skipped, (day, skipped)


def test_qdii_would_misfire_without_timegap_window():
    """★ 故障注入：把 recent 窗口掏空（=旧「严格早于 D」硬比口径）→ 必误报。

    证明这条窗口是防误报的承重墙，不是可有可无的宽松。9-16 应出现针对
    006555 / 005698 的「净值不符」。
    """
    issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-16")), _real_actual_data("2026-09-16", recent=False)
    )
    net = _net_issues(issues)
    assert any("006555" in i for i in net), net
    assert any("005698" in i for i in net), net


def test_parser_handles_truncated_name_row():
    """兼容历史「名称被括号吐到一半」的旧存档（• 浦银安盛全球智能科技(Q(006555)…）。"""
    rows = qc._parse_position_rows(_fixture_text("2026-09-16"))
    codes = [r["code"] for r in rows]
    assert "006555" in codes and "005698" in codes, codes


# ============================================================
# ② 故障注入 A：改「现净值」→ 必须报「净值不符」
# ============================================================

def test_inject_wrong_nav_reports_mismatch(tmp_path):
    """★ 故障注入 A：09-18 的「现4.156」→「现4.999」→ 必须报「净值不符」。

    4.999 既不是 expect（9-17=4.1558）也不在 recent 窗口内 → 真·幻觉。
    故障注入方向：净值核对若退回空转，本用例红。
    """
    day = "2026-09-18"
    base, _ = qc.check_hallucination_classified(
        str(_fixture_path(day)), _real_actual_data(day)
    )
    assert base == [], f"基线必须干净，实际 {base}"

    tampered = _fixture_text(day).replace("现4.156", "现4.999")
    assert tampered != _fixture_text(day), "前提：替换确实生效"

    issues, _ = qc.check_hallucination_classified(
        _write(tmp_path, tampered), _real_actual_data(day)
    )
    assert any("净值不符" in i and "002163" in i for i in issues), issues


def test_nav_value_present_in_window_is_not_flagged(tmp_path):
    """边界：把「现净值」改成**窗口内**的另一只真实净值 → 判时点差、**不报**。

    这是承重墙的另一半：窗口内 = 真实数据 ≠ 幻觉。改成 9-14 的 3.9593 会被
    当作时点差（skip），不该报 issue。反证窗口没被误用成「放水到 0 命中」。
    """
    day = "2026-09-18"
    # 002163 的 recent 窗口（<9-18 的最后 5 个交易日）含 9-14=3.9593
    tampered = _fixture_text(day).replace("现4.156", "现3.959")
    assert tampered != _fixture_text(day)

    issues, skipped = qc.check_hallucination_classified(
        _write(tmp_path, tampered), _real_actual_data(day)
    )
    assert not any("002163" in i and "净值不符" in i for i in issues), issues
    assert "hallucination_nav_timegap:002163" in skipped, skipped


def test_small_nav_deviation_within_tolerance_is_ok():
    """容差护栏：净值差 0.001 以内（.3f 四舍五入的正常损失）**不得**误报。

    09-18 002163 真实 4.1558 显示 4.156（差 0.0002）。故障注入方向：容差改 0 → 红。
    """
    assert qc.NAV_TOLERANCE >= 0.001
    issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-18")), _real_actual_data("2026-09-18")
    )
    assert not _net_issues(issues), issues


# ============================================================
# ③ 故障注入 B：改「▲Z%」→ 必须报「内部不一致」
# ============================================================

def test_inject_wrong_pct_reports_inconsistency(tmp_path):
    """★ 故障注入 B：09-16 的 `▲55.4%` → `▲12.3%` → 必报「内部不一致」。

    round((4.031-2.594)/2.594*100,1)=55.4，显示 12.3 → 差 43.1。
    故障注入方向：逐行一致性检查若被删，本用例红。
    """
    day = "2026-09-16"
    tampered = _fixture_text(day).replace("▲55.4%", "▲12.3%")
    assert tampered != _fixture_text(day), "前提：替换确实生效"

    issues, _ = qc.check_hallucination_classified(
        _write(tmp_path, tampered, "2026-09-16_briefing_LeiJiang.txt"),
        _real_actual_data(day),
    )
    assert any("内部不一致" in i and "002163" in i for i in issues), issues


def test_negative_pct_sign_is_not_a_false_positive():
    """符号护栏：晨报把方向放 ▲/▼、数字是绝对值 —— ▼ 必须还原成负号再比。

    故障注入方向：忘了处理 ▼（拿 +绝对值 比），09-17 的 ▼1.5/▼4.3/▼10.3/▼27.0
    四行会全部误报。本轮真踩过这个坑，故单列一条钉死。
    """
    issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-17")), _real_actual_data("2026-09-17")
    )
    assert not _inconsistency_issues(issues), issues


# ============================================================
# ④ 净值缺失路径：必须进 skipped，不得静默通过
# ============================================================

def test_no_nav_available_is_skipped_not_silently_passed():
    """★ 掏空数据源（actual_data={}）→ issues 空，但 **skipped 必须非空**（8 条）。"""
    issues, skipped = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-18")), {}
    )
    assert issues == [], issues
    assert skipped, "取不到净值必须进 skipped —— 静默通过就是漏报"
    assert len(skipped) == 8, skipped
    assert all(s.startswith("hallucination_nav_missing:") for s in skipped)


def test_briefing_missing_nav_row_is_skipped(tmp_path):
    """晨报里「现净值缺失」的行无从核对 → 必须进 skipped（不是通过也不是报错）。"""
    content = (
        "📊 组合温度计（截至近日收盘）\n"
        "总投入 ¥100  当前市值 ¥75  整体浮盈 📉 -25.0%\n\n"
        "持仓明细：\n"
        "  • 华夏全球科技先锋混合(005698)  "
        "买入3.530 → 现净值缺失 ⚠️  ¥75.0（按成本计）\n"
    )
    issues, skipped = qc.check_hallucination_classified(_write(tmp_path, content), {})
    assert issues == [], issues
    assert any("005698" in s for s in skipped), skipped


def test_evaluate_records_skipped_when_no_nav(tmp_path, monkeypatch):
    """整链路：provider 返回空 → evaluate_push_quality 的 checks_skipped 非空。"""
    (tmp_path / "2026-09-18_briefing_LeiJiang.txt").write_text(
        _fixture_text("2026-09-18"), encoding="utf-8"
    )
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)

    res = qc.evaluate_push_quality(
        "2026-09-18", "LeiJiang", actual_data_provider=lambda push_date, codes: {}
    )
    assert res["checks_skipped"], "取不到净值必须如实进 checks_skipped"
    assert any("hallucination" in s for s in res["checks_skipped"]), res["checks_skipped"]


# ============================================================
# ⑤ 取数函数 _build_actual_data / _nav_strictly_before / _recent_navs
# ============================================================

def test_build_actual_data_picks_last_day_strictly_before(monkeypatch):
    """★ 取数口径：expect 必须取**严格早于**晨报日期的最后交易日净值。

    故障注入方向：把 `d >= push_date` 改成 `d > push_date`（允许取当日），
    会取到 9-18 的 9.9999 → 断言失败。
    """
    series = [
        {"date": "2026-09-16", "nav": 4.1639, "rate": None},
        {"date": "2026-09-17", "nav": 4.1558, "rate": None},
        {"date": "2026-09-18", "nav": 9.9999, "rate": None},  # 当日：必须排除
        {"date": "2026-09-21", "nav": 8.8888, "rate": None},  # 未来：必须排除
    ]
    monkeypatch.setattr(
        qc, "_unit_nav_history", lambda code, days=30: series,
    )
    actual = qc._build_actual_data("2026-09-18", ["002163"])
    assert set(actual) == {"002163"}
    assert actual["002163"]["expect"] == 4.1558
    assert 9.9999 not in actual["002163"]["recent"]
    assert 8.8888 not in actual["002163"]["recent"]
    assert actual["002163"]["recent"] == [4.1639, 4.1558]


def test_build_actual_data_omits_codes_without_data(monkeypatch):
    """★ 取不到的 code **绝不**进 dict（不许编 0 / 拿成本顶替）。"""
    def fake(code, days=30):
        if code == "002163":
            return [{"date": "2026-09-17", "nav": 4.1558, "rate": None}]
        return []  # 数据源无返回

    monkeypatch.setattr(qc, "_unit_nav_history", fake)
    actual = qc._build_actual_data("2026-09-18", ["002163", "999999"])
    assert set(actual) == {"002163"}
    assert "999999" not in actual, "取不到的 code 绝不放进 dict（不编 0）"


def test_build_actual_data_tolerates_datasource_exception(monkeypatch):
    """数据源抛异常 → 当作取不到（进 skipped），不得把整个质检炸掉。"""
    def boom(code, days=30):
        raise RuntimeError("akshare down")

    monkeypatch.setattr(qc, "_unit_nav_history", boom)
    assert qc._build_actual_data("2026-09-18", ["002163"]) == {}


def test_build_actual_data_uses_unit_nav_not_cumulative(monkeypatch):
    """★ 口径守卫：expect 必须来自**单位净值**，不得回落成累计净值。

    背景：002163 有分红历史，累计 4.2824 / 单位 3.0385（差 1.2439）。
    2026-09-19 起晨报改用单位净值渲染，质检若仍取累计 → 整行误报 →
    22:00 真发告警。本用例是这条回归的承重墙。

    故障注入方向：把 ``_build_actual_data`` 的取数改回
    ``services.fund_monitor.get_fund_nav_history``（累计口径）→ 断言失败。
    """
    unit_series = [
        {"date": "2026-09-16", "nav": 2.9200, "rate": None},
        {"date": "2026-09-17", "nav": 2.9119, "rate": None},
    ]
    cumulative_series = [
        {"date": "2026-09-16", "nav": 4.1639, "rate": None},
        {"date": "2026-09-17", "nav": 4.1558, "rate": None},
    ]
    monkeypatch.setattr(qc, "_unit_nav_history",
                        lambda code, days=30: unit_series)
    # 累计口径仍在，但**不该被用到** —— 用哨兵值证明没走它
    monkeypatch.setattr(fund_monitor, "get_fund_nav_history",
                        lambda code, days=30, force_refresh=False: cumulative_series)

    actual = qc._build_actual_data("2026-09-18", ["002163"])
    assert actual["002163"]["expect"] == 2.9119, "expect 必须是单位净值"
    assert actual["002163"]["expect"] != 4.1558, "不得取累计净值（会误报）"


def test_recent_navs_returns_ascending_window():
    """_recent_navs 返回**严格早于 D** 的最后 k 个（升序），并剔除 0/坏日期。"""
    hist = [
        {"date": "2026-09-10", "nav": 1.0, "rate": None},
        {"date": "2026-09-11", "nav": 0.0, "rate": None},      # 0 → 剔除
        {"date": "2026-09-12", "nav": 2.0, "rate": None},
        {"date": "2026-09-15", "nav": 3.0, "rate": None},
        {"date": "2026-09-18", "nav": 9.0, "rate": None},      # 当日 → 排除
        {"date": "bad", "nav": 5.0, "rate": None},
    ]
    assert qc._recent_navs(hist, "2026-09-18", 2) == [2.0, 3.0]
    assert qc._recent_navs(hist, "2026-09-18", 10) == [1.0, 2.0, 3.0]
    assert qc._recent_navs(hist, "2026-09-11", 5) == [1.0]
    assert qc._recent_navs([], "2026-09-18", 5) == []


def test_nav_strictly_before_ignores_zero_and_bad_rows():
    """0 净值 / 坏日期 一律排除，绝不参与核对。"""
    hist = [
        {"date": "2026-09-16", "nav": 0.0, "rate": None},
        {"date": "not-a-date", "nav": 5.0, "rate": None},
        {"date": "2026-09-15", "nav": 4.0, "rate": None},
    ]
    assert qc._nav_strictly_before(hist, "2026-09-18") == 4.0
    assert qc._nav_strictly_before([], "2026-09-18") is None
    assert qc._nav_strictly_before(hist, "") is None
    assert qc._norm_date("20260917") == "2026-09-17"
    assert qc._norm_date("2026/09/17") == "2026-09-17"


def test_nav_entry_backcompat_with_plain_float():
    """_nav_entry 兼容旧形状（直接给净值 float），不留兼容缺口。"""
    assert qc._nav_entry(4.15) == (4.15, [])
    assert qc._nav_entry({"expect": 4.15, "recent": [4.0]}) == (4.15, [4.0])
    assert qc._nav_entry(None) == (None, [])


# ============================================================
# ⑥ 块级动态容差
# ============================================================

def test_summary_value_tolerance_is_dynamic():
    """★ 动态容差：8 行时 ≥1.4（固定 1.0 只剩 10% 余量、≥11 行必破）。

    误差上界 = 0.5 + 0.05×行数；这里断言公式给出的容差随行数增长且不低于下限。
    """
    assert qc._summary_value_tolerance(8) >= 1.4
    assert qc._summary_value_tolerance(20) > qc._summary_value_tolerance(8)
    assert qc._summary_value_tolerance(0) >= qc.SUMMARY_VALUE_TOLERANCE


def test_block_value_mismatch_beyond_tolerance_is_flagged(tmp_path):
    """块级「当前市值 ≠ Σ¥V」超过动态容差 → 必须报内部不一致。"""
    content = _fixture_text("2026-09-18").replace(
        "总投入 ¥709  当前市值 ¥768", "总投入 ¥709  当前市值 ¥600"
    )
    assert "当前市值 ¥600" in content
    issues, _ = qc.check_hallucination_classified(
        _write(tmp_path, content), _real_actual_data("2026-09-18")
    )
    assert any("内部不一致" in i and "市值合计" in i for i in issues), issues


# ============================================================
# ⑦ 整链路：evaluate_push_quality 真的用注入的 provider，且不误报
# ============================================================

def test_evaluate_uses_injected_provider_and_no_false_positive(tmp_path, monkeypatch):
    """★ 整链路：provider 被调用、push_date/codes 齐全，真实存档 0 净值/一致性 issue。"""
    (tmp_path / "2026-09-18_briefing_LeiJiang.txt").write_text(
        _fixture_text("2026-09-18"), encoding="utf-8"
    )
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)

    called = {}

    def provider(push_date, codes):
        called["push_date"] = push_date
        called["codes"] = list(codes)
        return _real_actual_data("2026-09-18")

    res = qc.evaluate_push_quality("2026-09-18", "LeiJiang",
                                   actual_data_provider=provider)

    assert called["push_date"] == "2026-09-18", called
    assert set(called["codes"]) == set(ALL_CODES), called["codes"]

    joined = " | ".join(i for p in res["pushes"] for i in p["issues"])
    assert "净值不符" not in joined, joined
    assert "内部不一致" not in joined, joined


# ============================================================
# ⑧ 源码护栏
# ============================================================

def test_dead_sector_regex_is_removed():
    """★ 源码护栏：永远匹配不上的「板块」正则必须已被移除（不许留着假装在工作）。"""
    src = inspect.getsource(qc)
    code = _code_only(src)

    assert "sector_mentions" not in code, "死正则的变量必须已删除"
    assert "板块[^" not in code, "死正则的字符类必须已从代码里移除"
    assert "板块涨跌幅不匹配" not in src, "旧的板块 issue 文案必须已删除"


def test_check_hallucination_delegates_not_duplicates():
    """接口护栏：check_hallucination 必须是**转发**到 classified 版本。"""
    thin = inspect.getsource(qc.check_hallucination)
    assert "check_hallucination_classified" in thin, "必须是转发"


def test_tolerances_are_sane():
    """容差护栏：22:00 会真发企微告警，容差宁可放宽也不要天天误报。"""
    assert 0 < qc.NAV_TOLERANCE <= 0.01
    assert 0 < qc.ROW_PCT_TOLERANCE <= 0.5
    assert 0 < qc.SUMMARY_VALUE_TOLERANCE <= 2.0
    assert 0 < qc.SUMMARY_PCT_TOLERANCE <= 2.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
