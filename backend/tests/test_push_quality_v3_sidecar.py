#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晨报净值质检 v3（侧车 + 独立第三方源）回归测试（v9.9.59）。

## 为什么要有这个文件

v3 有 600+ 行代码（``_load_sidecar`` / ``_check_render_consistency`` /
``_check_independent_caliber`` / ``_check_freshness`` / ``_reason_class`` /
``check_hallucination_v3`` / ``v3_verdict_to_report``），而
``daily_push_quality_check.py`` 的注释里引用的本文件曾经**并不存在** ——
即 v3 是 0 测试的。本文件补上，并把三条最容易"静默退化"的东西钉死。

## 三件最要紧的事

1. **caliber 是承重字段，不是注释**
   侧车声明 ``caliber == "unit_nav"`` 才能被采信。放过去一个
   ``"accum_nav"`` 的侧车，等于**拿累计净值去核单位净值**，v3 会全绿 ——
   比不检查更糟（不检查至少会 DEGRADED）。

2. **生成层 ↔ 消费层的契约**（本文件最要紧的一组）
   两侧是同一个人写的，但**从没被放在一起跑过**。
   schema 版本号、``caliber`` 字面量、``rows`` 的字段名
   （``code/name/nav/nav_date/cur_val/float_pct``）任何一处对不上，
   v3 上线就会**整条静默退化成 skipped** —— 看起来"没告警"，
   其实是"根本没在核"。契约用例把生成层真实产出喂给消费层跑通。

3. **零误报的反面**
   契约用例里，生成层真实产出 + 一致的独立源，必须 **issues == []**。
   这是"不会天天红"的正向证明（配合归档 dry-run 的负向证明）。

## 时区锚点

天天基金 ``pingzhongdata`` 的 ``x`` 是**北京零点**的 epoch(ms)。
naive ``utcfromtimestamp`` 会得到**前一天**。真实锚点：
``x = 1789488000000`` → 北京 **2026-09-16**（naive 会算成 09-15）。
差一天 = 核对日期整体错位一档，要么全绿（按错位的日期恰好也对上）
要么全红，都是灾难。

## 硬要求

- 每条守卫都要**能变红**（见文件末尾的故障注入清单注释）。
- 绝不打真实网络：独立源一律用注入的 provider。
- 绝不读真实用户档案：USERS_DIR / PUSH_ARCHIVE_DIR 一律 tmp_path。
"""
import hashlib
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts import daily_push_quality_check as qc  # noqa: E402
import scripts.night_worker as nw  # noqa: E402


# ---------------------------------------------------------------------------
# 构造用料
# ---------------------------------------------------------------------------

#: 生产实测真值（2026-09-19 天天基金 pingzhongdata）：{code: (单位, 累计)}
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
    "009708": 4.5892,  # 无分红，单位 == 累计
}

#: 生产持仓 BuLuoGeLi（V4 transactions，与 night_worker 同口径）
BULUOGELI_TXNS = [
    {"type": "BUY", "code": "009708", "name": "工银新兴制造混合C",
     "amount": 200.0, "shares": 45.23, "nav": 4.4218},
    {"type": "BUY", "code": "100038", "name": "富国沪深300指数增强A",
     "amount": 100.0, "shares": 49.02, "nav": 2.04},
    {"type": "BUY", "code": "163406", "name": "兴全合润混合A",
     "amount": 310.0, "shares": 117.99, "nav": 2.6273},
]

#: 生成层假净值时统一用的净值日期（让 nav_date 可断言）
FAKE_NAV_DATE = "2026-09-17"
PUSH_DATE = "2026-09-18"


def _row(code: str, name: str, buy: float, cur: float, arrow: str,
         pct: float, val: float) -> str:
    """逐字照抄 night_worker 的持仓明细行格式。"""
    return f"  • {name}({code})  买入{buy:.3f} → 现{cur:.3f}  {arrow}{pct:.1f}%  ¥{val:.1f}"


def _summary(cost: float, val: float, pct: float) -> str:
    flag = "📈" if pct >= 0 else "📉"
    return (f"📊 组合温度计（截至近日收盘）\n"
            f"总投入 ¥{cost:.0f}  当前市值 ¥{val:.0f}  整体浮盈 {flag} {pct:+.1f}%\n")


def _briefing(summary: str, *rows: str) -> str:
    return (f"=== {PUSH_DATE} 08:30:19 ===\n☀️ 早安！\n\n"
            + summary + "\n持仓明细：\n" + "\n".join(rows) + "\n")


def _archive(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def _sc_row(code, name, nav, nav_date=FAKE_NAV_DATE, wt_nav=2.6273,
            cur_val=264.33, float_pct=-14.73, shares=117.99,
            nav_missing=False):
    return {
        "code": code, "name": name,
        "nav": (None if nav_missing else nav),
        "nav_date": ("" if nav_missing else nav_date),
        "wt_nav": wt_nav, "shares": shares, "cur_val": cur_val,
        "float_pct": (None if nav_missing else float_pct),
        "navMissing": bool(nav_missing),
    }


def _sidecar(rows, schema=1, caliber="unit_nav", total=None):
    return {
        "schema": schema, "caliber": caliber,
        "generated_at": "2026-09-18T08:30:00",
        "rows": rows,
        "total": total or {"cost": 310.0, "value": 264.33, "pct": -14.73},
    }


def _write_sidecar(push_file: str, payload) -> str:
    p = qc._sidecar_path(push_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload if isinstance(payload, str)
                 else json.dumps(payload, ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def _domestic(name):
    return False


def _provider_for(unit_by_code, accum_by_code=None, dates=(FAKE_NAV_DATE,)):
    """构造独立源 provider：``code -> {"unit": {...}, "accum": {...}}``。"""
    def _fn(code):
        u = unit_by_code.get(code)
        if u is None:
            return None
        accum = (accum_by_code or {}).get(code)
        return {
            "unit": {d: u for d in dates},
            "accum": ({d: accum for d in dates} if accum is not None else {}),
        }
    return _fn


def _weekday_calendar(monkeypatch):
    """把交易日历钉成「工作日即交易日」，让时效性用例不依赖真实节假日表。"""
    import datetime as _dt

    def _is_trading_day(d):
        return d.weekday() < 5

    import services.signal_scout as ss
    monkeypatch.setattr(ss, "is_trading_day", _is_trading_day, raising=False)


# ===========================================================================
# 1. _load_sidecar 的每条拒绝路径（故障注入目标：删掉任一校验 → 对应用例红）
# ===========================================================================

def test_load_sidecar_missing(tmp_path):
    """没有侧车 → ``sidecar_missing``（v3 判 DEGRADED，绝不静默通过）。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", _briefing(
        _summary(310.0, 264.3, -14.7),
        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3)))
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None
    assert err == "sidecar_missing", err


