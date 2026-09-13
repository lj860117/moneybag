"""显著性校正（Newey-West 自相关 + BH-FDR 多重检验）回归测试 —— 2026-09-13

缺陷（根因在 services/factor_ic.py，不在消费方）：
    原实现 `significant = abs(t_stat) >= 2.0`，t_stat = ic_mean/(ic_std/√n)
    是**朴素、未校正**的 t 检验，两处系统性高估显著性：
      1. 没有多重检验校正：同时检验 17~70 个因子，|t|>=2（p≈0.05）纯靠
         运气就能刷出一批"显著"；
      2. IC 由**重叠前瞻窗口**算出 → IC 序列天生强自相关 → 有效样本量
         远小于 n → t 被系统性放大。
    线上真实缓存（2026-09-12 10:01，17 个因子）里 `significant` 全为 False，
    但那是裸 t 检验的结论，且载荷里连 `p_adjusted`/`n_tested` 键都没有 ——
    只要有一个通过，下游 stock_screen 就会拿噪声调权。

修法：`significant` 改成「自相关校正 + BH-FDR 校正后」的结论，
      `significant_naive` 同时保留，让"校正前显著、校正后不显著"可见。

本文件是故障注入测试（每加一个判据必配一个"摘掉就转红"的用例）：
  · 摘掉 BH（significant 直接用裸 p）→ test_bh_* / test_pure_noise_* 转红
  · 摘掉 NW（不写 t_stat_nw、不校正自相关）→ test_newey_west_* 转红
  · 载荷缺 significant_naive/significant_corrected 键 → test_payload_* 转红

用例全部用**确定性**数据（指数衰减序列 / 固定 seed 的 iid），不依赖随机，
也不依赖 Tushare —— 便于在服务器上复现同一组数字。
"""
import math
import random
from datetime import date, timedelta

import pytest

from services import factor_ic

Q = factor_ic._FDR_Q


# ── 合成面板（与 test_factor_ic_no_lookahead 同构，但自带一份，避免跨文件耦合）──

_BLOCK = 20
_N_BLOCKS = 15
_N_BARS = _BLOCK * _N_BLOCKS


def _make_dates(n: int) -> list:
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _make_closes(rng: random.Random, k: float = 0.8, jitter: float = 0.25) -> list:
    """区块收益 b_{i+1} = -k*b_i + noise：截面边界上 mom20 与前瞻收益必然负相关"""
    blocks, prev = [], rng.gauss(0.0, 1.0)
    for _ in range(_N_BLOCKS):
        blocks.append(prev)
        prev = -k * prev + rng.gauss(0.0, jitter)
    rets = []
    for b in blocks:
        rets.extend([b / _BLOCK] * _BLOCK)
    closes, p = [], 100.0
    for r in rets:
        p *= math.exp(r / 100.0)
        closes.append(p)
    return closes


@pytest.fixture
def synthetic_market(monkeypatch):
    rng = random.Random(20260912)
    n_stocks = 40
    codes = [f"{600000 + i}" for i in range(n_stocks)]
    dates = _make_dates(_N_BARS)
    hist = {c: _make_closes(rng) for c in codes}
    pool = [{"code": c, "name": f"合成{i}", "price": hist[c][-1], "market_cap": 100.0 + i}
            for i, c in enumerate(codes)]

    import services.stock_data_provider as sdp
    import services.backtest_engine as be
    import services.tushare_data as tsd

    monkeypatch.setattr(sdp, "get_stock_data", lambda: {"stocks": pool})
    monkeypatch.setattr(be, "_get_stock_hist", lambda code, period="daily", days=750: [
        {"date": d, "close": c} for d, c in zip(dates, hist[code][-days:])])
    monkeypatch.setattr(tsd, "is_configured", lambda: False)
    factor_ic._ic_cache.delete(f"ic_v2_20_{n_stocks}")
    return {"pool_size": n_stocks}


