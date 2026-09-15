"""晨报「组合温度计」接线回归测试（v9.9.x）
==========================================

死码背景
--------
``night_worker`` 生成晨报时，用下面这种**字符串 replace** 把组合温度计插到
持仓速览块之前::

    user_briefing = user_briefing.replace(
        f"📋 【{name} 持仓诊断】", f"{thermometer}\\n\\n📋 【{name} 持仓诊断】")

但块的真实标题是「📋 【{name} 持仓速览】」（见 ``_render_holdings_block``）。
两者在 2026-08-09 同一次 commit 里分叉：标题被改成「持仓速览」，replace 的
目标串却仍是「持仓诊断」→ replace **恒不命中** → 组合温度计**从未进过任何
一份晨报**（生来即死的死码）。

修复方式
--------
把温度计改成由 ``_render_holdings_block`` **直接拼进块首**，并新增
``_render_user_briefing`` 组装最终正文（温度计从 ``uid`` 现算）。
从根上消掉"靠字符串 replace 找标题"这种脆弱做法。

本文件断言**最终 user_briefing 里真的出现温度计文本**（这就是死码活了/没活的
判据），而不是去断言"调用过 replace"。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scripts.night_worker as nw                        # noqa: E402

UID = "LeiJiang"
NAME = "LeiJiang"
BRIEFING = "📊 2026-09-15 钱袋子晨报\n\n📝 【AI研判】\n今日中性偏多"
DIAG = "总评：持仓分散良好。"
ADVICE = "\n【操作建议】\n• 保持定投"
THERMO = "🌡️ 【组合温度计】当前组合温度 42°C（中性）"
TITLE = f"📋 【{NAME} 持仓速览】"


def test_thermometer_reaches_final_briefing(monkeypatch):
    """核心用例：温度计必须真的出现在**最终 user_briefing** 里。

    注入/退化形态：若 ``_render_holdings_block`` 不再拼装温度计（或有人把接线
    改回脆弱的字符串 replace），本用例立刻红 —— 这正是"死码"的判据。
    """
    monkeypatch.setattr(nw, "_build_portfolio_thermometer",
                        lambda uid: THERMO, raising=True)

    out = nw._render_user_briefing(BRIEFING, NAME, DIAG, ADVICE, [], UID)

    assert THERMO in out, f"温度计没进最终晨报（死码复发）:\n{out}"
    # 位置：温度计在「持仓速览」标题**之前**（与 v9.5.76 原意一致）
    assert out.index(THERMO) < out.index(TITLE), "温度计位置不对，应在标题之前"
    # 块的既有结构不能被破坏
    assert DIAG in out
    assert out.endswith("⚠️ AI建议仅供参考，不构成投资建议")


def test_no_thermometer_when_builder_returns_empty(monkeypatch):
    """边界：温度计返回空串 → 不出现、也不留空行占位。"""
    monkeypatch.setattr(nw, "_build_portfolio_thermometer",
                        lambda uid: "", raising=True)

    out = nw._render_user_briefing(BRIEFING, NAME, DIAG, ADVICE, [], UID)

    assert THERMO not in out
    assert TITLE in out, "温度计为空时块的基本结构仍须在"


def test_render_holdings_block_places_thermometer_before_title():
    """块级（结果断言）：显式传 thermometer → 出现且在标题之前；不传 → 不出现。"""
    with_t = nw._render_holdings_block(NAME, DIAG, ADVICE, [], THERMO)
    assert THERMO in with_t
    assert with_t.index(THERMO) < with_t.index(TITLE)

    without_t = nw._render_holdings_block(NAME, DIAG, ADVICE, [])
    assert THERMO not in without_t
    assert without_t.startswith(TITLE), f"无温度计时块首应是标题: {without_t!r}"


def test_fragile_title_replace_is_gone():
    """回潮守卫（弱，扫源码文本）：旧的脆弱 replace 目标串不应再出现。

    旧目标串是「📋 【{name} 持仓诊断】」——特征子串「持仓诊断】」。只要它再次
    出现，就说明有人又把"靠字符串 replace 找标题"的写法加回来了。
    注意区分日志/文案里的「持仓诊断」（不带右括号「】」）。
    """
    src = (BACKEND_DIR / "scripts" / "night_worker.py").read_text(encoding="utf-8")
    assert "持仓诊断】" not in src, (
        "night_worker 又出现了按「持仓诊断】」做 replace 的脆弱写法")
