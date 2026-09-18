#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报「净值核对 / 内部一致性」检查回归测试（v9.9.54）。

## 背景（见 daily_push_quality_check.py 顶部注释，这里只摘要点）

1. `check_hallucination()` 从上线起**每天空转**：调用处 `actual_data = {}`
   恒空 → `actual_pct is not None` 永不成立，只被如实记进 `checks_skipped`。
2. 原「检查1（基金涨跌幅）」语义本身也错：它抓的是**持仓浮盈亏率**（不是当日
   涨跌幅），拿真实「当日涨跌幅」去比必然 100% 误报。⇒ 本轮整体换成**净值核对**。
3. 原「检查2（板块涨跌幅）」正则永远匹配不上（实测 5 天晨报 0 命中）⇒ **删除**，
   替换为纯**内部一致性**检查（不需外部数据源）。

## 口径铁律

晨报日期 D 的「现净值」= **严格早于 D 的最后一个交易日**的净值（晨报 08:30
生成，那时能拿到的最新净值就是前一交易日的）。实测：
    002163: 9-15=4.0314  9-16=4.1639  9-17=4.1558
    ⇒ 9-16 晨报「现4.031」= 9-15 净值；9-17「现4.164」= 9-16 净值；…

## 本文件的两条硬要求

- **必须能精确红**：故障注入用例（改净值 / 改浮盈率 / 掏空数据源）都经过实跑，
  见各用例 docstring 里的「故障注入方向」。
- **绝不打真实数据源**：净值一律通过 `actual_data` / `actual_data_provider`
  注入。取数函数 `_build_actual_data` 自身用 monkeypatch 注入假序列来测。

## fixture

