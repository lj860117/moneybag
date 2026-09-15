"""「美股/QDII 欠配方向」判据回归测试（v9.9.x）
==============================================

真 bug 背景
-----------
再平衡"欠配方向"提示（绿色徽章）用的关键词列表 ``us_keywords`` 里**混进了
"港股"**。港股/港股通基金法律上**既非美股、也非 QDII**（走的是互联互通额度），
``fund_taxonomy`` 全市场实测 "港股" 440 命中、真值精度≈0，几乎全是港股通基金。

后果：一只港股通基金被打上「⬇ 补仓方向（美股/QDII欠配-22%）」，渲染成绿色
徽章（``pages/insight-fund.js:85`` / ``pages/analysis.js:1300``）→ **诱导用户
买港股去补美股桶**。

判据已从 ``_enrich_fund_holding_relation`` 的**嵌套函数**提到**模块级**
``api.signals._is_gap_match``，本文件直接调生产代码（而不是在测试里复刻一份）。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.signals as sig                                # noqa: E402

US_GAP_LABEL = "⬇ 补仓方向（美股/QDII欠配-22%）"


# ============================================================
# A. 修复本身：港股/港股通不得命中"美股桶"
# ============================================================

@pytest.mark.parametrize("name", [
    "天弘港股通精选A",
    "华夏恒生ETF联接A",
    "华宝港股通低波红利A",   # 命中红利低波桶可以，但绝不能是美股桶
])
def test_hk_funds_do_not_get_us_gap_label(name):
    """港股/港股通基金不得被打上「美股/QDII欠配-22%」。"""
    _hit, hint = sig._is_gap_match(name, "000001")
    assert hint != US_GAP_LABEL, f"{name} 被误标为美股/QDII欠配补仓方向: {hint}"


def test_pure_hk_fund_triggers_no_gap_at_all():
    """纯净港股基金（名字不含红利/价值等）→ 完全不触发任何欠配方向。

    旧列表含 "港股" 时这里会返回 (True, US_GAP_LABEL)；删词后应为 (False, "")。
    """
    hit, hint = sig._is_gap_match("天弘港股通精选A", "000001")
    assert (hit, hint) == (False, ""), f"港股基金不该命中欠配方向: {(hit, hint)}"


# ============================================================
# B. 防修过头：真美股 / 真 QDII 仍须命中
# ============================================================

@pytest.mark.parametrize("name", [
    "博时标普500ETF联接A",       # 标普 → 美股
    "国泰纳斯达克100指数",        # 纳斯达克 → 美股（简称被截断的真 QDII）
    "华夏野村日经225ETF(QDII)",   # 日经 + QDII
    "易方达海外收益债券A",         # 海外
])
def test_real_us_or_qdii_funds_still_match(name):
    """删 "港股" 不能把真美股/QDII 一起删没了。"""
    hit, hint = sig._is_gap_match(name)
    assert hit is True and hint == US_GAP_LABEL, f"{name} 漏判: {(hit, hint)}"


# ============================================================
# C. 回归：改动没有波及"红利低波"分支
# ============================================================

def test_dividend_lowvol_branch_still_works():
    """红利低波方向（欠配 -15%）仍可用。"""
    hit, hint = sig._is_gap_match("南方红利低波50ETF联接A")
    assert hit is True and "红利低波欠配-15%" in hint


def test_neutral_fund_matches_nothing():
    """既非美股/QDII 也非红利低波的普通基金 → 不命中。"""
    assert sig._is_gap_match("兴全合润混合A") == (False, "")


# ============================================================
# D. 弱守卫：源码文本层面，us_keywords 里不得再出现 "港股"
# ============================================================

def test_us_keywords_line_has_no_hk_keyword():
    """行为断言已在 A 节；这条只在 "港股" 以别的形式悄悄回来时提醒。"""
    src = (BACKEND_DIR / "api" / "signals.py").read_text(encoding="utf-8")
    us_lines = [ln for ln in src.splitlines() if "us_keywords = [" in ln]
    assert us_lines, "找不到 us_keywords 定义 —— 结构变了，守卫已空转"
    assert "港股" not in us_lines[0], f"us_keywords 又混进了 港股: {us_lines[0]}"