def _decay(n: int, a: float, tau: float) -> list:
    """确定性指数衰减序列：IC 序列最典型的自相关形态。

    真实 IC 序列常表现为"某段区间整体偏高/偏低、慢慢回到 0"——即一个大的
    初始冲击按 exp(-i/tau) 衰减。这种序列的裸 t 会被放大到"极显著"，
    因为它把有效样本量当成了 n。
    """
    return [a * math.exp(-i / tau) for i in range(n)]


# ── 1. BH-FDR 多重检验校正 ──────────────────────────────────────────

def test_bh_kills_a_family_of_borderline_naive_significant_pvalues():
    """20 个因子里 5 个裸 p<0.05 —— BH 校正后必须一个都不剩。

    故障注入：把 BH 摘掉（significant 直接取裸 p）→ corrected 会变成 5，
    本断言转红。
    """
    pvals = [0.031, 0.038, 0.042, 0.047, 0.049] + [0.60 + 0.01 * i for i in range(15)]
    assert sum(1 for p in pvals if p < Q) == 5, "前置：裸判据下有 5 个显著"

    adj = factor_ic._benjamini_hochberg(pvals)
    corrected = sum(1 for p in adj if p < Q)
    assert corrected == 0, (
        f"BH 未生效：裸判据 5 个显著，校正后仍剩 {corrected} 个。"
        "把 BH 摘掉本断言即转红")
    # 最小 p 的调整值 = min_j p_(j)*m/j = 0.049*20/5 = 0.196（可手工核对）
    assert adj[0] == pytest.approx(0.049 * 20 / 5)
    # 最大 p 的调整值就是它自己
    assert adj[-1] == pytest.approx(0.74)


def test_bh_adjusted_p_is_never_smaller_than_raw_p():
    """BH 只会把 p 调大或不动（调整后 p >= 原 p），方向不许反"""
    pvals = [0.001, 0.004, 0.02, 0.03, 0.041, 0.049, 0.2, 0.5, 0.9]
    adj = factor_ic._benjamini_hochberg(pvals)
    for a, p in zip(adj, pvals):
        assert a >= p - 1e-12, f"BH 把 {p} 调小成了 {a}"
        assert a <= 1.0


def test_bh_is_identity_for_single_factor_and_empty_family():
    """家族只有 1 个因子时不该有任何"校正"效果；空家族返回空列表"""
    assert factor_ic._benjamini_hochberg([0.013]) == pytest.approx([0.013])
    assert factor_ic._benjamini_hochberg([]) == []


def test_pure_noise_family_corrected_mostly_not_significant():
    """60 个纯噪声因子的家族：BH 校正后不得再报出任何一个显著。

    这是多重检验校正存在的唯一理由 —— 同时检验几十个因子，
    裸 |t|>=2 必然刷出一撮假显著（这里实测 3/60）。

    故障注入：摘掉 BH → corrected 会回到 3（或 2），本断言转红。
    """
    rng = random.Random(424242)
    naive_p, robust_p = [], []
    for _ in range(60):
        xs = [rng.gauss(0.0, 0.08) for _ in range(12)]
        st = factor_ic._factor_significance_stats(xs, forward_days=5)
        naive_p.append(st["p_value"])
        robust_p.append(st["p_value_robust"])

    n_naive = sum(1 for p in naive_p if p < Q)
    adj = factor_ic._benjamini_hochberg(robust_p)
    n_corrected = sum(1 for p in adj if p < Q)

    print(f"[纯噪声家族 m=60] 裸 t 显著 {n_naive}/60 → 自相关+BH 校正后 {n_corrected}/60")
    assert n_naive >= 1, "前置：纯噪声家族里本应靠运气刷出至少 1 个裸显著"
    assert n_corrected == 0, (
        f"BH 未生效：纯噪声家族校正后仍有 {n_corrected} 个显著（裸判据 {n_naive} 个）")


# ── 2. Newey-West 自相关校正 ────────────────────────────────────────

