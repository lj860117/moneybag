"""max_drawdown 符号契约测试（2026-09-13）。

背景 —— 同一个字段名 `max_drawdown` 在库内存在**两种相反的符号约定**，目前
各自独立成立，只因为它们的载荷从未合并：

  A. `services.fund_screen.build_drawdown_metrics` → **负百分比**（-20.0）
     消费方：`api/signals.py` 维度7，比较式 `max_drawdown < -30` / `> -10`
             （负号是语义的一部分，改成正数会让两档判断**双双失效**）
  B. `api/fund_detail.py:592` 内联 peak-trough → **正百分比**（20.0）
     消费方：`pages/_components.js:568` 渲染 `'-'+_dd+'%'` → 得到 `-20%`
  C. `services.fund_risk_adjusted` 契约字段（2026-09-13 补入）→ **正百分比**
     消费方：`services.fund_screen._drawdown_penalty`，**取 abs() 只看幅度**

所以现在的正确行为不是「统一成一个符号」，而是：**三者各自自洽，且永不合并**。
本文件把这个隐式不变量钉成显式断言。

最重要的那条是 `test_enrich_risk_adjusted_must_not_inject_max_drawdown`：
`_enrich_risk_adjusted` 是唯一会把 risk-adjusted 缓存字段合并进选基列表 dict 的
地方。一旦有人好心把 `max_drawdown` 也注入进去，A 的负值会被 C 的正值覆盖，
`signals.py` 维度7 的 `max_drawdown > -10` 将恒为真 → **每只基金都判定「低波动
稳健」+3 分**。这是静默错值，不是报错，故在此设卡。

故障注入对照：摘掉 `_drawdown_penalty` 里的 `abs()` → `test_drawdown_penalty_accepts_both_signs` 转红；
把 `max_drawdown` 加进 `_enrich_risk_adjusted` 的注入列表 → 合并那条转红。
"""
import inspect
import pathlib

import pytest

from api import signals
from services import fund_risk_adjusted as fra
from services import fund_screen as fs


# ───────────────── 1. A 侧：fund_screen 必须输出负百分比 ─────────────────

def test_build_drawdown_metrics_is_negative_percent():
    """fund_screen 的回撤是负百分比 —— signals.py 维度7 的阈值依赖这个符号。

    注意 build_drawdown_metrics 有 _MIN_DRAWDOWN_NAV_POINTS=60 的门槛
    （少于 60 个有效净值点判「数据不足」而留空），故用 70 点序列。
    峰值 1.2 跌到 0.9 = 25%。
    """
    out = fs.build_drawdown_metrics([1.0] * 20 + [1.2] * 20 + [0.9] * 30)
    assert out["max_drawdown"] == -25.0, (
        f"fund_screen 的回撤应为负百分比，实得 {out['max_drawdown']!r}。"
        "若这里变成正数，api/signals.py 维度7 的 `max_drawdown < -30` 与 "
        "`max_drawdown > -10` 两档会同时失效。"
    )
    assert out["max_drawdown"] < 0


def test_build_drawdown_metrics_zero_is_left_empty_not_negative_zero():
    """净值单调不回撤 → 留空并给 reason，不填 -0.0 这种占位值。"""
    out = fs.build_drawdown_metrics([1.0] * 30 + [1.1] * 30 + [1.2] * 10)
    assert out.get("max_drawdown") is None
    assert out.get("max_drawdown_reason")


# ───────────────── 2. C 侧：risk_adjusted 契约字段符号 ─────────────────

def test_risk_adjusted_contract_declares_max_drawdown_key():
    """契约里必须真的有 `max_drawdown` 键 —— 否则 _drawdown_penalty 的 L1 永不可达。

    构造不可计算类型（债券型）拿 skeleton：available=False 但键必须齐全。
    """
    metrics = fra.compute_risk_adjusted_metrics("000000", name="测试债基", fund_type="债券型")
    assert metrics is not None
    assert "max_drawdown" in metrics, (
        "compute_risk_adjusted_metrics 的返回体缺少 max_drawdown 键，"
        "fund_screen._drawdown_penalty 的 L1 分支将永远取不到值（闸门空转）。"
    )
    assert metrics["available"] is False


def test_risk_adjusted_magnitude_matches_fund_screen_opposite_sign():
    """同一段净值：C 的 compute_max_drawdown 给正**分数**，A 给负**百分比**。

    这条断言把「幅度一致、符号相反」这件事钉死，防止有人以为两者可以直接互换。
    """
    navs = [1.0, 1.2, 0.9, 1.1]
    fraction = fra.compute_max_drawdown(navs)
    assert fraction is not None
    assert pytest.approx(fraction, abs=1e-9) == 0.25, "risk_adjusted 口径应为正的分数(0~1)"
    assert pytest.approx(fs.compute_max_drawdown_pct(navs), abs=1e-9) == 25.0
    # 契约字段是 fraction*100 → 正百分比
    assert fraction * 100 > 0


