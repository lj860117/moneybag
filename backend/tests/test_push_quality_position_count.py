#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报「持仓明细行数 == 真实持仓只数」硬断言回归测试（v9.9.59）。

## 为什么需要这条断言

2026-09-16 ~ 09-18 连续三天，BuLuoGeLi 晨报「组合温度计」里占比约 75% 的
最大持仓 **163406 兴全合润混合A** 被幻觉删句逻辑**整行删除**，而汇总行的
「当前市值 ¥1333」仍含它的市值 —— 用户看到「总市值 1333 / 明细合计 328」。
现有质检是逐行核对**数值**，行数少一行它只会少核一行，**不会报"少了一行"**。

删句缺陷已在 v9.9.58 修掉，本断言补的是「整行消失」这**另一类**缺陷的
唯一守门人。

## 误报评估（生产只读实测，2026-09-19）

221 份生产归档 dry-run（/tmp 内，不带 --alert）：

    ISSUE  : 3   ← 全是真阳性（09-16/17/18 BuLuoGeLi，缺失 163406）
    SKIPPED: 214 ← 全部是 position_count:no_block（该段 2026-09-16 才引入，
                   09-15 及更早的晨报 + 全部 closing_review 都没有）
    OK     : 3   ← 09-16/17/18 LeiJiang（8 行 / 8 只）
    误报   : 0

⇒ 真实数据下不会天天红。三个豁免场景（无段 / 基准取不到 / 当天有交易）
各自有一条用例锁住，且**记 skipped 而不是静默跳过**。

## 硬要求