def test_newey_west_shrinks_t_for_autocorrelated_ic_series():
    """强自相关的 IC 序列：校正后 t 必须显著变小，且有效样本量远小于 n。

    序列用确定性指数衰减（无随机），数字可手工复核：
      n=24, forward_days=5 → nw_lag=4
      裸 t≈5.93 → t_stat_nw≈3.11 → t_stat_eff≈1.76（取更保守者）
      有效样本量 24 → 2.12

    故障注入：摘掉 NW（不写 t_stat_nw / 不校正自相关）→
    t_stat_nw 缺失或等于 t_stat，"t_nw < t" 与 "p_robust > p" 两条同时转红。
    """
    xs = _decay(24, a=1.0, tau=8.0)
    st = factor_ic._factor_significance_stats(xs, forward_days=5)

    assert st["nw_lag"] == 4, "滞后阶应为 forward_days-1"
    assert st["ic_autocorr_lag1"] > 0.8, st["ic_autocorr_lag1"]
    assert st["t_stat"] > 5.0, st["t_stat"]
    assert st["t_stat_nw"] < st["t_stat"], (
        f"NW 没起作用：t={st['t_stat']:.3f} t_nw={st['t_stat_nw']:.3f}")
    # 两种校正口径的更保守者必须不弱于 NW
    assert abs(st["t_stat_robust"]) <= abs(st["t_stat_nw"]) + 1e-9
    assert st["effective_n"] < 24 / 4, st["effective_n"]

    # 结论翻转：裸 t 极显著，自相关校正后不显著
    assert st["p_value"] < Q, "前置：裸 p 应显著"
    assert st["p_value_robust"] > Q, (
        f"自相关校正后仍显著（p_robust={st['p_value_robust']:.6f}）—— "
        "说明有效样本量没有下降，NW/effective_n 没生效")
    print(f"[强自相关 IC 序列] t={st['t_stat']:.3f} → t_nw={st['t_stat_nw']:.3f} "
          f"→ t_robust={st['t_stat_robust']:.3f}; eff_n={st['effective_n']:.2f}/24; "
          f"p={st['p_value']:.6f} → p_robust={st['p_value_robust']:.6f}")


def test_newey_west_barely_changes_t_for_iid_series():
    """反向对照：iid 序列不应被 NW 大幅改动（防止"校正"变成乱砍）"""
    rng = random.Random(20260913)
    xs = [0.15 + rng.gauss(0.0, 0.10) for _ in range(24)]
    st = factor_ic._factor_significance_stats(xs, forward_days=5)
    ratio = st["t_stat_nw"] / st["t_stat"]
    print(f"[iid 对照] t={st['t_stat']:.3f} t_nw={st['t_stat_nw']:.3f} "
          f"ratio={ratio:.4f} rho1={st['ic_autocorr_lag1']:.4f}")
    assert 0.7 < ratio < 1.3, f"iid 序列被 NW 改动了 {ratio:.3f} 倍"
    assert abs(st["ic_autocorr_lag1"]) < 0.25


def test_effective_n_formula_matches_rho1():
    """effective_n = n(1-ρ1)/(1+ρ1)，且被夹在 [2, n]"""
    xs = _decay(30, a=1.0, tau=10.0)
    _t, eff, rho = factor_ic._effective_n_t_stat(xs)
    assert rho > 0.5
    expected = 30 * (1 - rho) / (1 + rho)
    assert eff == pytest.approx(max(2.0, min(30.0, expected)))
    assert 2.0 <= eff <= 30.0


def test_short_series_degrade_to_no_evidence_without_fabricating():
    """短于 3 个点的序列：自相关校正无从估计 → 一律退化成"无证据"
    （t_nw=0、p_robust=1），既不抛异常，也不编一个"看起来算过"的数字。

    注意：这里刻意不测 len==0 —— 调用方（compute_factor_ic）在
    len(series) < _MIN_IC_PERIODS 时根本不进入本函数，n=0 不是可达输入。
    """
    for bad in ([0.1], [0.1, 0.2], [-0.05, 0.02]):
        st = factor_ic._factor_significance_stats(bad, forward_days=5)
        assert st["t_stat_nw"] == 0.0
        assert st["t_stat_robust"] == 0.0
        assert st["p_value_robust"] == 1.0
        assert st["ic_autocorr_lag1"] == 0.0
        assert st["nw_lag"] == min(4, len(bad) - 1)