def test_load_sidecar_unreadable(tmp_path):
    """侧车是坏 JSON → 如实记 ``sidecar_unreadable:*``，不猜内容。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, "{ 这不是 json")
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None
    assert err.startswith("sidecar_unreadable:"), err


def test_load_sidecar_not_object(tmp_path):
    """侧车顶层不是对象（例如写成了 rows 数组）→ ``sidecar_invalid:not_object``。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, json.dumps([{"code": "163406"}]))
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None
    assert err == "sidecar_invalid:not_object", err


def test_load_sidecar_schema_mismatch_is_rejected(tmp_path):
    """schema 版本不符 → 拒用。

    ⚠️ 为什么不能"宽容地读"：schema 升级后字段名/语义可能整体变，
    消费层按旧结构解析会得到一批 None，而 None 在 v3 里一律走
    ``unverified``（不报错）→ 整条检查**静默退化**。
    """
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)],
                               schema=2))
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None, "schema 不符却仍被采信"
    assert err.startswith("sidecar_schema:"), err


def test_load_sidecar_caliber_mismatch_is_rejected(tmp_path):
    """**承重字段**：caliber 不是 ``unit_nav`` 一律拒用。

    放过去一个 ``accum_nav`` 侧车，v3 会拿累计净值去"核对单位净值" ——
    两边口径一致时全绿，而实际错的是口径本身。这比不检查更糟。
    """
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 8.5191)],
                               caliber="accum_nav"))
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None, "caliber 不是 unit_nav 却仍被采信"
    assert err.startswith("caliber_mismatch:"), err


def test_load_sidecar_rows_not_list(tmp_path):
    """``rows`` 不是数组 → ``sidecar_invalid:rows``。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, _sidecar({"163406": {}}))
    sidecar, err = qc._load_sidecar(f)
    assert sidecar is None
    assert err == "sidecar_invalid:rows", err


def test_load_sidecar_happy_path(tmp_path):
    """合法侧车 → 接受，且 ``error == ""``。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "x")
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)]))
    sidecar, err = qc._load_sidecar(f)
    assert err == "", err
    assert sidecar["caliber"] == qc.CALIBER_UNIT_NAV
    assert sidecar["schema"] == qc.SIDECAR_SCHEMA


def test_sidecar_path_is_same_prefix_as_archive(tmp_path):
    """侧车必须与正文存档**同目录同前缀**，只差扩展名（不串日期/用户）。"""
    f = _archive(tmp_path, "2026-09-18_briefing_LeiJiang.txt", "x")
    sp = qc._sidecar_path(f)
    assert sp.name == "2026-09-18_briefing_LeiJiang.navs.json", sp.name
    assert sp.parent == Path(f).parent


