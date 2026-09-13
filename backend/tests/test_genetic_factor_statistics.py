"""P1-9 回归测试：遗传因子挖掘的三道统计防护

背景（P1-9 缺陷）：
    在单票约 800 天时序上做约 6000 次表达式搜索，取同段历史 |IC| 最高者当
    "最好因子"。三个问题：
      1. `zscore` 用**全样本**均值/方差 → 第 i 个点的取值依赖 i 之后的数据
         （前视偏差），会让"运气好的表达式"刷出虚高 IC；
      2. 没有样本外验证 —— 同一段数据既用来挑因子又用来报成绩；
      3. 没有多重检验校正 —— 6000 次搜索下纯噪声能刷出多高的 |IC| 无人知晓，
         裸 |IC| 排名把"搜索次数的运气"当成了 alpha。

本文件按项目规矩做**故障注入测试**：每加一个判据，就写一个「摘掉判据即转红」
的用例。第 1 节的滚动 zscore 用例同时断言"全样本版会挂"，用来证明这条判据
测的确实是前视偏差，而不是碰巧通过。
"""
import inspect

import numpy as np
import pytest

from services import genetic_factor as gf


# ── 1. zscore 必须是因果的（滚动窗口），全样本版必须会挂 ──────────────

def test_rolling_zscore_is_prefix_invariant():
    """滚动 zscore 的前 m 个取值必须只依赖前 m 个数据（因果）。"""
    rng = np.random.default_rng(11)
    body = rng.normal(0, 1, 300)
    # 未来段塞入极端值：均值/方差会被严重带偏
    x = np.concatenate([body, np.array([500.0, -800.0, 1200.0] * 10)])
    w, m = 60, 200

    got = gf._rolling_zscore(x, w)[:m]
    ref = gf._rolling_zscore(x[:m], w)          # 只用前 m 个点重算
    assert np.allclose(got, ref), "滚动 zscore 用到了未来数据（前视偏差）"


def test_full_sample_zscore_is_contaminated_by_future():
    """反证：全样本版会被未来段污染 —— 证明上一条测的确实是前视偏差。

    同时钉死"生产路径已不用全样本版"这件事：若把 Node.evaluate 里的 zscore
    改回 _full_sample_zscore，上一个用例会红（前缀不再不变）。
    """
    rng = np.random.default_rng(11)
    body = rng.normal(0, 1, 300)
    x = np.concatenate([body, np.array([500.0, -800.0, 1200.0] * 10)])
    m = 200
    contam = gf._full_sample_zscore(x)[:m]
    clean = gf._full_sample_zscore(x[:m])
    assert not np.allclose(contam, clean), \
        "全样本版竟然不受未来段影响？那本用例没有测到前视偏差"


def test_zscore_node_uses_rolling_window_and_writes_param_in_expression():
    """表达式节点：zscore 必须带窗口参数，且求值走滚动实现"""
    tree = gf.Node("zscore", children=[gf.Node("field", field="close")], param=20)
    assert tree.to_string() == "zscore(close, 20)"

    rng = np.random.default_rng(5)
    x = np.concatenate([rng.normal(10, 1, 300), np.full(30, 1e4)])
    n = len(x)
    vals = tree.evaluate({"close": x}, n)
    ref = gf._rolling_zscore(x[:300], 20)
    assert np.allclose(vals[:300], ref)


def test_random_tree_gives_zscore_a_window():
    """随机生成的 zscore 节点必须自带滚动窗口（否则等于前视偏差）"""
    import random
    random.seed(3)
    found = 0
    for _ in range(400):
        t = gf._random_tree()
        stack = [t]
        while stack:
            node = stack.pop()
            if node.op == "zscore":
                found += 1
                assert node.param in gf._ZS_WINDOWS, f"zscore 窗口非法: {node.param}"
            stack.extend(node.children)
    assert found > 0, "400 棵随机树里没出现 zscore，用例没测到东西"


# ── 2. IC 计算：口径正确 + 不依赖 scipy（防静默返回 0）───────────────

def test_spearman_corr_direction_and_ties():
    a = np.arange(60, dtype=float)
    assert gf._spearman_corr(a, a) == pytest.approx(1.0)
    assert gf._spearman_corr(a, a[::-1]) == pytest.approx(-1.0)
    t = np.array([1, 1, 2, 2, 2, 3, 4, 4, 5, 6] * 6, dtype=float)
    assert gf._spearman_corr(t, t) == pytest.approx(1.0)


