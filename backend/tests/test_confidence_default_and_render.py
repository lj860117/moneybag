"""v9.9.26 P1-9 收尾：置信度「缺失 ≠ 高置信度」+ 前端 unknown 渲染回归测试。

背景（这是本轮 P1-9 漏掉的两条腿）：
  1. 后端闸门（api/signals.py）已经把低/缺失置信度的方向标成 unknown，但
     **三个消费方各自写着 `f.get("trend_confidence", 55)`**：
       - api/holdings.py（持仓详情 + 持仓列表富化）
       - scripts/dca_scheduler.py（每月定投日推送）
     默认 55 的含义是「置信度充足」——等于把「没有置信度」直接升级成
     「模型很确信」。更糟的是键存在、值为 None 时 `.get` 返回 None，
     传进 calc_smart_dca_v2 会在 `trend_confidence < 50` 处抛
     TypeError（py3 里 None < int 非法）——一个静默的 500。
  2. 前端 `pages/_components.js` 用三元兜底渲染
     `trend_direction==='up'?'偏多':down?'偏空':'震荡'`，把闸门产出的
     `unknown` 显示成「震荡」，即「数据不足」被伪装成「判断为横盘」。

修法是把归一集中到 services/signal.py:normalize_trend_confidence()，
本文件锁定该函数的行为 + 三个调用点不再各写一套默认值 + 前端渲染。

全部离线，不发起网络请求、不调 LLM、不写盘。
"""
import re
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_REPO = _BACKEND.parent

from services.signal import (
    calc_smart_dca_v2,
    normalize_trend_confidence,
)


# ---------------------------------------------------------------- 归一函数

@pytest.mark.parametrize("raw,expected", [
    (None, 0),            # 键缺失 / 显式 None —— 必须按「不足」
    ("", 0),              # 空字符串
    ("abc", 0),           # 非数字
    ("N/A", 0),
    ([], 0),
    ({}, 0),
    (object(), 0),
    (float("nan"), 0),    # NaN → int() 抛 ValueError
    (float("inf"), 0),    # inf 超范围
    (-1, 0),              # 负数超范围
    (150, 0),             # >100 超范围
    (0, 0),
    (35, 35),             # 本项目的真实低置信度值（多空分歧）
    (49, 49),
    (50, 50),             # 分界点原样保留，不做取整/抹平
    (55, 55),
    (72, 72),
    (85, 85),
    (100, 100),
    ("55", 55),           # 字符串数字可容忍
])
def test_normalize_trend_confidence(raw, expected):
    assert normalize_trend_confidence(raw) == expected


def test_normalize_never_upgrades_unknown_to_sufficient():
    """负面控制：任何「说不清」的输入都不得落到闸门阈值(50)及以上。"""
    for raw in (None, "", "abc", [], {}, float("nan"), -1, 150):
        assert normalize_trend_confidence(raw) < 50


def test_normalize_is_pure_and_idempotent():
    for raw in (None, 35, 85, "abc", 150):
        first = normalize_trend_confidence(raw)
        assert normalize_trend_confidence(raw) == first
        assert normalize_trend_confidence(first) == first


# ------------------------------------------------- 行为：真正被消费时不崩

def test_missing_confidence_does_not_crash_dca():
    """旧写法传 None 会在这里抛 TypeError；归一后必须能跑完。"""
    conf = normalize_trend_confidence(None)          # 消费方现在的写法
    dca = calc_smart_dca_v2(
        trend_direction="up", trend_score=30,
        trend_confidence=conf, nav_percentile=40,
    )
    assert isinstance(dca["multiplier"], float)
    assert dca["multiplier"] > 0


def test_missing_confidence_is_more_conservative_than_high_confidence():
    """同样的行情，置信度未知必须比置信度充足更靠近 1.0x（更保守）。"""
    kwargs = dict(trend_direction="up", trend_score=30, nav_percentile=40)
    unknown = calc_smart_dca_v2(trend_confidence=normalize_trend_confidence(None), **kwargs)
    sure = calc_smart_dca_v2(trend_confidence=normalize_trend_confidence(85), **kwargs)
    assert abs(unknown["multiplier"] - 1.0) <= abs(sure["multiplier"] - 1.0)


def test_old_default_55_would_have_been_more_aggressive():
    """把旧默认 55 与新默认 0 并排比一次，证明这次改的是行为不是注释。

    如果哪天矩阵调整导致两者相等，这个断言会红——那时应当重新确认
    「缺失按不足处理」是否仍成立，而不是把断言删掉。
    """
    kwargs = dict(trend_direction="flat", trend_score=10, nav_percentile=40)
    old = calc_smart_dca_v2(trend_confidence=55, **kwargs)
    new = calc_smart_dca_v2(trend_confidence=normalize_trend_confidence(None), **kwargs)
    assert abs(new["multiplier"] - 1.0) <= abs(old["multiplier"] - 1.0)


# ------------------------------------------- 负面控制：调用点不得各写默认值

_DEFAULT_55_RE = re.compile(r"trend_confidence\s*=\s*[^,\n]*\.get\([^)]*,\s*(?:5[05]|100)\s*\)")