# ───────────────── 3. 合并闸门：_enrich_risk_adjusted 不得注入 max_drawdown ─────────────────

def test_enrich_risk_adjusted_must_not_inject_max_drawdown(monkeypatch):
    """**本文件的头号断言。**

    选基列表里的 `max_drawdown` 必须始终来自 fund_screen（负百分比）。
    若 risk-adjusted 的正百分比被合并进来覆盖它，signals.py 维度7 会静默反转：
    `> -10` 恒真 → 每只基金白拿 +3 分「低波动稳健」。
    """
    metrics = {
        "available": True,
        "sharpe_ratio": 1.23,
        "sortino_ratio": 1.85,
        "max_drawdown": 35.2,      # C 侧：正百分比，绝不能流进选基列表
        "calmar_ratio": 0.4,
    }
    monkeypatch.setattr(fra, "get_risk_adjusted_cache", lambda code: metrics)
    monkeypatch.setattr(fra, "enqueue_risk_adjusted_warmup", lambda missed: None)
    monkeypatch.setattr(fra, "invalidate_risk_adjusted_cache", lambda code: None)

    fund = {"code": "110011", "name": "某混合基金", "max_drawdown": -20.0}  # A 侧：负百分比

    signals._enrich_risk_adjusted([fund])

    # 允许注入的字段确实注入了（证明这次调用真的跑到了注入分支）
    assert fund["sharpe_ratio"] == 1.23
    assert fund["sortino_ratio"] == 1.85

    # 回撤必须保持 A 侧原值，符号不许被翻
    assert fund["max_drawdown"] == -20.0, (
        f"_enrich_risk_adjusted 把 max_drawdown 覆盖成了 {fund['max_drawdown']!r}。"
        "这会让 api/signals.py 维度7 的 `max_drawdown > -10` 恒为真，"
        "所有基金被误判为「低波动稳健」并白拿 +3 分。"
    )


def test_enrich_risk_adjusted_does_not_create_max_drawdown_when_absent(monkeypatch):
    """原始 dict 没有 max_drawdown 时，也不许凭空造一个出来。"""
    metrics = {"available": True, "sharpe_ratio": 0.9, "max_drawdown": 12.0}
    monkeypatch.setattr(fra, "get_risk_adjusted_cache", lambda code: metrics)
    monkeypatch.setattr(fra, "enqueue_risk_adjusted_warmup", lambda missed: None)

    fund = {"code": "110022", "name": "某股票基金"}
    signals._enrich_risk_adjusted([fund])

    assert "max_drawdown" not in fund, (
        "选基列表里凭空出现了 max_drawdown，来源不明 → 后续按 < -30 判断必然错。"
    )


# ───────────────── 4. 消费方容错：_drawdown_penalty 必须符号无关 ─────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (35.2, -10.0), (-35.2, -10.0),   # >20% → 重罚
        (12.0, -6.0), (-12.0, -6.0),     # >10% → 中罚
        (6.0, -3.0), (-6.0, -3.0),       # >5%  → 轻罚
        (4.9, 0.0), (-4.9, 0.0),         # 不足 5% → 跳过
        (0.0, 0.0),
    ],
)
def test_drawdown_penalty_accepts_both_signs(value, expected):
    """惩罚只关心幅度。故障注入：摘掉 abs() 则负值那一半全红。"""
    assert fs._drawdown_penalty({"max_drawdown": value}) == expected


def test_drawdown_penalty_skips_when_unavailable():
    """取不到 risk / 字段缺失 / 值非法 → 跳过惩罚（0.0），绝不回退到「近3月跌幅」假代理。"""
    assert fs._drawdown_penalty(None) == 0.0
    assert fs._drawdown_penalty({}) == 0.0
    assert fs._drawdown_penalty({"max_drawdown": None}) == 0.0
    assert fs._drawdown_penalty({"max_drawdown": "abc"}) == 0.0
    assert fs._drawdown_penalty({"_nav": [1, 2]}) == 0.0


# ───────────────── 5. signals.py 维度7 的负阈值是契约的一部分 ─────────────────

def test_signals_dimension7_compares_against_negative_thresholds():
    """静态钉住 signals.py 维度7 的负阈值比较。

    这是纯文本断言，用于在「符号被统一成正数」的重构里当绊线：
    一旦有人把 `max_drawdown < -30` 改成 `< 30`，本测试转红并提示需同步
    fund_screen.build_drawdown_metrics 的符号（A 侧）而不是改比较式。
    """
    src = pathlib.Path(inspect.getfile(signals)).read_text(encoding="utf-8")
    assert "max_drawdown < -30" in src, (
        "api/signals.py 维度7 的负阈值比较消失或改了符号。"
        "它是 fund_screen 负百分比口径(A)的消费方 —— 改符号必须同时改 provider。"
    )
    assert "max_drawdown > -10" in src