def test_calc_ic_is_not_silently_zero():
    """`_calc_ic` 若因缺 scipy 抛出被吞，会静默返回 0（看起来在跑、其实全假）"""
    rng = np.random.default_rng(2)
    x = rng.normal(size=200)
    assert gf._calc_ic(x, x) == pytest.approx(1.0), \
        "_calc_ic 返回 0：IC 计算被静默降级了"
    assert gf._calc_ic(x, rng.normal(size=200)) == pytest.approx(0.0, abs=0.15)


# ── 3. 样本内/样本外切分：选择阶段绝不许碰样本外 ─────────────────────

def test_fitness_on_in_sample_span_ignores_out_of_sample_labels():
    """故障注入：只改样本外标签，样本内适应度必须逐位不变。

    若有人把选择阶段的区间参数写错（例如 lo/hi 用默认全区间），本用例转红。
    """
    rng = np.random.default_rng(21)
    n = 800
    x = rng.normal(size=n)
    fwd_a = rng.normal(0, 0.02, n)
    fwd_b = fwd_a.copy()
    fwd_b[560:795] = rng.normal(0, 0.02, 235)     # 只改样本外
    tree = gf.Node("zscore", children=[gf.Node("field", field="close")], param=20)

    fa = gf._fitness_from_values(tree, x, fwd_a, 0, 555)
    fb = gf._fitness_from_values(tree, x, fwd_b, 0, 555)
    assert fa == fb, "样本外标签影响了样本内适应度 —— 选择阶段看到了样本外"

    # 反证：全区间口径下两者必然不同，说明本用例确实能抓到泄漏
    assert gf._fitness_from_values(tree, x, fwd_a) != \
        gf._fitness_from_values(tree, x, fwd_b)


def test_evolution_fitness_is_called_with_in_sample_span(monkeypatch):
    """接线测试：进化主循环传给适应度的区间必须正好是样本内区间。

    摘掉这条（例如把 lo/hi 写回默认全区间），选择阶段就会看到样本外数据 ——
    本用例是确定性的，不依赖随机结果。

    ⚠️ 期望值必须**由外部独立推算**（n * split_ratio - forward_horizon），
    不能拿 res["oos_validation"]["in_sample_range"] 跟自己对账 —— 那样一旦
    实现把 lo/hi 改成全样本，两边会一起变，用例照样绿（自证式假绿）。
    """
    seen = []
    orig = gf._fitness_from_values

    def _spy(tree, values, fwd_ret, lo=0, hi=None):
        seen.append((lo, hi))
        return orig(tree, values, fwd_ret, lo, hi)

    monkeypatch.setattr(gf, "_fitness_from_values", _spy)
    res, _ = _run_synthetic(population_size=12, generations=3, top_k=1)
    assert seen, "进化过程没有调用适应度函数"

    n = res["config"]["data_points"]
    expected_lo = 0
    expected_hi = max(1, int(n * gf._OOS_SPLIT_RATIO) - gf._FORWARD_HORIZON)
    assert expected_hi < n, "前置：purge 后样本内必须严格短于全样本"
    spans = {(lo, hi) for lo, hi in seen}
    assert spans == {(expected_lo, expected_hi)}, (
        f"适应度用了 {spans}，按 split={gf._OOS_SPLIT_RATIO}/purge={gf._FORWARD_HORIZON} "
        f"独立算出的样本内区间是 ({expected_lo}, {expected_hi})（n={n}）")
    # 与载荷里的声明交叉核对（这一步是自洽性检查，不是唯一依据）
    assert res["oos_validation"]["in_sample_range"] == [expected_lo, expected_hi]
    assert res["oos_validation"]["out_of_sample_range"][0] >= expected_hi


def test_evolve_factors_outputs_oos_split_and_metadata():
    """端到端（小参数）：必须带样本外区间、purge 与搜索次数"""
    res, _ = _run_synthetic(population_size=40, generations=8, top_k=3)
    assert "error" not in res, res.get("error")
    oos = res["oos_validation"]
    assert oos["selection_uses"] == "in_sample_only"
    assert oos["split_ratio"] == pytest.approx(0.7)
    assert oos["purged_points"] == gf._FORWARD_HORIZON
    assert oos["out_of_sample_range"][0] > oos["in_sample_range"][1], \
        "样本内区间与样本外区间重叠了"
    cfg = res["config"]
    assert cfg["search_trials"] == 40 * 8
    assert cfg["in_sample_points"] + cfg["out_of_sample_points"] <= cfg["data_points"]