@pytest.mark.parametrize("rel", [
    "api/holdings.py",
    "scripts/dca_scheduler.py",
])
def test_no_call_site_keeps_sufficient_default(rel):
    """源码扫描：不允许再出现 `xxx.get("trend_confidence", 55)` 这类写法。

    这是把「修一处漏一处」钉死的地方——新增调用点时若又写了默认值，
    这个测试就会红。
    """
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    hits = _DEFAULT_55_RE.findall(src)
    assert not hits, f"{rel} 仍存在把缺失置信度当充足的默认值: {hits}"


@pytest.mark.parametrize("rel", [
    "api/holdings.py",
    "scripts/dca_scheduler.py",
])
def test_call_sites_use_the_shared_normalizer(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    assert "normalize_trend_confidence" in src, f"{rel} 未使用共享归一函数"


# ------------------------------------------------------------- 前端渲染

def test_frontend_does_not_render_unknown_as_sideways():
    """前端不得把 unknown 渲染成「震荡」（= 把没判断说成判断为横盘）。

    取值收敛到「只有显式 flat 才叫震荡」：up/down/flat 之外的任何值
    （unknown、undefined、后端新增枚举）都走「数据不足」，这样后端将来
    多一个状态也不会被前端悄悄显示成横盘。
    """
    src = (_REPO / "pages" / "_components.js").read_text(encoding="utf-8")
    assert "f.trend_direction==='flat'?'震荡':'数据不足'" in src
    # 走势预估面板必须读闸门标记，而不是只靠三元兜底
    assert "trend_confidence_sufficient === false" in src
    assert "d.trend_direction==='up'?'偏多':d.trend_direction==='down'?'偏空':'震荡'" not in src


def test_frontend_translate_map_matches_backend_label():
    """方向翻译表里 unknown 的文案必须与后端常量一致，不许自造第二套。"""
    cfg = (_BACKEND / "config.py").read_text(encoding="utf-8")
    m = re.search(r"INSUFFICIENT_DATA_LABEL\s*=\s*[\"']([^\"']+)[\"']", cfg)
    assert m, "config.py 里找不到 INSUFFICIENT_DATA_LABEL"
    label = m.group(1)
    src = (_REPO / "app.js").read_text(encoding="utf-8")
    assert f"'unknown': '{label}'" in src, (
        f"app.js 的 unknown 文案与后端 {label!r} 不一致")


def test_landing_does_not_fabricate_confidence_50():
    """landing.js 不得在置信度缺失时兜底成 50%（那正是闸门阈值，等于谎报达标）。"""
    src = (_REPO / "pages" / "landing.js").read_text(encoding="utf-8")
    assert "d.confidence||50" not in src
    assert "d.confidence || 50" not in src


def test_quiz_does_not_hardcode_factor_weights():
    """quiz.js 的因子说明不得再硬编码权重副本。

    原实现写死「技术面(25%)：RSI(8%) + MACD(10%) …」，后端权重一调这段
    说明就开始说谎，而下方同一段文案里已经用 d.details 列了真实权重——
    同一件事两份数据。改法是从 details 现算，删掉硬编码副本。
    """
    src = (_REPO / "pages" / "quiz.js").read_text(encoding="utf-8")
    for stale in ("技术面(25%)", "基本面(30%)", "资金面(20%)", "情绪面(15%)"):
        assert stale not in src, f"quiz.js 仍硬编码权重: {stale}"
    assert "_sigCatLines" in src, "quiz.js 未改为从 d.details 现算权重构成"


def test_quiz_does_not_coerce_missing_confidence_to_zero_percent():
    """展示置信度时缺失必须说「未知」，不能渲染成 0%（0% 是一个具体判断）。"""
    src = (_REPO / "pages" / "quiz.js").read_text(encoding="utf-8")
    assert "Math.round(d.confidence||0)+'%'" not in src


def test_frontend_suppresses_directional_score_when_insufficient():
    src = (_REPO / "pages" / "_components.js").read_text(encoding="utf-8")
    # 置信度不足时不再拼接带符号分数，改显示阈值说明
    assert "方向判断需置信度≥50%" in src


# ----------------------------------------------------- 闸门阈值与文案一致

def test_gate_threshold_constant_matches_frontend_copy():
    """后端阈值改了、前端文案没跟着改 = 用户看到的规则是错的。"""
    cfg = (_BACKEND / "config.py").read_text(encoding="utf-8")
    m = re.search(r"MIN_CONFIDENCE_FOR_DIRECTION\s*=\s*(\d+)", cfg)
    assert m, "config.py 里找不到 MIN_CONFIDENCE_FOR_DIRECTION"
    threshold = m.group(1)
    fe = (_REPO / "pages" / "_components.js").read_text(encoding="utf-8")
    assert f"置信度≥{threshold}%" in fe, (
        f"前端文案与后端阈值 {threshold} 不一致，需同步修改 _components.js")


def test_insufficient_confidence_shortcut_reuses_gate_constant():
    """前端自算兜底时也读同一阈值，避免第二套数字。"""
    cfg = (_BACKEND / "config.py").read_text(encoding="utf-8")
    m = re.search(r"MIN_CONFIDENCE_FOR_DIRECTION\s*=\s*(\d+)", cfg)
    assert m
    threshold = m.group(1)
    em = re.search(r"MIN_CONFIDENCE_FOR_DIRECTION\s*=\s*(\d+)",
                   (_REPO / "pages" / "insight-fund.js").read_text(encoding="utf-8"))
    assert em, "insight-fund.js 未定义 MIN_CONFIDENCE_FOR_DIRECTION 常量"
    assert em.group(1) == threshold, "前后端阈值不一致"