# ── 3. 真实载荷：两个键必须同时在，且校正链路可核对 ──────────────────

def test_payload_exposes_both_naive_and_corrected_flags(synthetic_market):
    """真实 compute_factor_ic 载荷必须同时给出
    significant_naive（裸 t）与 significant_corrected（校正后），
    以及 p_value / p_value_robust / p_adjusted / n_tested 完整链路。

    故障注入：删掉 significant_naive / significant_corrected /
    p_adjusted / n_tested 任一键 → 本用例转红（stock_screen 的准入闸门
    也会因为拿不到校正字段而拒绝该载荷）。
    """
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True)
    assert "error" not in result, result.get("error")

    n_tested = result["summary"]["n_tested"]
    assert n_tested >= 1

    for fname, info in result["factors"].items():
        for key in ("significant", "significant_naive", "significant_corrected",
                    "p_value", "p_value_robust", "p_adjusted", "n_tested",
                    "t_stat", "t_stat_nw", "t_stat_eff", "t_stat_robust",
                    "effective_n", "ic_autocorr_lag1", "nw_lag", "bonferroni_alpha"):
            assert key in info, f"{fname} 缺少显著性字段 {key}"

    # 只对「真正参与检验」的因子做数值链路核对
    tested = {f: v for f, v in result["factors"].items() if v.get("n_periods")}
    assert tested, "合成面板下应有动量因子参与检验"
    for fname, info in tested.items():
        assert info["significant"] is info["significant_corrected"], \
            f"{fname} significant 的语义必须等于校正后结论"
        assert info["n_tested"] == n_tested, f"{fname} 的家族大小应与 summary 一致"
        assert info["p_adjusted"] >= info["p_value_robust"] - 1e-12, \
            f"{fname} BH 调整后的 p 不应小于稳健 p"
        assert info["nw_lag"] == max(0, min(20 - 1, info["n_periods"] - 1))

    s = result["summary"]
    assert s["significant_corrected_count"] <= s["significant_naive_count"], \
        "校正后显著数不可能超过校正前（校正只会更保守）"
    print(f"[合成面板] n_tested={n_tested}, "
          f"naive={s['significant_naive_count']} → "
          f"corrected={s['significant_corrected_count']}, "
          f"bonferroni_alpha={s['bonferroni_alpha']}")


def test_payload_declares_correction_method_and_in_sample_limitation(synthetic_market):
    """校正口径与「IC 是样本内度量」的局限必须写进载荷，供上游/前端如实展示"""
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True)
    corr = result["significance_correction"]
    assert corr["multiple_testing"] == "benjamini_hochberg_fdr"
    assert corr["autocorrelation"] == "newey_west_bartlett"
    assert corr["fdr_q"] == Q
    assert corr["n_tested"] == result["summary"]["n_tested"]
    assert corr["bonferroni_alpha"] == result["summary"]["bonferroni_alpha"]

    joined = " ".join(result["limitations"])
    assert "样本内" in joined, "必须声明 IC 是样本内度量"
    assert "不等于样本外有效" in joined
    assert any("significant_naive" in w for w in result["warnings"]), \
        "warnings 必须解释 significant 与 significant_naive 的区别"


def test_untested_factors_have_unified_schema_and_no_fabricated_numbers(synthetic_market):
    """无历史面板的因子：schema 必须与已检验因子一致（缺键会让下游 KeyError），
    但数值一律留空 —— 尤其不许出现"看起来算过"的 p_adjusted=0.0"""
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True)
    untested = {f: v for f, v in result["factors"].items() if not v.get("n_periods")}
    assert untested, "Tushare 关闭时应有估值/财务因子无历史面板"
    for fname, info in untested.items():
        assert info["significant"] is False
        assert info["significant_naive"] is False
        assert info["significant_corrected"] is False
        assert info["p_adjusted"] is None
        assert info["p_value"] is None
        assert info["n_tested"] == 0
        assert info["effective"] is False
