"""组合整体相对成本 = 口径 A（成本加权）的独立回归。

背景（2026-09 专项核对）：PM 调研脚本算出的「组合整体相对成本」与线上
`dca.py` 的 `combo_cost_pct` 差 1.78pct，一度被怀疑是「组合层面算法在两个
实现之间不一致」。核对结论是**两个数都对，只是口径不同，且数学上必然不等**：

    口径 A（生产，成本加权）= Σmv / Σcost - 1 = Σ(ωc_i × r_i)     ← 本文件锁的
    口径 B（基金评价，市值加权）= Σ(ωm_i × r_i)
    恒等式：B - A = Var_c(r) / (1 + A)  ≥ 0

完整推导与业务结论见 `services/fund_signal/dca.py` 模块 docstring「为什么第一条
不能修正成市值加权」。本文件把那条恒等式钉成可执行断言，防止未来有人看到
「两个数字对不上」就把 `dca.py` 的 A 口径「修正」成 B 口径。

为什么独立成文件而不是并进 `test_fund_signal_e2e.py`：e2e 文件覆盖的是
signal_pool 条件式语义 / 预算守门 / 文案渲染，主题是「信号该不该推」；
本文件覆盖的是「一个数字该怎么算」，是跨模块（dca + portfolio）的口径不变量，
日后按「口径 / caliber」检索时能一次命中。

全部离线：不发起任何网络请求，状态用内存态，weight_mv 用生产同款最大余数法。
"""
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.fund_signal import dca
from services.fund_signal import state as real_state
from services.fund_signal.portfolio import (
    FundPosition,
    _largest_remainder_weights,
)


# ============================================================
# 合成持仓
# ============================================================
# 3 只收益率有明显方差的持仓：+16.67% / -26.72% / +4.19%
# （dd_cost_pct 经 dca._pct 取 2 位小数后即为这三个数）
_SPREAD_SPECS = [
    ("013107", "华夏先进制造",       500.0,  1.20, 1.4000),
    ("005698", "华夏全球科技先锋QDII", 800.0, 2.50, 1.8320),
    ("008984", "财通科技创新混合C",   1000.0, 1.91, 1.9900),
]

# 退化工况：3 只收益率完全相同（均 -10%）→ Var_c(r) = 0 → 两个口径必须相等
_FLAT_SPECS = [
    ("013107", "华夏先进制造",       500.0,  1.0, 0.9),
    ("005698", "华夏全球科技先锋QDII", 800.0, 1.0, 0.9),
    ("008984", "财通科技创新混合C",   1000.0, 1.0, 0.9),
]


def _pos(code: str, name: str, shares: float, cost_nav: float, unit_nav: float) -> FundPosition:
    """构造一个 FundPosition；market_value 按份额 × 单位净值自算。"""
    return FundPosition(
        code=code,
        name=name,
        shares=shares,
        cost_nav=cost_nav,
        unit_nav=unit_nav,
        adj_nav=unit_nav,
        is_qdii="QDII" in name.upper(),
        market_value=shares * unit_nav,
        weight_mv=0.0,
        nav_date="20260924",
        nav_history=[],
    )


def _build(specs: list) -> list:
    """specs = [(code, name, shares, cost_nav, unit_nav)] → weight_mv 已补齐的持仓。

    weight_mv 复用生产同款 `_largest_remainder_weights`（最大余数法，Σ == 100.00），
    这样 B 口径复现的是线上真实的取整行为，而不是手填的整权重。
    """
    positions = [_pos(*s) for s in specs]
    weights = _largest_remainder_weights([p.market_value for p in positions])
    for p, w in zip(positions, weights):
        p.weight_mv = w
    assert abs(sum(p.weight_mv for p in positions) - 100.0) < 1e-9
    return positions


# ============================================================
# 两个口径与恒等式右侧的计算（测试内独立实现，不复用被测代码，防自证）
# ============================================================

def _total_cost(positions: list) -> float:
    """Σ (shares × cost_nav)。"""
    return sum(p.shares * p.cost_nav for p in positions)


def _caliber_a(positions: list) -> float:
    """口径 A（生产口径）：Σmv / Σcost - 1，返回**百分数、全精度未取整**。"""
    total_mv = sum(p.market_value for p in positions)
    return (total_mv / _total_cost(positions) - 1.0) * 100.0