def test_top_factors_carry_in_sample_and_out_of_sample_ic():
    res, _ = _run_synthetic(population_size=40, generations=8, top_k=3)
    assert res["top_factors"], "没有产出任何因子"
    for f in res["top_factors"]:
        assert "ic_is" in f and "ic_oos" in f
        assert f["ic"] == f["ic_is"]                  # 兼容旧字段：ic 仍是样本内
        assert isinstance(f["oos_same_direction"], bool)
        # 方向反转绝不许算作样本外复现
        if not f["oos_same_direction"]:
            assert f["oos_confirmed"] is False


def test_direction_flip_is_not_confirmed():
    """核心诚实判据：样本内 + 样本外方向相反 → oos_confirmed 必须为 False"""
    res, _ = _run_synthetic(population_size=40, generations=8, top_k=5)
    flips = [f for f in res["top_factors"] if not f["oos_same_direction"]]
    if flips:   # 合成数据上通常会命中方向反转
        for f in flips:
            assert f["oos_confirmed"] is False
            assert "方向反转" in f["rating"]
    else:
        assert all(f["oos_confirmed"] for f in res["top_factors"])


# ── 4. 置换检验：随机基准必须真实算出来，且校正真的生效 ──────────────

def _noise_case(m=3000, k=235, n_perm=200, seed=99):
    """构造「纯噪声标签 + 纯噪声候选」，并挑出 |IC| 最高的那个当"选中因子"。"""
    rng = np.random.default_rng(seed)
    labels = np.full(800, np.nan)
    labels[:795] = rng.normal(0, 0.02, 795)
    cands = [rng.normal(size=800) for _ in range(m)]
    oos_lo, oos_hi = 560, 795
    # 用与实现同口径的方式挑"被选中"的因子（|IC| 最大者）
    lab = labels[oos_lo:oos_hi]
    ics = [abs(gf._spearman_corr(c[oos_lo:oos_hi], lab)) for c in cands]
    return labels, cands, int(np.argmax(ics)), oos_lo, oos_hi, n_perm


def test_permutation_baseline_is_real_and_max_is_far_above_single_trial():
    """6000 次搜索的随机基准必须显著高于单因子随机基准（否则校正没意义）"""
    labels, cands, best_i, lo, hi, n_perm = _noise_case()
    r = gf._permutation_significance(cands, labels, lo, hi,
                                     selected_vector=cands[best_i], n_perm=n_perm)
    assert r["available"] is True, r.get("reason")
    single, mx = r["null_single_trial"], r["null_max_distribution"]
    assert mx["mean"] > 2 * single["mean"], \
        f"搜索 max 基准 {mx['mean']} 没比单因子基准 {single['mean']} 高，基准可疑"
    assert single["p95"] < mx["p95"] <= mx["p99"] <= mx["max"]
    assert r["observed_statistic"] == "selected_factor_oos_abs_ic"
    # 口径一致性：置换检验里的观测值必须与 _calc_ic 报出的样本外 |IC| 同源，
    # 否则 p 值是在拿另一把尺子量，校正就失去意义。
    assert r["observed_abs_ic"] == pytest.approx(
        abs(gf._spearman_corr(cands[best_i][lo:hi], labels[lo:hi])), abs=1e-3)


def test_cherry_picked_noise_is_naive_significant_but_corrected_not():
    """本项目最核心的一条：裸 |IC| 看起来显著、多重校正后不显著。

    从 3000 个纯噪声因子里挑 |IC| 最高者：它的 |IC| 落在单因子分布的远端
    （naive p 很小），但正好是"搜索 max"的典型值（校正 p 很大）。
    摘掉多重校正（用 pooled 而不是 null_max）本用例即转红。
    """
    labels, cands, best_i, lo, hi, n_perm = _noise_case()
    r = gf._permutation_significance(cands, labels, lo, hi,
                                     selected_vector=cands[best_i], n_perm=n_perm)
    assert r["significant"] is False, "纯噪声被判定为显著 —— 校正没生效"
    assert r["p_value_corrected"] > 0.05
    assert r["p_value_naive"] < 0.05, "裸 p 应该很小，否则不构成对照"
    assert r["p_value_corrected"] > 10 * r["p_value_naive"]
    assert "未通过" in r["verdict"]