# ===========================================================================
# 2. 时区锚点（注释里写了，这里把它变成断言）
# ===========================================================================

def test_networth_trend_timezone_anchor():
    """``x=1789488000000`` → 北京 **2026-09-16**，不是 09-15。

    naive ``utcfromtimestamp`` 会得到 09-15（该时刻 UTC 还是前一天 16:00）。
    差一天 = 核对日期整体错位一档。
    """
    js = 'var Data_netWorthTrend = [{"x":1789488000000,"y":2.2401}];'
    got = qc._parse_networth_trend(js)
    assert got == {"2026-09-16": 2.2401}, got
    assert "2026-09-15" not in got, "naive utcfromtimestamp 少加了 8 小时"


def test_acworth_trend_array_form():
    """``Data_ACWorthTrend`` 是 ``[[ts, val], ...]`` 数组形式。"""
    js = "var Data_ACWorthTrend = [[1789488000000, 8.5191]];"
    got = qc._parse_acworth_trend(js)
    assert got == {"2026-09-16": 8.5191}, got


def test_networth_trend_skips_bad_elements():
    """坏元素（缺字段 / 非数值 / 非正净值）一律跳过，**绝不编 0**。"""
    js = ('var Data_netWorthTrend = [{"x":1789488000000,"y":2.2401},'
          '{"x":1789488000000}, {"y":1}, null, "x",'
          '{"x":1789488000000,"y":-1}];')
    got = qc._parse_networth_trend(js)
    assert got == {"2026-09-16": 2.2401}, got


def test_networth_trend_no_match_returns_empty():
    """解析不出 → 空字典（调用方判 DEGRADED），不抛异常。"""
    assert qc._parse_networth_trend("") == {}
    assert qc._parse_networth_trend("var x = 1;") == {}


def test_independent_nav_at_has_no_window():
    """``_independent_nav_at`` **没有窗口**：日期对不上就是没核到（None）。

    窗口是万能免罪符 —— 任何滞后一档都会被判成"时点差 → skipped → 永远绿"。
    """
    series = {"unit": {"2026-09-16": 2.2401}, "accum": {}}
    assert qc._independent_nav_at(series, "2026-09-16") == 2.2401
    assert qc._independent_nav_at(series, "2026-09-17") is None
    assert qc._independent_nav_at(series, "") is None
    assert qc._independent_nav_at(None, "2026-09-16") is None


# ===========================================================================
# 3. (i) 渲染一致性：侧车 vs 正文
# ===========================================================================

def test_render_consistency_catches_nav_drift(tmp_path):
    """正文「现2.240」vs 侧车 2.900 → 命中「渲染不一致」。

    反向也是守卫：本用例里**内部一致性是成立的**（2.627→2.240 = ▼14.7%），
    所以红了就证明是 (i) 层抓到的，不是 (0) 层顺带报的。
    """
    content = _briefing(_summary(310.0, 264.3, -14.7),
                        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3))
    rows = qc._parse_position_rows(content)
    assert len(rows) == 1
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.900)])
    issues, unver = qc._check_render_consistency(rows, sidecar)
    assert issues, "渲染漂移没被抓到"
    assert "渲染不一致" in issues[0], issues
    assert "2.240" in issues[0] and "2.900" in issues[0], issues
    # 反向：内部一致性不该同时红（否则说明用例选错了注入点）
    assert qc._check_internal_consistency(rows, content) == []


def test_render_consistency_clean_is_silent(tmp_path):
    """反向用例：侧车与正文一致 → 0 issue（零误报的正向证明）。"""
    content = _briefing(_summary(310.0, 264.3, -14.7),
                        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3))
    rows = qc._parse_position_rows(content)
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401,
                                wt_nav=2.6273, cur_val=264.33, float_pct=-14.73)])
    issues, unver = qc._check_render_consistency(rows, sidecar)
    assert issues == [], issues
    assert unver == [], unver


def test_render_consistency_row_not_in_sidecar(tmp_path):
    """正文有行、侧车里没有 → 命中（生成层漏记一行）。"""
    content = _briefing(_summary(310.0, 264.3, -14.7),
                        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3))
    rows = qc._parse_position_rows(content)
    issues, _ = qc._check_render_consistency(rows, _sidecar([]))
    assert issues and "侧车里不存在" in issues[0], issues