def _caliber_b(positions: list, snap: dict) -> float:
    """口径 B（基金评价口径）：Σ(weight_mv / 100 × dd_cost_pct)。

    刻意走快照里已经算好的 `dd_cost_pct` 与 `weight_mv`（含各自的 2 位取整），
    复现「PM 调研脚本那种」算法，而不是用全精度 r_i。
    """
    rows = {r["code"]: r for r in snap["rows"]}
    return sum(rows[p.code]["weight_mv"] / 100.0 * rows[p.code]["dd_cost_pct"]
               for p in positions)


def _cost_weighted_variance(positions: list) -> float:
    """Var_c(r)：收益率按【成本】权重的加权方差（分数制，非百分数）。

    Var_c(r) = E_c(r²) - (E_c(r))²，其中 E_c 按 ωc_i = cost_i / Σcost 加权。
    恒等式右侧用的就是这个量；注意权重是**成本**而非市值，取错则恒等式不成立。
    """
    costs = [p.shares * p.cost_nav for p in positions]
    total_cost = sum(costs)
    rates = [p.unit_nav / p.cost_nav - 1.0 for p in positions]
    omegas = [c / total_cost for c in costs]
    exp_r = sum(w * r for w, r in zip(omegas, rates))
    exp_r2 = sum(w * r * r for w, r in zip(omegas, rates))
    return exp_r2 - exp_r * exp_r


def _expected_gap_pct(positions: list) -> float:
    """恒等式预测的 B - A（百分数）：Var_c(r) / (1 + A) × 100。"""
    a_frac = _caliber_a(positions) / 100.0
    return _cost_weighted_variance(positions) / (1.0 + a_frac) * 100.0


# ============================================================
# 用例 1：口径 A 精确成立（_pct 层面逐位相等，不是近似）
# ============================================================

def test_combo_cost_pct_equals_cost_weighted_caliber_a():
    """combo_cost_pct == _pct(Σmarket_value, Σ(shares × cost_nav))，精确相等。"""
    positions = _build(_SPREAD_SPECS)
    snap = dca._build_snapshot(positions, None)

    total_mv = sum(p.market_value for p in positions)
    total_cost = _total_cost(positions)

    assert snap["combo_cost_pct"] == dca._pct(total_mv, total_cost), snap["combo_cost_pct"]
    # 锚定值：合成数据固定，故可写死（LeiJiang 真实组合的 -3.99% 会随重录变化，不可写死）
    assert snap["combo_cost_pct"] == -7.86, snap["combo_cost_pct"]

    # 恒等式的另一半：A 就是 r_i 的【成本】加权平均，精确成立（残差 ~1e-14）
    exp_r = sum((p.shares * p.cost_nav) / total_cost * (p.unit_nav / p.cost_nav - 1.0)
                for p in positions)
    assert abs(exp_r * 100.0 - _caliber_a(positions)) < 1e-9


# ============================================================
# 用例 2：两个口径不等，且差值 == Var_c(r)/(1+A)
# ============================================================

def test_market_value_weighted_differs_by_cost_weighted_variance():
    """B - A ≈ Var_c(r)/(1+A) —— 「两个算法差 X pct」的根因，不是 bug。"""
    positions = _build(_SPREAD_SPECS)
    snap = dca._build_snapshot(positions, None)

    a_pct = snap["combo_cost_pct"]
    b_pct = _caliber_b(positions, snap)

    # 先防死测试：方差必须真的非 0，否则下面所有断言都会平凡通过
    var_c = _cost_weighted_variance(positions)
    assert var_c > 1e-6, f"合成持仓的 Var_c(r) 必须 > 0，当前 {var_c}"

    # (a) 两个口径必须不等，且差得远（不是浮点噪声）
    assert a_pct != b_pct, (a_pct, b_pct)
    assert abs(b_pct - a_pct) > 1.0, f"差距仅 {abs(b_pct - a_pct)} pct，用例退化"

    # (b) 差值 == 恒等式预测值。容差 0.02 pct 留给两处取整：
    #     weight_mv 被 _largest_remainder_weights 取整到 2 位（≤0.005×|r| ≈ 0.0014 pct）
    #     + dd_cost_pct 被 _pct 取整到 2 位（加权后 ≤0.005 pct）
    #     + combo_cost_pct 自身 2 位取整（≤0.005 pct）。本数据实测残差 0.0020 pct。
    expected = _expected_gap_pct(positions)
    assert abs((b_pct - a_pct) - expected) < 0.02, {
        "A": a_pct, "B": b_pct, "B-A": b_pct - a_pct,
        "Var_c(r)/(1+A)": expected, "Var_c(r)": var_c,
    }