def test_true_signal_passes_corrected_significance():
    """反向对照：候选里真有一个与标签强相关的因子时，校正后必须显著"""
    rng = np.random.default_rng(31)
    labels = np.full(800, np.nan)
    labels[:795] = rng.normal(0, 0.02, 795)
    lo, hi = 560, 795
    signal = np.zeros(800)
    signal[lo:hi] = labels[lo:hi] * 400 + rng.normal(0, 0.05, hi - lo)  # 强相关
    cands = [signal] + [rng.normal(size=800) for _ in range(500)]
    r = gf._permutation_significance(cands, labels, lo, hi,
                                     selected_vector=signal, n_perm=200)
    assert r["available"] is True
    assert r["observed_abs_ic"] > 0.5
    assert r["p_value_corrected"] < 0.05
    assert r["significant"] is True


def test_permutation_is_deterministic_with_fixed_seed():
    labels, cands, best_i, lo, hi, n_perm = _noise_case(m=200, n_perm=50)
    a = gf._permutation_significance(cands, labels, lo, hi,
                                     selected_vector=cands[best_i], n_perm=n_perm)
    b = gf._permutation_significance(cands, labels, lo, hi,
                                     selected_vector=cands[best_i], n_perm=n_perm)
    assert a == b, "固定种子下随机基准必须可复算"


def test_permutation_degrades_without_fabricating_numbers():
    """候选太少 → available=False，且绝不能编造基准数字"""
    rng = np.random.default_rng(4)
    labels = np.full(800, np.nan)
    labels[:795] = rng.normal(0, 0.02, 795)
    cands = [rng.normal(size=800) for _ in range(5)]
    r = gf._permutation_significance(cands, labels, 560, 795, selected_vector=cands[0])
    assert r["available"] is False
    assert "reason" in r and r["reason"]
    for forbidden in ("null_max_distribution", "null_single_trial",
                      "p_value_corrected", "observed_abs_ic"):
        assert forbidden not in r, f"降级时仍给出了 {forbidden} —— 造数了"


def test_short_history_degrades_to_error_not_fake_factors():
    """历史不足时返回 error，不许硬凑一段样本外区间"""
    rng = np.random.default_rng(8)
    n = 150
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    data = {"close": close, "open": close, "high": close, "low": close,
            "volume": np.abs(rng.normal(1e6, 1e5, n)),
            "returns": np.concatenate(([0.0], np.diff(np.log(close))))}
    fwd = np.full(n, np.nan)
    for i in range(n - gf._FORWARD_HORIZON):
        fwd[i] = close[i + gf._FORWARD_HORIZON] / close[i] - 1.0

    orig = gf._prepare_data
    gf._gf_cache.clear()
    gf._prepare_data = lambda code, days=800: (data, fwd)
    try:
        res = gf.evolve_factors(code="SHORT", population_size=10, generations=2)
    finally:
        gf._prepare_data = orig
        gf._gf_cache.clear()
    assert "error" in res and "不足" in res["error"]


def test_no_data_returns_error():
    orig = gf._prepare_data
    gf._gf_cache.clear()
    gf._prepare_data = lambda code, days=800: (None, None)
    try:
        res = gf.evolve_factors(code="NODATA", population_size=4, generations=1)
    finally:
        gf._prepare_data = orig
        gf._gf_cache.clear()
    assert res == {"error": "数据获取失败"}


# ── 5. 置换检验被真正接进主流程（不是死代码）────────────────────────

def test_evolve_factors_exposes_permutation_baseline():
    """端到端：significance 必须带真实随机基准，且被接进返回结果"""
    res, _ = _run_synthetic(population_size=60, generations=10, top_k=3)
    s = res["significance"]
    assert s["available"] is True, f"置换检验没跑起来: {s.get('reason')}"
    assert s["n_candidates"] >= gf._PERM_MIN_CANDIDATES
    assert s["n_permutations"] == gf._PERM_N_PERMUTATIONS
    assert s["seed"] == gf._PERM_SEED
    assert s["null_max_distribution"]["mean"] > s["null_single_trial"]["mean"]
    assert 0.0 <= s["p_value_corrected"] <= 1.0
    assert isinstance(s["significant"], bool)
    assert res["summary"]["oos_confirmed"] <= len(res["top_factors"])
    assert "limitations" in res and res["limitations"]