def test_render_consistency_flags_nav_missing_disagreement(tmp_path):
    """侧车说"净值缺失"、正文却印了净值（或反之）→ 必须报。

    ⚠️ 这是补的一个**静默失效洞**：下面的 nav 比对用
    ``r["cur"] is not None and sc_nav is not None`` 做守卫，两侧只要有一边
    说缺失，比对就被跳过 —— 于是"生成层说缺失、正文却印了数"这种
    **不同源**的严重不一致会被完全无视。
    """
    content = _briefing(_summary(310.0, 264.3, -14.7),
                        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3))
    rows = qc._parse_position_rows(content)
    assert not rows[0]["navMissing"]

    # 方向一：正文有净值，侧车却记缺失
    issues, _ = qc._check_render_consistency(
        rows, _sidecar([_sc_row("163406", "兴全合润混合A", None,
                                nav_missing=True)]))
    assert issues and "渲染不一致" in issues[0], issues

    # 方向二：正文标注缺失，侧车却有净值
    content2 = _briefing(
        _summary(310.0, 310.0, 0.0),
        "  • 华夏全球科技先锋混合(005698)  买入3.530 → 现净值缺失 ⚠️  ¥75.0（按成本计）")
    rows2 = qc._parse_position_rows(content2)
    assert rows2 and rows2[0]["navMissing"]
    issues2, _ = qc._check_render_consistency(
        rows2, _sidecar([_sc_row("005698", "华夏全球科技先锋混合", 2.679)]))
    assert issues2 and "渲染不一致" in issues2[0], issues2


def test_render_consistency_nav_missing_agreement_is_silent(tmp_path):
    """反向：两侧都说缺失 → 不报（否则每天误报）。"""
    content = _briefing(
        _summary(310.0, 310.0, 0.0),
        "  • 华夏全球科技先锋混合(005698)  买入3.530 → 现净值缺失 ⚠️  ¥75.0（按成本计）")
    rows = qc._parse_position_rows(content)
    issues, _ = qc._check_render_consistency(
        rows, _sidecar([_sc_row("005698", "华夏全球科技先锋混合", None,
                                wt_nav=3.530, cur_val=75.0, nav_missing=True)]))
    assert issues == [], issues


def test_render_consistency_sidecar_row_not_rendered(tmp_path):
    """侧车有行、正文没有 → ``render_truncated:<code>``（进 skipped，不告警）。

    这是"整行被删"的另一面：它也可能是幻觉删句。**不判 FAIL** 是因为
    行数断言（``check_position_count``）已经在管这件事，两层都告警会重复。
    """
    content = _briefing(_summary(310.0, 264.3, -14.7))
    rows = qc._parse_position_rows(content)
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)])
    issues, unver = qc._check_render_consistency(rows, sidecar)
    assert issues == [], issues
    assert unver == ["render_truncated:163406"], unver


# ===========================================================================
# 4. (ii) 独立源口径 / 数值核对
# ===========================================================================

def test_independent_caliber_passes_when_unit_nav_matches():
    """独立源单位净值 == 侧车 → 通过，进 ``checked``。"""
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, _provider_for({"163406": 2.2401}))
    assert issues == [], issues
    assert unver == [], unver
    assert checked == ["163406"], checked


def test_independent_caliber_flags_accumulated_nav_as_caliber_bug():
    """侧车对得上**累计**、对不上**单位** → 归因成「口径不符」。

    这是 2026-09-19 生产实测的真 P0：002163 / 100038 / 163406 三只渲染的是
    累计净值（买入净值仍是单位 ⇒ 混口径，市值虚增 1.4~3.8×）。
    归因必须是 ``累计净值`` 而不是丢一句"数值不符"，否则告警不可行动。
    """
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 8.5191)])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, _provider_for({"163406": 2.2401}, {"163406": 8.5191}))
    assert len(issues) == 1, issues
    assert "口径不符（独立源）" in issues[0], issues
    assert "累计净值" in issues[0], issues
    assert "2.2401" in issues[0], issues
    assert checked == [], checked


def test_independent_caliber_generic_mismatch_when_not_accum():
    """对不上单位、也对不上累计 → 普通「净值不符」，不谎报成口径问题。"""
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 3.3333)])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, _provider_for({"163406": 2.2401}, {"163406": 8.5191}))
    assert len(issues) == 1, issues
    assert "净值不符（独立源）" in issues[0], issues
    assert "口径不符" not in issues[0], issues


def test_independent_unreachable_is_degraded_not_pass():
    """独立源取不到 → ``independent_unreachable:<code>``（DEGRADED）。

    ⚠️ **绝不**当成"通过"：网络依赖的守卫，取不到数时必须如实说没核到。
    """
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, lambda code: None)
    assert issues == [], "取不到数却判成了不一致 —— 这是纯误报"
    assert unver == ["independent_unreachable:163406"], unver
    assert checked == [], checked


def test_independent_no_such_date_is_degraded():
    """侧车声明的日期在独立源里没有（QDII 滞后 / 新基金）→ 没核到，不判错。"""
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401,
                                nav_date="2026-09-10")])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, _provider_for({"163406": 2.2401}, dates=("2026-09-17",)))
    assert issues == [], issues
    assert unver == [f"independent_no_such_date:163406@2026-09-10"], unver