`tests/fixtures/2026-09-1{6,7,8}_briefing_LeiJiang.txt` 是服务器
`/opt/moneybag/data/logs/pushes/` 的**真实存档**，未做任何脱敏或裁剪。
"""
import inspect
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import daily_push_quality_check as qc  # noqa: E402
import services.fund_monitor as fund_monitor  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# 三只基金的**生产实测净值**（累计净值口径，与晨报同源）。
# 键 = 晨报日期 D，值 = 「严格早于 D 的最后一个交易日」的净值。
REAL_NAV = {
    "2026-09-16": {"002163": 4.0314, "013107": 2.8353, "016501": 3.1245},
    "2026-09-17": {"002163": 4.1639, "013107": 2.9081, "016501": 3.2524},
    "2026-09-18": {"002163": 4.1558, "013107": 2.9041, "016501": 3.2702},
}
ALL_CODES_18 = ["002163", "013107", "016501", "005851", "006555",
                "008984", "007356", "005698"]


def _fixture_path(day: str) -> pathlib.Path:
    return FIXTURES / f"{day}_briefing_LeiJiang.txt"


def _fixture_text(day: str) -> str:
    return _fixture_path(day).read_text(encoding="utf-8")


def _write(tmp_path, content: str, name: str = "2026-09-18_briefing_LeiJiang.txt") -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _code_only(src: str) -> str:
    """剥掉注释行，只看真实代码（护栏用，避免被说明文字打红）。"""
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))


# ============================================================
# ① 真实存档回归：对的晨报必须 0 issue（误报就是 bug）
# ============================================================

@pytest.mark.parametrize("day", ["2026-09-16", "2026-09-17", "2026-09-18"])
def test_real_archive_has_zero_issue(day):
    """★ 真实晨报（数字都对）跑新逻辑必须 **0 issue**。

    它是本轮的正确性基线：净值核对若与晨报口径不一致（比如把「当日净值」当成
    「严格早于当日」），这三天会立刻大面积误报。
    """
    issues, _skipped = qc.check_hallucination_classified(
        str(_fixture_path(day)), REAL_NAV[day]
    )
    assert issues == [], f"{day} 真实晨报不该误报，实际 {issues}"


def test_real_archive_check_is_not_vacuous():
    """★ 证明上面「0 issue」不是空跑：这三天里确实有 3 只被**真正核对**过。

    如果 check_hallucination 又退回「一律 skipped / 不进循环」，本用例仍会绿
    （它只看解析结果），但 `test_inject_wrong_nav_reports_mismatch` 会红 ——
    两条一起才把「真的在核」钉死。
    """
    nav = REAL_NAV["2026-09-18"]
    rows = qc._parse_position_rows(_fixture_text("2026-09-18"))
    verified = [r for r in rows if not r["navMissing"] and r["code"] in nav]

    assert {r["code"] for r in verified} == {"002163", "013107", "016501"}
    for r in verified:
        assert abs(r["cur"] - nav[r["code"]]) <= qc.NAV_TOLERANCE, (
            f"{r['code']} 晨报 {r['cur']} 应≈实际 {nav[r['code']]}"
        )


def test_parser_handles_truncated_name_row():
    """兼容历史「名称被括号吐到一半」的旧存档（• 浦银安盛全球智能科技(Q(006555)…）。

    故障注入方向：把名称组改成贪婪 `[^\\n]+`，这一行会吞掉代码前的 `(Q`、
    抓不到 006555 —— 断言失败。
    """
    rows = qc._parse_position_rows(_fixture_text("2026-09-16"))
    codes = [r["code"] for r in rows]
    assert "006555" in codes and "005698" in codes, codes


# ============================================================
# ② 故障注入 A：改「现净值」→ 必须报「净值不符」
# ============================================================

def test_inject_wrong_nav_reports_mismatch(tmp_path):
    """★ 故障注入 A：把某行「现4.156」改成「现4.999」→ 必须报「净值不符」。

    先证明原样 0 issue，再改一个数 → 必须出现针对 002163 的净值不符。
    故障注入方向：若净值核对又变回空转（actual_data 被忽略），这里会**静默
    通过** —— 本用例会红。
    """
    base_issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-18")), REAL_NAV["2026-09-18"]
    )
    assert base_issues == [], f"基线必须干净，实际 {base_issues}"

    tampered = _fixture_text("2026-09-18").replace("现4.156", "现4.999")
    assert tampered != _fixture_text("2026-09-18"), "前提：替换确实生效"

    issues, _ = qc.check_hallucination_classified(
        _write(tmp_path, tampered), REAL_NAV["2026-09-18"]
    )
    assert any("净值不符" in i and "002163" in i for i in issues), issues


def test_small_nav_deviation_within_tolerance_is_ok(tmp_path):
    """容差护栏：净值差 0.001 以内（显示四舍五入的正常损失）**不得**误报。

    晨报用 `.3f`，真实 4.1558 显示成 4.156（差 0.0002）—— 若容差写死 0，
    三天真实存档会天天误报。故障注入方向：把 NAV_TOLERANCE 改成 0 → 本用例红。
    """
    assert qc.NAV_TOLERANCE >= 0.001
    issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-18")), REAL_NAV["2026-09-18"]
    )
    assert not any("净值不符" in i for i in issues), issues


# ============================================================
# ③ 故障注入 B：改「▲Z%」→ 必须报「内部不一致」
# ============================================================

def test_inject_wrong_pct_reports_inconsistency(tmp_path):
    """★ 故障注入 B：把 002163 的 `▲55.4%` 改成 `▲12.3%` → 必报「内部不一致」。

    逐行一致性：round((4.031-2.594)/2.594*100, 1)=55.4，显示 12.3 → 差 43.1。
    故障注入方向：若逐行一致性检查被删，本用例红。
    """
    content = _fixture_text("2026-09-16")
    tampered = content.replace("▲55.4%", "▲12.3%")
    assert tampered != content, "前提：替换确实生效"

    issues, _ = qc.check_hallucination_classified(
        _write(tmp_path, tampered, "2026-09-16_briefing_LeiJiang.txt"),
        REAL_NAV["2026-09-16"],
    )
    assert any("内部不一致" in i and "002163" in i for i in issues), issues


def test_negative_pct_sign_is_not_a_false_positive(tmp_path):
    """符号护栏：晨报把方向放 ▲/▼、数字是绝对值 —— ▼ 必须还原成负号再比。

    故障注入方向：若忘了处理 ▼（直接拿 +绝对值 比），09-17 里 ▼1.5 / ▼4.3 /
    ▼10.3 / ▼27.0 四行会全部误报「应显示 -X%，晨报显示 +X%」。本轮真踩过
    这个坑（第一版实现全量误报），故单列一条钉死。
    """
    issues, _ = qc.check_hallucination_classified(
        str(_fixture_path("2026-09-17")), REAL_NAV["2026-09-17"]
    )
    assert not any("内部不一致" in i for i in issues), issues


# ============================================================
# ④ 净值缺失路径：必须进 skipped，不得静默通过
# ============================================================

def test_no_nav_available_is_skipped_not_silently_passed(tmp_path):
    """★ 掏空数据源（actual_data={}）→ issues 空，但 **skipped 必须非空**。

    这是本项目铁律：不允许静默失效。「没核对」必须可被看见（进
    checks_skipped），不能表现成「检查通过」。9-18 有 8 行都带现净值。
    """
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
    issues, skipped = qc.check_hallucination_classified(
        _write(tmp_path, content), {}
    )
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
# ⑤ 取数函数 _build_actual_data / _nav_strictly_before
# ============================================================

def test_build_actual_data_picks_last_day_strictly_before(monkeypatch):
    """★ 取数口径：必须取**严格早于**晨报日期的最后交易日净值。

    故障注入方向：把 `d >= push_date` 改成 `d > push_date`（即允许取到当日），
    本用例会取到 9-18 的 9.9999 → 断言失败。
    """
    series = [
        {"date": "2026-09-16", "nav": 4.1639, "rate": None},
        {"date": "2026-09-17", "nav": 4.1558, "rate": None},
        {"date": "2026-09-18", "nav": 9.9999, "rate": None},  # 当日：必须排除
        {"date": "2026-09-21", "nav": 8.8888, "rate": None},  # 未来：必须排除
    ]
    monkeypatch.setattr(
        fund_monitor, "get_fund_nav_history",
        lambda code, days=30, force_refresh=False: series,
    )
    actual = qc._build_actual_data("2026-09-18", ["002163"])
    assert actual == {"002163": 4.1558}, actual


def test_build_actual_data_omits_codes_without_data(monkeypatch):
    """★ 取不到的 code **绝不**进 dict（不许编 0 / 拿成本顶替）。"""
    def fake(code, days=30, force_refresh=False):
        if code == "002163":
            return [{"date": "2026-09-17", "nav": 4.1558, "rate": None}]
        return []  # 数据源无返回

    monkeypatch.setattr(fund_monitor, "get_fund_nav_history", fake)
    actual = qc._build_actual_data("2026-09-18", ["002163", "999999"])
    assert actual == {"002163": 4.1558}
    assert "999999" not in actual, "取不到的 code 绝不放进 dict（不编 0）"


def test_build_actual_data_tolerates_datasource_exception(monkeypatch):
    """数据源抛异常 → 当作取不到（进 skipped），不得把整个质检炸掉。"""
    def boom(code, days=30, force_refresh=False):
        raise RuntimeError("akshare down")

    monkeypatch.setattr(fund_monitor, "get_fund_nav_history", boom)
    actual = qc._build_actual_data("2026-09-18", ["002163"])
    assert actual == {}


def test_nav_strictly_before_ignores_zero_and_bad_rows():
    """0 净值 / 坏日期 一律排除，绝不参与核对（避免拿 0 比出巨额「差值」）。"""
    hist = [
        {"date": "2026-09-16", "nav": 0.0, "rate": None},
        {"date": "not-a-date", "nav": 5.0, "rate": None},
        {"date": "2026-09-15", "nav": 4.0, "rate": None},
    ]
    assert qc._nav_strictly_before(hist, "2026-09-18") == 4.0
    assert qc._nav_strictly_before([], "2026-09-18") is None
    assert qc._nav_strictly_before(hist, "") is None
    # 归一化：8 位 / 斜杠 写法也能识别
    assert qc._norm_date("20260917") == "2026-09-17"
    assert qc._norm_date("2026/09/17") == "2026-09-17"


# ============================================================
# ⑥ 整链路：evaluate_push_quality 真的用注入的 provider，且不误报
# ============================================================

def test_evaluate_uses_injected_provider(tmp_path, monkeypatch):
    """★ 整链路：evaluate_push_quality 必须把真实 provider 用起来（不再空跑）。

    断言 provider 被调用、push_date 解析对（来自文件名 2026-09-18）、codes 齐全，
    且真实存档在真净值下不产生净值/一致性 issue。
    """
    (tmp_path / "2026-09-18_briefing_LeiJiang.txt").write_text(
        _fixture_text("2026-09-18"), encoding="utf-8"
    )
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)

    called = {}

    def provider(push_date, codes):
        called["push_date"] = push_date
        called["codes"] = list(codes)
        return REAL_NAV["2026-09-18"]

    res = qc.evaluate_push_quality("2026-09-18", "LeiJiang",
                                   actual_data_provider=provider)

    assert called["push_date"] == "2026-09-18", called
    assert set(called["codes"]) == set(ALL_CODES_18), called["codes"]

    joined = " | ".join(i for p in res["pushes"] for i in p["issues"])
    assert "净值不符" not in joined, joined
    assert "内部不一致" not in joined, joined
    # 8 行里只有 3 只有净值 → 另外 5 只如实进 skipped
    assert any("hallucination_nav_missing:006555" in s
               for s in res["checks_skipped"]), res["checks_skipped"]


# ============================================================
# ⑦ 源码护栏：死正则必须已删除、接口不许复制
# ============================================================

def test_dead_sector_regex_is_removed():
    """★ 源码护栏：永远匹配不上的「板块」正则必须已被移除（不许留着假装在工作）。

    实测 5 天真实晨报 0 命中 —— 留一个恒不命中的正则在代码里充当「检查」，
    等于让人误以为它在工作（本项目反复强调的「假绿」）。
    """
    src = inspect.getsource(qc)
    code = _code_only(src)

    assert "sector_mentions" not in code, "死正则的变量必须已删除"
    assert "板块[^" not in code, "死正则的字符类必须已从代码里移除"
    assert "板块涨跌幅不匹配" not in src, "旧的板块 issue 文案必须已删除"


def test_check_hallucination_delegates_not_duplicates():
    """接口护栏：check_hallucination 必须是**转发**到 classified 版本。

    两份实现迟早漂移：只改一份时另一份会把旧行为静默带回线上。
    """
    thin = inspect.getsource(qc.check_hallucination)
    assert "check_hallucination_classified" in thin, "必须是转发"


def test_tolerances_are_sane():
    """容差护栏：22:00 会真发企微告警，容差宁可放宽也不要天天误报。

    上限钉死是为了拦住「把容差放到跟信号同量级」从而什么都抓不到。
    """
    assert 0 < qc.NAV_TOLERANCE <= 0.01
    assert 0 < qc.ROW_PCT_TOLERANCE <= 0.5
    assert 0 < qc.SUMMARY_VALUE_TOLERANCE <= 2.0
    assert 0 < qc.SUMMARY_PCT_TOLERANCE <= 2.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
