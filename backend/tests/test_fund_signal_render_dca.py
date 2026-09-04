"""P0-4 定投前瞻（render_dca）集中度段两个缺陷的回归。

缺陷 A（空行）：xray.industries 为空时仍拼出「集中度提醒（…）：」，冒号后空白。
缺陷 B（缺持仓截止日）：PRD §6.1 强制规则 4 + §6.1.1 H1 把定投集中度段列为
    穿透类内容，要求标注「持仓截止日 + 滞后天数」，原实现只有覆盖率。

全部离线：直接构造 XrayResult / Coverage dataclass 喂给 render_dca，
不发起任何网络请求、不写盘。
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.fund_signal.render import render_dca  # noqa: E402
from services.fund_signal.xray import (  # noqa: E402
    Coverage,
    IndustryExposure,
    XrayResult,
)


# ============================================================
# helpers
# ============================================================

@dataclass
class _Pos:
    """render_dca 只用到 p.code，其余字段留默认值即可。"""

    code: str = "013107"
    weight_mv: float = 50.0


def _coverage(end_date="2026-06-30", ann_date="2026-07-21", lag_days=21,
              penetrated_pct=61.8):
    return Coverage(
        penetrated_pct=penetrated_pct,
        blind_pct=0.0,
        residual_pct=100.0 - penetrated_pct,
        blind_funds=[],
        end_date=end_date,
        ann_date=ann_date,
        lag_days=lag_days,
        industry_source="sw_l2",
    )


def _xray(industries=None, **cov_kw):
    if industries is None:
        industries = [
            IndustryExposure("半导体", 33.5),
            IndustryExposure("通信设备", 13.3),
            IndustryExposure("电池", 8.1),   # 第 3 名不应出现在定投段（只取前 2）
        ]
    return XrayResult(
        industries=industries,
        stocks=[],
        coverage=_coverage(**cov_kw),
        triggered_rules=[],
    )


def _render(xray):
    """最小可用 snap：只有 xray，其余字段走默认分支。"""
    snap = {"nav_date": "20260903", "combo_cost_pct": -3.2, "xray": xray}
    return render_dca(snap, [_Pos()])


def _concentration_line(sig):
    """取出集中度那一行；没有则 ""。

    ⚠️ 断言必须收敛到这一行：顶部数据截止行本身就有「QDII 滞后 2 天」，
    用 whole-content 断言 "滞后" 会命中无关行，测不出降级。
    """
    for line in sig["content"].split("\n"):
        if "集中度提醒" in line:
            return line
    return ""


# ============================================================
# 缺陷 A：industries 为空 → 整行不输出
# ============================================================

def test_empty_industries_emits_no_concentration_line():
    """industries=[] 时不得出现空壳行「集中度提醒（…）：」。"""
    sig = _render(_xray(industries=[]))

    content = sig["content"]
    assert "集中度提醒" not in content, f"出现了空壳集中度行:\n{content}"


def test_empty_industries_also_suppresses_conclusion_line():
    """同一份数据的结论行（render.py L263）也不输出：拿不到穿透 → 都不说。"""
    sig = _render(_xray(industries=[]))

    assert "本期定投若仍投向" not in sig["content"]


def test_empty_industries_other_lines_survive():
    """缺陷 A 的修法必须是「只砍集中度行」，不能误伤顶部数据截止等既有行。"""
    sig = _render(_xray(industries=[]))

    content = sig["content"]
    assert "数据截止" in content
    assert "组合整体" in content


# ============================================================
# 缺陷 B 正常路径：持仓截止日 + 滞后天数，且年份不被吃掉
# ============================================================

def test_concentration_line_carries_holding_end_date_and_lag():
    sig = _render(_xray(end_date="2026-06-30", lag_days=21))

    content = sig["content"]
    assert "持仓截止 2026-06-30" in content, f"缺持仓截止日:\n{content}"
    assert "滞后 21 天" in content, f"缺滞后天数:\n{content}"
    assert "穿透覆盖 61.8% 净值" in content, f"覆盖率丢了:\n{content}"
    assert "半导体 33.5% ｜ 通信设备 13.3%" in content, f"行业敞口不对:\n{content}"


def test_end_date_keeps_full_year_not_compressed_to_mmdd():
    """年份不得被吃掉：必须是 YYYY-MM-DD，不是 06-30。

    单独断言「持仓截止 06-30」不存在 —— 只断 "2026-06-30" in content 的话，
    一旦有人用 _fmt_md 压成 "06-30"，"2026-06-30" 会消失，理论上也能抓到；
    但本条反向断言让失败原因一目了然（而不是一堆 assert in 里挑一个）。
    """
    sig = _render(_xray(end_date="2026-06-30", lag_days=21))

    content = sig["content"]
    assert "持仓截止 06-30" not in content, f"年份被压掉了:\n{content}"
    assert "持仓截止 2026-06-30" in content


def test_concentration_line_full_text_is_exact():
    """锁定整行文案，防止标点/顺序被悄悄改动。"""
    sig = _render(_xray(end_date="2026-06-30", lag_days=21))

    line = [l for l in sig["content"].split("\n") if "集中度提醒" in l]
    assert len(line) == 1, sig["content"]
    assert line[0] == (
        "集中度提醒（持仓截止 2026-06-30，滞后 21 天，穿透覆盖 61.8% 净值）："
        "半导体 33.5% ｜ 通信设备 13.3%"
    )


def test_only_top_two_industries_are_rendered():
    """定投段只取前 2 大行业，第 3 名「电池」不得出现。"""
    sig = _render(_xray(end_date="2026-06-30", lag_days=21))

    assert "电池" not in sig["content"]


def test_xray_none_emits_no_concentration_line():
    """对照组：xray 为 None 时（未启用穿透）同样不输出集中度行。"""
    sig = _render(None)

    assert "集中度提醒" not in sig["content"]


# ============================================================
# 缺陷 B 降级路径：时点算不出来 → 只丢子句，保留覆盖率
# ============================================================

@pytest.mark.parametrize("cov_kw", [
    {"end_date": "", "lag_days": 21},     # 持仓截止日缺失
    {"end_date": "2026-06-30", "lag_days": 0},   # 滞后天数解析失败
    {"end_date": "", "lag_days": 0},      # 两者都缺
])
def test_degrades_to_coverage_only_when_stamp_unavailable(cov_kw):
    """降级不得把整行删掉：覆盖率和行业敞口本身仍然有效。"""
    sig = _render(_xray(**cov_kw))

    line = _concentration_line(sig)
    assert line, f"整行被误删:\n{sig['content']}"
    assert "持仓截止" not in line, f"脏数据仍拼了持仓截止日:\n{line}"
    assert "滞后" not in line, f"脏数据仍拼了滞后天数:\n{line}"
    assert "穿透覆盖 61.8% 净值" in line, f"覆盖率丢了:\n{line}"
    assert "半导体 33.5%" in line, f"行业敞口丢了:\n{line}"


def test_negative_lag_days_also_degrades():
    """ann_date < end_date 的脏数据（滞后为负）同样走降级。"""
    sig = _render(_xray(end_date="2026-06-30", lag_days=-3))

    line = _concentration_line(sig)
    assert line, f"整行被误删:\n{sig['content']}"
    assert "持仓截止" not in line, f"负数滞后仍拼了日期:\n{line}"
    assert "穿透覆盖 61.8% 净值" in line