- **必须能精确红**：删掉一行 → 红。恒绿的守卫等于没守卫。
- **反向用例**：行数齐全 → 不报。
- **绝不打真实数据源 / 绝不读真实用户档案**：基准一律注入或用 tmp_path。
"""
import hashlib
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import daily_push_quality_check as qc  # noqa: E402


# ---------------------------------------------------------------------------
# 构造用晨报（格式逐字照抄生产生成层 scripts/night_worker.py）
# ---------------------------------------------------------------------------

HEADER = "=== 2026-09-18 08:30:19 ===\n☀️ 早安，BuLuoGeLi！\n\n📊 2026-09-18 钱袋子晨报\n"


def _row(code: str, name: str, buy: float, cur: float, arrow: str,
         pct: float, val: float) -> str:
    return f"  • {name}({code})  买入{buy:.3f} → 现{cur:.3f}  {arrow}{pct:.1f}%  ¥{val:.1f}"


# 生产 2026-09-18 BuLuoGeLi 的三只持仓（事故形态：163406 那一行被删）
ROW_100038 = _row("100038", "富国沪深300指数增强A", 2.040, 2.461, "▲", 20.6, 120.6)
ROW_009708 = _row("009708", "工银新兴制造混合C", 4.422, 4.589, "▲", 3.8, 207.6)
ROW_163406 = _row("163406", "兴全合润混合(LOF)A", 2.624, 8.519, "▲", 224.7, 1005.0)
# 净值取不到的形态：同样占一行（生成层「只标注不删」，不会整行消失）
ROW_005698_MISSING = (
    "  • 华夏全球科技先锋混合(005698)  买入3.530 → 现净值缺失 ⚠️  ¥75.0（按成本计）"
)

SUMMARY_3 = (
    "📊 组合温度计（截至近日收盘）\n"
    "总投入 ¥610  当前市值 ¥1333  整体浮盈 📈 +118.6%\n"
    "\n"
    "持仓明细：\n"
)


def _briefing(*rows: str, summary: str = SUMMARY_3) -> str:
    return HEADER + "\n" + summary + "\n".join(rows) + "\n"


ALL_3_ROWS = (ROW_163406, ROW_100038, ROW_009708)


def _write_user(tmp_path: pathlib.Path, user_id: str, payload: dict) -> None:
    """把用户档案写到 tmp_path/users/{sha256(uid)[:16]}.json。"""
    users = tmp_path / "users"
    users.mkdir(parents=True, exist_ok=True)
    safe = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    (users / f"{safe}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _txn(code: str, typ: str = "BUY", shares: float = 100.0,
         date: str = "2026-05-21T00:00:00", **kw) -> dict:
    t = {"id": f"tx_{code}_{typ}", "type": typ, "code": code,
         "name": f"基金{code}", "amount": 100, "shares": shares,
         "nav": 1.0, "date": date}
    t.update(kw)
    return t


def _portfolio(*txns) -> dict:
    return {"userId": "u", "portfolio": {"transactions": list(txns)}}


# ===========================================================================
# 核心：故障注入必须红 / 齐全必须不红
# ===========================================================================


def test_rows_complete_is_green():
    """反向用例：行数齐全（3 行 / 3 只）→ 不报，且不留 skipped。"""
    issues, skipped = qc.check_position_count(
        _briefing(*ALL_3_ROWS), ["163406", "100038", "009708"], {}
    )
    assert issues == [], issues
    # 真的核对过，不是靠豁免混过去的
    assert [s for s in skipped if s.startswith("position_count:")] == [], skipped


@pytest.mark.parametrize("drop", [0, 1, 2])
def test_drop_any_row_turns_red(drop):
    """故障注入：删掉**任意**一行 → 必须红，且点名缺失的代码。

    这是本断言的存在理由：数值核对看不见「整行消失」。
    """
    rows = [r for i, r in enumerate(ALL_3_ROWS) if i != drop]
    issues, _skipped = qc.check_position_count(
        _briefing(*rows), ["163406", "100038", "009708"], {}
    )
    assert len(issues) == 1, issues
    assert "持仓明细行数与真实持仓只数不符" in issues[0]
    assert "缺失" in issues[0]


def test_drop_biggest_holding_reproduces_the_incident():
    """复刻事故：删掉占比最大的 163406（其余两行数值全对）→ 红。

    事故里「当前市值 ¥1333」仍含 163406 的市值，逐行数值核对全绿 ——
    只有行数断言能抓住它。
    """
    issues, _ = qc.check_position_count(
        _briefing(ROW_100038, ROW_009708), ["163406", "100038", "009708"], {}
    )
    assert len(issues) == 1
    assert "163406" in issues[0]
    assert "渲染 2 行" in issues[0]
    assert "真实持仓 3 只" in issues[0]


def test_extra_row_also_turns_red():
    """多出一行（渲染了已清仓的基金）同样要红 —— 断言是双向的。"""
    issues, _ = qc.check_position_count(
        _briefing(*ALL_3_ROWS, _row("000001", "华夏成长混合", 1.0, 1.1, "▲", 10.0, 50.0)),
        ["163406", "100038", "009708"], {},
    )
    assert len(issues) == 1
    assert "多出 000001" in issues[0]


def test_duplicate_row_turns_red():
    """同一只渲染两行（行数凑对了但内容错）→ 红。"""
    issues, _ = qc.check_position_count(
        _briefing(ROW_163406, ROW_163406, ROW_100038),
        ["163406", "100038"], {},
    )
    assert any("重复渲染" in i for i in issues), issues


def test_block_present_but_zero_rows_turns_red():
    """段头在、一行都没有 → 红（不许因为「解析不出」就放行）。"""
    issues, _ = qc.check_position_count(
        _briefing(), ["163406", "100038", "009708"], {}
    )
    assert len(issues) == 1
    assert "0 行" in issues[0]


# ===========================================================================
# 豁免：必须对合理场景让路，且**如实记 skipped**（本项目禁止静默通过）
# ===========================================================================


def test_exempt_when_no_position_block():
    """豁免①：整段不存在（09-15 及更早的归档 / closing_review）→ skipped。

    不豁免的话 200+ 份历史归档会一夜之间集体变红 —— 那是本断言最不能犯的错。
    """
    content = HEADER + "\n📝 【AI研判】\n一句话：市场中性。\n"
    issues, skipped = qc.check_position_count(content, ["163406"], {})
    assert issues == []
    assert "position_count:no_block" in skipped


def test_exempt_when_holdings_source_unavailable():
    """豁免③：基准算不出来 → skipped，**绝不当成「0 只」放过**。"""
    for reason in ("no_user_file", "no_transactions", "unreadable_user_file"):
        issues, skipped = qc.check_position_count(
            _briefing(*ALL_3_ROWS), None, {"reason": reason}
        )
        assert issues == [], (reason, issues)
        assert f"position_count:{reason}" in skipped, (reason, skipped)


def test_exempt_when_holdings_changed_on_push_day():
    """豁免②：差异代码在晨报当天有交易 → 记 skipped，不误报。

    晨报 01:00 生成、质检 22:00 跑，中间补录一笔就会让基准漂移。
    宁可这一条漏报，也不在 22:00 打扰真人用户。
    """
    diag = {"same_day_codes": ["163406"]}
    issues, skipped = qc.check_position_count(
        _briefing(ROW_100038, ROW_009708), ["163406", "100038", "009708"], diag
    )
    assert issues == []
    assert "position_count:holdings_changed_same_day:163406" in skipped


def test_same_day_exemption_does_not_mask_other_missing_rows():
    """豁免②只豁免**当天有交易**的那只，其余缺失照样报 —— 不连带放行。"""
    diag = {"same_day_codes": ["163406"]}      # 163406 与 100038 都缺，只豁免前者
    issues, skipped = qc.check_position_count(
        _briefing(ROW_009708), ["163406", "100038", "009708"], diag
    )
    assert any("缺失 100038" in i for i in issues), issues
    assert not any("163406" in i for i in issues), issues   # 被豁免的那只不进 issue
    assert "position_count:holdings_changed_same_day:163406" in skipped


def test_nav_missing_row_still_counts_as_rendered():
    """净值取不到时生成层渲染「现净值缺失」行（只标注不删）→ 仍算一行。

    ⇒ 不存在「净值取不到就不渲染」的合理少渲染场景，无需为此豁免。
    """
    issues, skipped = qc.check_position_count(
        _briefing(ROW_100038, ROW_005698_MISSING), ["100038", "005698"], {}
    )
    assert issues == [], issues
    assert [s for s in skipped if s.startswith("position_count:")] == [], skipped


# ===========================================================================
# 基准口径（load_active_holding_codes）—— 与生成层 night_worker 同口径
# ===========================================================================


def test_load_active_holding_codes_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("USERS_DIR", str(tmp_path / "users"))
    _write_user(tmp_path, "U1", _portfolio(
        _txn("163406", shares=100), _txn("100038", shares=50)))
    codes, diag = qc.load_active_holding_codes("U1")
    assert codes == ["100038", "163406"]
    assert diag["reason"] == ""
    assert diag["same_day_codes"] == []


def test_load_active_holding_codes_excludes_fully_sold(tmp_path, monkeypatch):
    """已清仓（剩余份额 <= 1e-6）不算活跃 —— 与 night_worker 一致。"""
    monkeypatch.setenv("USERS_DIR", str(tmp_path / "users"))
    _write_user(tmp_path, "U1", _portfolio(
        _txn("163406", "BUY", shares=100),
        _txn("163406", "SELL", shares=100),
        _txn("100038", "BUY", shares=50),
        _txn("100038", "SELL", shares=20),   # 部分卖出仍活跃
    ))
    codes, _ = qc.load_active_holding_codes("U1")
    assert codes == ["100038"]


def test_load_active_holding_codes_sell_shares_inferred(tmp_path, monkeypatch):
    """SELL 缺份额时用 到账金额 ÷ 确认净值 反推（照抄 night_worker）。"""
    monkeypatch.setenv("USERS_DIR", str(tmp_path / "users"))
    _write_user(tmp_path, "U1", _portfolio(
        _txn("163406", "BUY", shares=100),
        _txn("163406", "SELL", shares=0, amount=100, nav=2.0),  # 反推 50 份
    ))
    codes, _ = qc.load_active_holding_codes("U1")
    assert codes == ["163406"]      # 还剩 50 份


def test_load_active_holding_codes_asof_and_same_day(tmp_path, monkeypatch):
    """asof 过滤：晨报生成之后的交易不算基准；当天有交易的代码要报出来。"""
    monkeypatch.setenv("USERS_DIR", str(tmp_path / "users"))
    _write_user(tmp_path, "U1", _portfolio(
        _txn("163406", shares=100, date="2026-05-21T00:00:00"),
        _txn("100038", shares=50, date="2026-09-18T10:00:00"),   # 当天盘中补录
        _txn("009708", shares=50, date="2026-09-20T10:00:00"),   # 晨报之后
    ))
    codes, diag = qc.load_active_holding_codes("U1", "2026-09-18")
    assert codes == ["100038", "163406"]        # 009708 被 asof 过滤掉
    assert diag["same_day_codes"] == ["100038"]


def test_load_active_holding_codes_missing_file_is_none(tmp_path, monkeypatch):
    """档案不存在 → codes 必须是 None（不是 []）：[] 会被当成「0 只」静默通过。"""
    monkeypatch.setenv("USERS_DIR", str(tmp_path / "users"))
    codes, diag = qc.load_active_holding_codes("nobody")
    assert codes is None
    assert diag["reason"] == "no_user_file"


def test_user_portfolio_path_uses_sha256_not_raw_userid(monkeypatch):
    """路径必须用 sha256(uid)[:16]：算错会静默落到「基准取不到」= 断言形同虚设。"""
    monkeypatch.setenv("USERS_DIR", "/tmp/whatever/users")
    p = qc._user_portfolio_path("LeiJiang")
    assert p.name == hashlib.sha256(b"LeiJiang").hexdigest()[:16] + ".json"
    assert "LeiJiang" not in str(p)     # 裸 userId 不做文件名


# ===========================================================================
# 端到端：接进 evaluate_push_quality 后能驱动 FAIL
# ===========================================================================


def _archive(tmp_path: pathlib.Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")


def test_evaluate_fails_when_row_is_missing(tmp_path, monkeypatch):
    """端到端：晨报少一行 → status=FAIL 且 issue 在列表里（会进 22:00 告警）。"""
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    _archive(tmp_path, "2026-09-18_briefing_BuLuoGeLi.txt",
             _briefing(ROW_100038, ROW_009708))
    res = qc.evaluate_push_quality(
        "2026-09-18", "BuLuoGeLi",
        actual_data_provider=lambda d, codes: {},
        holdings_provider=lambda uid, asof: (["163406", "100038", "009708"], {}),
    )
    assert res["status"] == "FAIL", res
    flat = " ".join(res["issues"] + [i for p in res["pushes"] for i in p["issues"]])
    assert "163406" in flat, flat


def test_evaluate_passes_when_rows_complete(tmp_path, monkeypatch):
    """端到端反向：行数齐全 → 不因本断言产生任何 blocking issue。"""
    monkeypatch.setattr(qc, "PUSH_ARCHIVE_DIR", str(tmp_path))
    _archive(tmp_path, "2026-09-18_briefing_BuLuoGeLi.txt", _briefing(*ALL_3_ROWS))
    res = qc.evaluate_push_quality(
        "2026-09-18", "BuLuoGeLi",
        actual_data_provider=lambda d, codes: {},
        holdings_provider=lambda uid, asof: (["163406", "100038", "009708"], {}),
    )
    flat = " ".join(res["issues"] + [i for p in res["pushes"] for i in p["issues"]])
    assert "持仓明细行数" not in flat, flat
    assert not any(s.startswith("position_count:non") for s in res["checks_skipped"])