def test_independent_caliber_skips_nav_missing_rows():
    """净值缺失行（生成层已标注）→ 不核，如实记 ``nav_missing:<code>``。"""
    sidecar = _sidecar([_sc_row("005698", "华夏全球科技先锋混合", None,
                                nav_missing=True)])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, _provider_for({"005698": 1.0}))
    assert issues == [], issues
    assert unver == ["nav_missing:005698"], unver


def test_independent_provider_exception_is_degraded_not_crash():
    """独立源 provider **抛异常** → DEGRADED，不得让整轮质检崩掉。

    ⚠️ 22:00 带 ``--alert`` 的 cron 里，一个未捕获异常会让**整个质检静默
    停摆**（连 v9.9.57 既有的检查都不跑了）—— 那是比误报更糟的失效。
    """
    class _ConnReset(Exception):
        pass

    def _boom(code):
        raise _ConnReset("connection reset")

    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)])
    issues, unver, checked = qc._check_independent_caliber(sidecar, _boom)
    assert issues == [], "provider 崩了却判成了不一致"
    assert unver == ["independent_error:163406:_ConnReset"], unver
    assert checked == [], checked


def test_independent_caliber_never_falls_back_to_internal_source():
    """独立源失败时**不得回落**到项目内部取数链路。

    拿同源数据冒充"独立核对"，比不核对更糟（两边错得一模一样 → 全绿）。
    本用例断言：provider 返回 None 时，v3 里没有任何一条"已核对"。
    """
    sidecar = _sidecar([
        _sc_row("163406", "兴全合润混合A", 2.2401),
        _sc_row("100038", "富国沪深300指数增强A", 1.8750),
    ])
    issues, unver, checked = qc._check_independent_caliber(
        sidecar, lambda code: None)
    assert checked == [], "独立源全挂却仍有 code 被判为『已核对』"
    assert len(unver) == 2, unver


# ===========================================================================
# 5. (iii) 时效性
# ===========================================================================

def test_freshness_stale_is_degraded(monkeypatch):
    """境内基金净值日期落后于"最近应已披露交易日" → ``freshness_stale``。"""
    _weekday_calendar(monkeypatch)
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401,
                                nav_date="2026-09-10")])
    degraded, unver = qc._check_freshness(sidecar, PUSH_DATE, _domestic)
    assert degraded, "整条链路滞后一档却没被抓到"
    assert degraded[0].startswith("freshness_stale:"), degraded
    assert "2026-09-10" in degraded[0], degraded


def test_freshness_fresh_is_silent(monkeypatch):
    """反向：净值日期就是最近交易日 → 0 degraded。"""
    _weekday_calendar(monkeypatch)
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401,
                                nav_date="2026-09-17")])
    degraded, unver = qc._check_freshness(sidecar, PUSH_DATE, _domestic)
    assert degraded == [], degraded


def test_freshness_pure_qdii_is_degraded_not_stale(monkeypatch):
    """纯 QDII 组合：境内日历不适用 → 如实 DEGRADED，不谎报「滞后」。

    QDII 净值本来就 T+2 披露，用 A 股日历断言会**天天**报滞后。
    """
    _weekday_calendar(monkeypatch)
    sidecar = _sidecar([_sc_row("005698", "华夏全球科技先锋混合(QDII)", 1.5,
                                nav_date="2026-09-10")])
    degraded, unver = qc._check_freshness(sidecar, PUSH_DATE, lambda n: True)
    assert degraded == ["freshness_pure_qdii"], degraded


def test_freshness_calendar_unavailable_is_degraded():
    """日历不可用 → DEGRADED（**绝不**当成"通过"）。"""
    sidecar = _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)])

    def _boom(d):
        raise RuntimeError("calendar down")

    import services.signal_scout as ss
    old = getattr(ss, "is_trading_day", None)
    ss.is_trading_day = _boom
    try:
        degraded, unver = qc._check_freshness(sidecar, PUSH_DATE, _domestic)
    finally:
        if old is not None:
            ss.is_trading_day = old
    assert degraded == ["freshness_calendar_unavailable"], degraded


def test_freshness_no_dated_rows_is_degraded(monkeypatch):
    """没有任何带日期的行 → DEGRADED（全缺失不能算"时效达标"）。"""
    _weekday_calendar(monkeypatch)
    sidecar = _sidecar([_sc_row("005698", "华夏全球科技先锋混合", None,
                                nav_missing=True)])
    degraded, _ = qc._check_freshness(sidecar, PUSH_DATE, _domestic)
    assert degraded == ["freshness_no_dated_rows"], degraded


# ===========================================================================
# 6. 三态映射 / 原因分类
# ===========================================================================