# ── 工具：合成数据跑一次 evolve_factors ───────────────────────────────

def _run_synthetic(population_size=40, generations=8, top_k=3, seed=42):
    import random
    rng = np.random.default_rng(7)
    n = 800
    close = 20 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    data = {"close": close, "open": close, "high": close * 1.01, "low": close * 0.99,
            "volume": np.abs(rng.normal(1e6, 2e5, n)),
            "returns": np.concatenate(([0.0], np.diff(np.log(close))))}
    fwd = np.full(n, np.nan)
    for i in range(n - gf._FORWARD_HORIZON):
        fwd[i] = close[i + gf._FORWARD_HORIZON] / close[i] - 1.0

    orig = gf._prepare_data
    gf._gf_cache.clear()
    random.seed(seed)
    gf._prepare_data = lambda code, days=800: (data, fwd)
    try:
        res = gf.evolve_factors(code="SYN", population_size=population_size,
                               generations=generations, top_k=top_k)
    finally:
        gf._prepare_data = orig
        gf._gf_cache.clear()
    return res, data


# ============================================================
# 附加：_safe_div 的等价性与告警
# ============================================================

def test_safe_div_is_bitwise_equal_to_old_formulation_without_warning():
    """2026-09-13：`_safe_div` 从 `np.where(cond, 0.0, a/b)` 改为
    `np.divide(a, b, out=..., where=...)`。

    改的动机只有一条：旧写法会**先把 a/b 整体算出来**再挑，于是 b=0 的元素
    照样触发 `RuntimeWarning: divide by zero / invalid value`，每次跑测试刷屏，
    把真警告淹掉。`where=` 只在该位置计算。

    数值必须**逐位不变** —— 本用例就是这个保证。
    故障注入：把实现改回 `np.where(np.abs(b) < 1e-10, 0.0, a / b)` →
    告警断言转红（数值断言仍绿，证明这条测的是告警而非数值）。
    """
    import warnings

    def _old(a, b):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.where(np.abs(b) < 1e-10, 0.0, a / b)

    cases = [
        (np.array([1.0, 2.0, 3.0]), np.array([0.0, 2.0, 0.0])),      # 含零分母
        (np.array([1.0, 2.0, 3.0, 4.0]), np.array([2.0])),           # 广播
        (np.array([[1.0, 2.0], [3.0, 4.0]]),
         np.array([[0.0, 1.0], [1.0, 0.0]])),                        # 二维
        (np.array([1e-12, 5.0]), np.array([1e-11, 1.0])),            # 阈值边界
        (np.array([-3.0, 2.0]), np.array([4.0, -0.0])),              # 负零
        (np.arange(1200, dtype=float),
         np.where(np.arange(1200) % 7 == 0, 0.0, 1.5)),              # 规模
    ]
    for a, b in cases:
        old = np.asarray(_old(a, b), dtype=float)
        new = np.asarray(gf._safe_div(a, b), dtype=float)
        assert old.shape == new.shape, f"形状变了: {old.shape} vs {new.shape}"
        assert np.array_equal(old, new), f"数值不再逐位等价 (a={a!r}, b={b!r})"
        assert not np.isnan(new).any(), "出现了 NaN（旧实现不会有）"

    # 标量零分母：旧实现直接抛 ZeroDivisionError，新实现返回 0.0（更安全）
    assert float(np.asarray(gf._safe_div(1.0, 0.0))) == 0.0

    # 核心断言：不再发出 divide/invalid 告警
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gf._safe_div(np.array([1.0, 2.0]), np.array([0.0, 1.0]))
    offenders = [w for w in caught
                 if "divide" in str(w.message).lower()
                 or "invalid" in str(w.message).lower()]
    assert not offenders, (
        f"_safe_div 又发出了除法告警 {[str(w.message) for w in offenders]} —— "
        f"说明实现退回了「先整体算 a/b 再挑」的写法（np.where(cond, 0.0, a/b)）。"
        f"该写法数值相同但会刷屏掩盖真警告，应使用 np.divide(..., where=)。")