# ============================================================
# 用例 3：方向性 —— B ≥ A（市值加权系统性高估收益）
# ============================================================

def test_market_value_weighted_never_below_cost_weighted():
    """B ≥ A 恒成立（1+A>0 且 Var_c(r)≥0）；有方差时严格 >。"""
    positions = _build(_SPREAD_SPECS)
    snap = dca._build_snapshot(positions, None)

    a_pct = snap["combo_cost_pct"]
    b_pct = _caliber_b(positions, snap)

    # 用户真实盈亏（A）必须**不高**于「基金评价」口径（B）
    assert b_pct >= a_pct, f"B({b_pct}) 低于 A({a_pct})，方向反了"
    assert b_pct > a_pct, "有方差时应严格大于（Var_c(r) > 0）"


# ============================================================
# 用例 4：边界 —— 所有持仓收益率相同 → Var_c(r)=0 → 两口径相等
# ============================================================

def test_identical_returns_make_both_calibers_equal():
    """退化工况：Var_c(r) = 0 ⇒ B - A = 0，两个口径必须相等。

    ⚠️ 这条不是为了防「差多少」，而是防**把公式记反 / 记歪**：
    任何正确的组合口径，在「所有持仓收益率完全一致」这个零离散度角点上都必须
    退化成同一个数。凡是退化不到 0 的实现（符号反了、权重取错、多加了常数项），
    都会在这里挂掉，而在用例 1~3 里却可能蒙混过关。
    """
    positions = _build(_FLAT_SPECS)
    snap = dca._build_snapshot(positions, None)

    var_c = _cost_weighted_variance(positions)
    assert abs(var_c) < 1e-12, f"退化用例的 Var_c(r) 必须为 0，当前 {var_c}"

    a_pct = snap["combo_cost_pct"]
    b_pct = _caliber_b(positions, snap)

    assert abs(b_pct - a_pct) < 1e-6, f"Var=0 时两口径必须相等，A={a_pct} B={b_pct}"
    assert a_pct == -10.0, a_pct
    assert b_pct == -10.0, b_pct
    assert _expected_gap_pct(positions) == 0.0


# ============================================================
# 用例 5：公开路径 —— 推给用户的文案里带的是 A 口径，不是 B 口径
# ============================================================

class _FakeState:
    """内存版 state.load / state.save，隔离落盘副作用。"""
    def __init__(self):
        self.store = {}

    def load(self, user_id, name):
        return self.store.get((user_id, name), {})

    def save(self, user_id, name, data):
        self.store[(user_id, name)] = dict(data)


def test_dca_push_text_carries_caliber_a_not_caliber_b(monkeypatch):
    """走完整 collect() → render_dca()，断言最终文案里是 A(-7.9%) 不是 B(-4.6%)。

    前 4 条用例验的是 `_build_snapshot` 内部的算式；这条补上「算式对了但没接到
    出口」的缺口 —— 如果哪天有人在 render 之前把 combo_cost_pct 换成市值加权，
    前 4 条全绿，只有这条会红。
    """
    store = _FakeState()
    monkeypatch.setattr(real_state, "load", store.load)
    monkeypatch.setattr(real_state, "save", store.save)
    # 触发日判定与本用例主题无关，钉成恒等函数，避免依赖交易日历/网络。
    monkeypatch.setattr(dca, "resolve_trigger_day", lambda today: today)
    # 预置上月已推记录：既跨过幂等，也跨过冷启动宽限（宽限只在 state 为空时生效）。
    store.save("u1", "dca_state", {"last_push_month": "2026-09"})

    positions = _build(_SPREAD_SPECS)
    out = dca.collect("u1", positions, None, today=date(2026, 10, 23))

    assert len(out) == 1, out
    content = out[0]["content"]

    # A 口径 -7.86% → 文案里 "%.1f" 呈现为 -7.9%
    assert "-7.9%" in content, content
    # B 口径 -4.61% → 呈现为 -4.6%，绝不能出现在推给用户的文案里
    assert "-4.6%" not in content, f"文案里出现了市值加权的 B 口径：{content}"