def test_reason_class_nav_mismatch():
    """有 issue → ``nav_mismatch``。"""
    assert qc._reason_class({"issues": ["x"], "degraded": [], "unverified": []}) \
        == "nav_mismatch"


def test_reason_class_takes_first_degraded_prefix():
    """无 issue 时取第一条 degraded 的**前缀**（供冷却键用）。"""
    assert qc._reason_class(
        {"issues": [], "degraded": ["freshness_stale:max=1"], "unverified": []}
    ) == "freshness_stale"
    assert qc._reason_class(
        {"issues": [], "degraded": ["sidecar_missing"], "unverified": []}
    ) == "sidecar_missing"


def test_reason_class_clean_is_empty():
    assert qc._reason_class(
        {"issues": [], "degraded": [], "unverified": []}) == ""


def test_v3_verdict_to_report_maps_fail_and_skipped():
    """issues → blocking（会告警）；degraded/unverified → skipped（不告警）。

    这是本项目「22:00 带 --alert 跑」硬约束下的映射：DEGRADED 走
    ``checks_skipped``（``main()`` 会打印，运维看得见）但**不**打扰用户。
    """
    verdict = {
        "issues": ["❌ x"],
        "degraded": ["sidecar_missing"],
        "unverified": ["independent_unreachable:163406"],
    }
    issues, skipped = qc.v3_verdict_to_report(verdict)
    assert issues == ["❌ x"], issues
    assert skipped == ["v3:sidecar_missing",
                       "v3:independent_unreachable:163406"], skipped


# ===========================================================================
# 7. check_hallucination_v3 整体行为
# ===========================================================================

def test_v3_no_sidecar_is_degraded_not_pass(tmp_path):
    """无侧车 → DEGRADED + ``row_unverified``，**绝不静默通过**。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", _briefing(
        _summary(310.0, 264.3, -14.7),
        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3)))
    v = qc.check_hallucination_v3(f, push_date=PUSH_DATE, sidecar=None,
                                  sidecar_error="sidecar_missing",
                                  independent_provider=lambda c: None,
                                  qdii_detector=_domestic)
    assert v["issues"] == [], v
    assert v["degraded"] == ["sidecar_missing"], v
    assert "row_unverified:163406" in v["unverified"], v
    assert v["reason_class"] == "sidecar_missing", v
    assert v["checked"] == 0


def test_v3_no_position_rows_is_silent(tmp_path):
    """没有持仓明细行（空仓 / closing_review）→ 全空，不判 DEGRADED。"""
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", "☀️ 早安！今天空仓")
    v = qc.check_hallucination_v3(f, push_date=PUSH_DATE, sidecar=None,
                                  sidecar_error="sidecar_missing",
                                  independent_provider=lambda c: None,
                                  qdii_detector=_domestic)
    assert v == {"issues": [], "degraded": [], "unverified": [], "checked": 0,
                 "total_rows": 0, "reason_class": ""}, v


def test_v3_all_green_end_to_end(tmp_path, monkeypatch):
    """有侧车 + 独立源一致 + 时效达标 + 内部一致 → **issues == []**。

    这是"不会天天红"的正向证明：四层检查全部命中且全部通过。
    """
    _weekday_calendar(monkeypatch)
    content = _briefing(_summary(310.0, 264.3, -14.7),
                        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3))
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", content)
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)]))
    sidecar, err = qc._load_sidecar(f)
    assert err == "", err
    v = qc.check_hallucination_v3(
        f, push_date=PUSH_DATE, sidecar=sidecar,
        independent_provider=_provider_for({"163406": 2.2401},
                                           {"163406": 8.5191}),
        qdii_detector=_domestic)
    assert v["issues"] == [], v
    assert v["checked"] == 1, v
    assert v["degraded"] == [], v
    assert v["unverified"] == [], v
    assert v["reason_class"] == "", v


def test_v3_accumulated_caliber_produces_blocking_issue(tmp_path, monkeypatch):
    """反向：渲染了累计净值 → v3 产出 blocking issue（告警可直接行动）。"""
    _weekday_calendar(monkeypatch)
    content = _briefing(_summary(310.0, 1005.0, 224.7),
                        _row("163406", "兴全合润混合A", 2.627, 8.519, "▲", 224.7, 1005.0))
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", content)
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 8.5191,
                                        cur_val=1005.2, float_pct=224.7)]))
    sidecar, _ = qc._load_sidecar(f)
    v = qc.check_hallucination_v3(
        f, push_date=PUSH_DATE, sidecar=sidecar,
        independent_provider=_provider_for({"163406": 2.2401},
                                           {"163406": 8.5191}),
        qdii_detector=_domestic)
    assert v["issues"], "累计口径 P0 没被 v3 抓到"
    assert any("口径不符" in i for i in v["issues"]), v["issues"]
    assert v["reason_class"] == "nav_mismatch", v


# ===========================================================================
# 8. 契约：生成层产出 ↔ 消费层接受（本文件最要紧的一组）
# ===========================================================================
#
# 两侧是同一个人写的，但从没被放在一起跑过。schema 版本号、caliber 字面量、
# rows 字段名任何一处对不上 → v3 上线就整条静默退化成 skipped。
# 下面把生成层的真实产出喂给消费层跑通，并反向注入三种"契约漂移"验证能红。

def _drive_generator(uid, txns, tmp_path, monkeypatch, archive_dir=None):
    """在隔离的 USERS_DIR / PUSH_ARCHIVE_DIR 下驱动生成层。

    Returns:
        ``(text, sidecar, push_file)``。
    """
    import config

    safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
    users_dir = tmp_path / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / f"{safe}.json").write_text(
        json.dumps({"userId": uid, "portfolio": {"transactions": txns}},
                   ensure_ascii=False), encoding="utf-8")

    def _fake_unit_nav(code, *a, **kw):
        v = UNIT_NAV.get(code)
        if v is None:
            return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}
        return {"code": code, "nav": str(v), "official_nav": str(v),
                "date": FAKE_NAV_DATE, "change": "0"}

    monkeypatch.setenv("USERS_DIR", str(users_dir))
    monkeypatch.setattr("services.market_data.get_fund_nav", _fake_unit_nav)
    monkeypatch.setattr("services.fund_monitor.get_fund_nav_history",
                        lambda code, days=3, force_refresh=False: [])
    monkeypatch.setattr(config, "PUSH_ARCHIVE_DIR",
                        archive_dir or (tmp_path / "pushes"))

    text, sidecar = nw._build_portfolio_thermometer_with_data(uid)
    push_file = _archive(archive_dir or (tmp_path / "pushes"),
                         f"{PUSH_DATE}_briefing_{uid}.txt", text)
    return text, sidecar, push_file


def test_contract_sidecar_shape_is_accepted_by_consumer(tmp_path, monkeypatch):
    """契约①：生成层产出的侧车字段集 == 消费层要求的字段集。"""
    text, sidecar, _ = _drive_generator(
        "qa_v3_contract", BULUOGELI_TXNS, tmp_path, monkeypatch)
    assert sidecar, "生成层没产出侧车（温度计可能走了 except 分支）"
    assert sidecar["schema"] == qc.SIDECAR_SCHEMA, sidecar["schema"]
    assert sidecar["caliber"] == qc.CALIBER_UNIT_NAV, sidecar["caliber"]
    assert isinstance(sidecar["rows"], list)
    assert {r["code"] for r in sidecar["rows"]} == {"009708", "100038", "163406"}

    required = {"code", "name", "nav", "nav_date", "wt_nav", "shares",
                "cur_val", "float_pct", "navMissing"}
    for r in sidecar["rows"]:
        missing = required - set(r)
        assert not missing, f"{r.get('code')} 侧车行缺字段 {missing}"


def test_contract_write_then_load_roundtrip(tmp_path, monkeypatch):
    """契约②：``nw._write_navs_sidecar`` 落盘的侧车能被 ``qc._load_sidecar`` 接受。

    两侧各自算路径（一个拼 ``{date}_briefing_{uid}.navs.json``，一个从
    ``.txt`` 换后缀）—— 拼法不一致就会永远 sidecar_missing。
    """
    archive_dir = tmp_path / "pushes"
    text, sidecar, push_file = _drive_generator(
        "qa_v3_rt", BULUOGELI_TXNS, tmp_path, monkeypatch, archive_dir)

    written = nw._write_navs_sidecar(PUSH_DATE, "qa_v3_rt", sidecar)
    assert written, "侧车落盘失败"
    assert Path(written) == qc._sidecar_path(push_file), (
        f"生成层落盘路径 {written} 与消费层查找路径 "
        f"{qc._sidecar_path(push_file)} 不一致 —— 上线必然全程 sidecar_missing")

    loaded, err = qc._load_sidecar(push_file)
    assert err == "", f"消费层拒绝了自己生产出来的侧车：{err}"
    assert len(loaded["rows"]) == 3


def test_contract_real_briefing_produces_zero_issues(tmp_path, monkeypatch):
    """契约③（零误报的正向证明）：真实生成 + 一致的独立源 → **0 issue**。

    用生成层**真的跑出来**的正文与侧车，配一个与生成层单位净值一致的独立源，
    四层检查必须全绿。这条绿了才说明 v3 不会在正常日子里天天红。
    """
    _weekday_calendar(monkeypatch)
    text, sidecar, push_file = _drive_generator(
        "qa_v3_green", BULUOGELI_TXNS, tmp_path, monkeypatch)
    _write_sidecar(push_file, sidecar)

    loaded, err = qc._load_sidecar(push_file)
    assert err == "", err

    v = qc.check_hallucination_v3(
        push_file, push_date=PUSH_DATE, sidecar=loaded,
        independent_provider=_provider_for(UNIT_NAV, ACCUM_NAV),
        qdii_detector=_domestic)
    assert v["issues"] == [], f"真实产出却判红（误报！）：{v['issues']}"
    assert v["checked"] == 3, v
    assert v["degraded"] == [], v
    assert v["unverified"] == [], v


def test_contract_render_matches_sidecar_exactly(tmp_path, monkeypatch):
    """契约④：生成层正文的每个数字都与它自己的侧车逐项对得上。

    只要有人改了正文的格式化（例如 ``.3f`` → ``.2f``）却没同步容差，
    这条会先红在单测里，而不是红在 22:00 的用户企微上。
    """
    text, sidecar, _ = _drive_generator(
        "qa_v3_fmt", BULUOGELI_TXNS, tmp_path, monkeypatch)
    rows = qc._parse_position_rows(text)
    assert len(rows) == 3, f"正文没解析出 3 行：\n{text}"
    issues, unver = qc._check_render_consistency(rows, sidecar)
    assert issues == [], f"正文与侧车对不上：{issues}"
    assert unver == [], unver


# ===========================================================================
# 9. 与 v9.9.57 既有路径的边界（不许顺手改坏）
# ===========================================================================

def test_no_sidecar_keeps_v9957_path(tmp_path, monkeypatch):
    """无侧车 → v9.9.57 的原路径照跑，**不得**出现 ``superseded_by_v3``。"""
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", _briefing(
        _summary(310.0, 264.3, -14.7),
        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3)))
    res = qc.evaluate_push_quality(
        PUSH_DATE, "U",
        actual_data_provider=lambda d, codes: {},
        holdings_provider=lambda uid, asof: (["163406"], {}),
    )
    assert "hallucination:superseded_by_v3" not in res["checks_skipped"], (
        "无侧车却让 v3 接管了 —— v9.9.57 路径被悄悄顶掉")


def test_with_sidecar_v3_takes_over(tmp_path, monkeypatch):
    """有侧车 → v3 接管（出现 ``superseded_by_v3`` 且不再打同源取数）。"""
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    f = _archive(tmp_path, f"{PUSH_DATE}_briefing_U.txt", _briefing(
        _summary(310.0, 264.3, -14.7),
        _row("163406", "兴全合润混合A", 2.627, 2.240, "▼", 14.7, 264.3)))
    _write_sidecar(f, _sidecar([_sc_row("163406", "兴全合润混合A", 2.2401)]))

    def _must_not_be_called(d, codes):
        raise AssertionError("v3 接管后不应再走 v9.9.57 的同源取数")

    res = qc.evaluate_push_quality(
        PUSH_DATE, "U",
        actual_data_provider=_must_not_be_called,
        holdings_provider=lambda uid, asof: (["163406"], {}),
        independent_provider=_provider_for({"163406": 2.2401},
                                           {"163406": 8.5191}),
        qdii_detector=_domestic,
    )
    assert "hallucination:superseded_by_v3" in res["checks_skipped"], res
    flat = " ".join(i for p in res["pushes"] for i in p["issues"])
    assert "口径不符" not in flat and "净值不符" not in flat, flat


# ===========================================================================
# 故障注入清单（跑法见本文件末尾 docstring 之外；每条对应上面的守卫）
# ---------------------------------------------------------------------------
#  1. 删掉 _load_sidecar 的 caliber 校验      → test_load_sidecar_caliber_mismatch_is_rejected 红
#  2. 删掉 _load_sidecar 的 schema 校验       → test_load_sidecar_schema_mismatch_is_rejected 红
#  3. _parse_trend 改回 utcfromtimestamp      → test_networth_trend_timezone_anchor 红
#  4. _check_render_consistency 恒返回 ([],[]) → test_render_consistency_catches_nav_drift 红
#  5. 去掉 _check_independent_caliber 的累计归因 → test_independent_caliber_flags_accumulated_nav_as_caliber_bug 红
#  6. 独立源失败时算成 checked                → test_independent_unreachable_is_degraded_not_pass 红
#  7. 去掉 freshness 的 max_domestic < expected → test_freshness_stale_is_degraded 红
#  8. _reason_class 恒返回 ""                 → test_reason_class_nav_mismatch 红
#  9. night_worker 侧车 caliber 改错字面量     → test_contract_* 全组红
# 10. night_worker 侧车 schema 改成 2         → test_contract_* 全组红
# 11. night_worker 侧车 row 字段改名          → test_contract_sidecar_shape_is_accepted_by_consumer 红
# ===========================================================================
