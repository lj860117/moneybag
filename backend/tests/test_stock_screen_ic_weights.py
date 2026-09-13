"""P1-8 回归测试：cache_warmer 算出的 factor_ic 必须真的被读进选股权重

背景（P1-8 缺陷）：
    scripts/cache_warmer.py 每天把 compute_factor_ic() 的结果写进
    data/_cache/factor_ic.json（线上是真实数据），但全仓**没有任何读取方**
    —— 因子有效性算完就烂在磁盘里，选股权重一直只用
    config.STOCK_FACTOR_WEIGHTS_BY_REGIME 那张「经验值、未回测」的表。

修法：把 |IC| 按维度聚合，与查表权重线性混合，并把「这次是 table 还是
ic_blended」作为标记暴露出去（绝不静默混算）。

本文件是**故障注入测试**（项目规矩：每加一个判据必须配一个「摘掉判据就转红」
的用例）：
  1. 往 factor_ic 缓存塞一组明显 IC → 权重必须真的变，且标记为 ic_blended
  2. 缓存为空 → 权重必须逐位等于查表值，且标记为 table
  3. 缓存过期 / 样本不足 / 截面不足 / 不显著 → 必须原样返回查表值
  3b. **数据版本门**：载荷必须带 factor_ic 的显著性校正字段
     （p_adjusted < 0.05 且 n_tested 存在），旧载荷（只有裸 significant）
     一律拦下并给出 skip_reason=significance_not_corrected
  4. 返回值形状契约：恒为 7 维、无下划线元信息（元信息走独立接口）

注：用例只用「只属于单一维度」的因子做探针，避免 F01_PE（value+risk）/
F20_MOM_60D（growth+momentum）/F15、F16（quality+risk）这类跨维度因子
把用例语义搅浑。
"""
import json
import time

import pytest

import config as _config
from services import stock_screen as ss

DIM_KEYS = ("value", "growth", "quality", "momentum", "risk", "liquidity", "sentiment")
# import 期（未被 fixture 改写前）的 DATA_DIR，用于缓存路径契约测试
_DATA_DIR_DEFAULT = _config.DATA_DIR

# 只属 value 的因子（F01_PE 同属 risk，故意不用）
_VALUE_FACTORS = ("F02_PB", "F03_EP", "F04_ROE_PB", "F05_EPS")
# 只属 momentum 的因子（F20_MOM_60D 同属 growth，故意不用）
_MOMENTUM_FACTORS = ("F18_MOM_5D", "F19_MOM_20D", "F21_MOM_1D")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """每个用例：权重缓存清空 + factor_ic 缓存指向 tmp（默认不存在）。"""
    ss._weight_cache.clear()
    monkeypatch.setattr(ss, "_IC_WEIGHTS_CACHE_FILE", tmp_path / "factor_ic.json")
    yield
    ss._weight_cache.clear()


def _factor(ic, samples=200, n_periods=8, significant=True,
            p_adjusted=0.01, n_tested=17, significant_naive=True):
    """按 2026-09-13 **之后**的 factor_ic 载荷形状构造单因子记录。

    准入闸门（stock_screen._corrected_significance_ok）要求三项齐备：
      significant（校正后）为真 + p_adjusted < 0.05 + n_tested 为正整数。
    缺任一项都会被 skip_reason=significance_not_corrected 拦下，
    以免拿「未做自相关/多重检验校正」的裸 t 检验结论去调权。
    """
    return {"ic": ic, "samples": samples, "n_periods": n_periods,
            "significant": significant,
            "significant_corrected": significant,
            "significant_naive": significant_naive,
            "p_adjusted": p_adjusted if significant else None,
            "n_tested": n_tested}


def _legacy_factor(ic, samples=200, n_periods=8, significant=True):
    """2026-09-13 **之前**写出的旧载荷：只有裸 t 检验的 significant，
    没有 p_adjusted / n_tested / significant_naive（线上 2026-09-12 的
    factor_ic.json 就是这种形状，17 个因子全部 significant=False）。"""
    return {"ic": ic, "samples": samples, "n_periods": n_periods,
            "significant": significant}


def _write_ic_cache(path, factors, expires_offset=86400.0):
    """按 cache_warmer._save_cache 的落盘格式写一份 factor_ic 缓存。"""
    payload = {
        "data": {"factors": factors},
        "cached_at": "2026-09-13T10:00:00",
        "ttl_hours": 24,
        "expires_at": time.time() + expires_offset,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _table(regime):
    """未经 IC 修正的查表权重（直接读 config，不受注入影响）。"""
    from config import STOCK_FACTOR_WEIGHTS_BY_REGIME
    return ss._normalize_weights(STOCK_FACTOR_WEIGHTS_BY_REGIME[regime])


# ── 1. 注入明显 IC → 权重必须真变，且标记 ic_blended ─────────────────

def test_injected_ic_actually_changes_weights_and_flags_source(tmp_path):
    """核心故障注入：IC 强(value)/弱(momentum) 时，权重必须朝 IC 强的方向移动。

    摘掉「读 factor_ic」这段逻辑，本用例立刻转红（权重会停在查表值）。
    """
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.30) for f in _VALUE_FACTORS},
        **{f: _factor(0.02) for f in _MOMENTUM_FACTORS},
    })
    # 除 value / momentum 外的维度（含 sentiment）应完全不动
    table = _table("牛市")
    assert table["value"] == pytest.approx(0.12)
    assert table["momentum"] == pytest.approx(0.24)

    blended = ss.get_weights_for_regime("牛市")
    meta = ss.get_weights_for_regime_meta("牛市")

    assert meta["weights_source"] == "ic_blended", "IC 注入了却没标记来源"
    assert set(meta["ic_dims_used"]) == {"value", "momentum"}
    assert blended["value"] > table["value"], "IC 强的维度没有被提权"
    assert blended["momentum"] < table["momentum"], "IC 弱的维度没有被降权"
    # 未测维度的权重预算必须原样保留（不能因为「没测」就被抹成 0）
    for d in ("growth", "quality", "risk", "liquidity", "sentiment"):
        assert blended[d] == pytest.approx(table[d]), f"{d} 无 IC 度量却被动过"
    assert abs(sum(blended.values()) - 1.0) < 1e-9


def test_weights_change_equals_expected_blend_formula(tmp_path):
    """混合比例必须是 _IC_BLEND_LAMBDA，且只在有 IC 的维度间重分配预算"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.30) for f in _VALUE_FACTORS},
        **{f: _factor(0.02) for f in _MOMENTUM_FACTORS},
    })
    table = _table("牛市")
    got = ss.get_weights_for_regime("牛市")

    lam = ss._IC_BLEND_LAMBDA
    budget = table["value"] + table["momentum"]
    s_val, s_mom = 0.30, 0.02
    total = s_val + s_mom
    assert got["value"] == pytest.approx(
        (1 - lam) * table["value"] + lam * budget * (s_val / total), abs=1e-9)
    assert got["momentum"] == pytest.approx(
        (1 - lam) * table["momentum"] + lam * budget * (s_mom / total), abs=1e-9)


# ── 2. 缓存为空 → 必须逐位等于查表值，且标记 table ───────────────────

def test_empty_cache_returns_table_weights_bit_for_bit():
    """没有 factor_ic 缓存时，权重必须与旧实现逐位一致（不许悄悄改数）"""
    for regime in ("牛市", "熊市", "震荡", "轮动"):
        assert ss.get_weights_for_regime(regime) == _table(regime), \
            f"{regime} 在无 IC 缓存时权重被改动了"
        assert ss.get_weights_for_regime_meta(regime)["weights_source"] == "table"
    assert ss.get_weights_for_regime("火星牛市") == \
        ss._normalize_weights(ss.DEFAULT_DIM_WEIGHTS)
    assert ss.get_weights_for_regime_meta("火星牛市")["skip_reason"] == "regime_not_in_table"


def test_cache_present_but_no_known_factor_stays_table(tmp_path):
    """缓存里只有 factor_ic 不认识的因子 → 无维度可用，必须退回 table"""
    _write_ic_cache(tmp_path / "factor_ic.json", {"F99_NOT_A_FACTOR": _factor(0.9)})
    meta = ss.get_weights_for_regime_meta("熊市")
    assert meta["weights_source"] == "table"
    assert meta["skip_reason"] == "no_qualified_factor"


# ── 3. 闸门逐条验证（每条都是一个「摘掉就转红」的判据）───────────────

def test_expired_cache_is_ignored(tmp_path):
    _write_ic_cache(tmp_path / "factor_ic.json",
                    {**{f: _factor(-0.30) for f in _VALUE_FACTORS},
                     **{f: _factor(0.02) for f in _MOMENTUM_FACTORS}},
                    expires_offset=-3600)
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "table"
    assert meta["skip_reason"] == "ic_cache_unavailable"


def test_insufficient_samples_is_ignored(tmp_path):
    """样本不足的强 IC 必须被排除。

    value 组 |IC|=0.5 但样本 99 个（< 门槛）；momentum 组样本充足但 |IC| 很小。
    闸门若失效，value 会被大幅提权 —— 本用例即转红。
    """
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.50, samples=ss._IC_MIN_SAMPLES - 1) for f in _VALUE_FACTORS},
        **{f: _factor(0.01) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["ic_qualified_factors"] == len(_MOMENTUM_FACTORS), \
        "样本不足的 value 组不该算合格"
    assert meta["ic_dims_used"] in ([], ["momentum"])
    assert ss.get_weights_for_regime("牛市") == _table("牛市")
    assert meta["weights_source"] == "table"


def test_insufficient_periods_is_ignored(tmp_path):
    """截面数不足的强 IC 同样必须被排除（n_periods<3 时 IC 均值不可信）"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.50, n_periods=ss._IC_MIN_PERIODS - 1) for f in _VALUE_FACTORS},
        **{f: _factor(0.01) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert "value" not in (meta.get("ic_dims_used") or [])
    assert ss.get_weights_for_regime("牛市") == _table("牛市")


def test_all_qualified_but_none_significant_is_ignored(tmp_path):
    """合格但不显著 → 按 factor_ic 自己的建议不动权重，且原因可见"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.30, significant=False) for f in _VALUE_FACTORS},
        **{f: _factor(0.02, significant=False) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "table"
    assert meta["ic_qualified_factors"] == len(_VALUE_FACTORS) + len(_MOMENTUM_FACTORS)
    assert meta["ic_significant_factors"] == 0
    assert meta["skip_reason"] == "no_significant_factor"
    assert ss.get_weights_for_regime("牛市") == _table("牛市")


def test_single_measured_dim_reports_table_not_ic_blended(tmp_path):
    """只有一个维度有 IC 度量时数值不可能变 → 不许标记 ic_blended（防假绿）"""
    _write_ic_cache(tmp_path / "factor_ic.json",
                    {f: _factor(-0.30) for f in _VALUE_FACTORS})
    meta = ss.get_weights_for_regime_meta("牛市")
    assert ss.get_weights_for_regime("牛市") == _table("牛市")
    assert meta["weights_source"] == "table"
    assert meta["skip_reason"] == "weight_distribution_unchanged"


def test_corrupted_cache_file_degrades_without_raising(tmp_path):
    (tmp_path / "factor_ic.json").write_text("{ not json ", encoding="utf-8")
    w = ss.get_weights_for_regime("牛市")
    assert set(w) == set(DIM_KEYS)
    assert ss.get_weights_for_regime_meta("牛市")["weights_source"] == "table"


def test_none_ic_value_is_not_treated_as_zero(tmp_path):
    """IC 为 None（factor_ic 对无面板因子的表示）绝不能被当成 0 计权"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(None) for f in _VALUE_FACTORS},
        **{f: _factor(None) for f in _MOMENTUM_FACTORS},
    })
    assert ss.get_weights_for_regime_meta("牛市")["weights_source"] == "table"


# ── 3b. 数据版本门：只认「校正后显著」，旧载荷一律拦下 ────────────────
#
# 背景：线上 data/_cache/factor_ic.json（2026-09-12，17 个因子）里的
# `significant` 是裸 t 检验（|t|>=2）的结论，载荷里根本没有 p_adjusted /
# n_tested / significant_naive 这些键。若照单全收，等于隔着一层缓存继续拿
# 「未做自相关校正、未做多重检验校正」的显著性去调权 —— 闸门必须能识别
# 载荷版本，而不是「字段名叫 significant 就信它」。

def test_legacy_payload_is_rejected_as_significance_not_corrected(tmp_path):
    """旧载荷（只有裸 significant）→ 不许调权，且原因必须写明是「未校正」。

    故障注入：把 _IC_REQUIRE_CORRECTED_SIGNIFICANCE 关掉（模拟"放宽闸门"），
    本用例立刻转红 —— 权重会变成 ic_blended。
    """
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _legacy_factor(-0.80) for f in _VALUE_FACTORS},
        **{f: _legacy_factor(0.80) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "table", \
        "旧载荷（无 p_adjusted/n_tested）不得用于调权"
    assert meta["skip_reason"] == "significance_not_corrected", \
        f"skip_reason 必须能区分「未校正」，实际 {meta['skip_reason']!r}"
    assert meta["ic_qualified_factors"] == 7, "过样本量/截面门槛的因子应有 7 个"
    assert meta["ic_significant_factors"] == 7, "裸 significant 为真 → 7 个"
    assert meta["ic_corrected_significant_factors"] == 0, \
        "旧载荷没有任何一个因子带校正字段 → 校正后显著数必须是 0"
    assert ss.get_weights_for_regime("牛市") == _table("牛市")


def test_legacy_payload_would_blend_if_gate_were_relaxed(tmp_path, monkeypatch):
    """反向验证：上面那条红确实是闸门在起作用，不是巧合。

    把 _IC_REQUIRE_CORRECTED_SIGNIFICANCE 置 False（等价于"放宽一个字"），
    同样的旧载荷立刻被采信 → 证明闸门是**唯一**拦住它的东西。
    """
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _legacy_factor(-0.80) for f in _VALUE_FACTORS},
        **{f: _legacy_factor(0.80) for f in _MOMENTUM_FACTORS},
    })
    monkeypatch.setattr(ss, "_IC_REQUIRE_CORRECTED_SIGNIFICANCE", False)
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "ic_blended", \
        "关掉闸门后旧载荷就会被采信 —— 说明 test_legacy_payload_is_rejected... 的红来自闸门"
    assert set(meta["ic_dims_used"]) == {"value", "momentum"}


def test_significant_but_p_adjusted_too_large_is_rejected(tmp_path):
    """有校正字段但没过 BH（p_adjusted>=0.05）→ 同样不许调权"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.80, p_adjusted=0.31) for f in _VALUE_FACTORS},
        **{f: _factor(0.80, p_adjusted=0.31) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "table"
    assert meta["skip_reason"] == "significance_not_corrected"
    assert meta["ic_corrected_significant_factors"] == 0


def test_p_adjusted_exactly_at_threshold_is_rejected(tmp_path):
    """p_adjusted == 0.05 不算通过（判据是严格小于），边界不许含糊"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.80, p_adjusted=ss._IC_P_ADJUSTED_MAX) for f in _VALUE_FACTORS},
        **{f: _factor(0.80, p_adjusted=ss._IC_P_ADJUSTED_MAX) for f in _MOMENTUM_FACTORS},
    })
    assert ss.get_weights_for_regime_meta("牛市")["weights_source"] == "table"


def test_threshold_is_real_not_decorative(tmp_path, monkeypatch):
    """故障注入：把阈值放宽到 0.5 → 同一个 p_adjusted=0.31 的载荷立刻被采信"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.80, p_adjusted=0.31) for f in _VALUE_FACTORS},
        **{f: _factor(0.80, p_adjusted=0.31) for f in _MOMENTUM_FACTORS},
    })
    monkeypatch.setattr(ss, "_IC_P_ADJUSTED_MAX", 0.5)
    assert ss.get_weights_for_regime_meta("牛市")["weights_source"] == "ic_blended"


def test_missing_n_tested_is_rejected(tmp_path):
    """p_adjusted 有、但家族大小 n_tested 缺失 → 不可追溯，一样拦下"""
    bad = {f: {**_factor(-0.80), "n_tested": None} for f in _VALUE_FACTORS}
    bad.update({f: {**_factor(0.80), "n_tested": None} for f in _MOMENTUM_FACTORS})
    _write_ic_cache(tmp_path / "factor_ic.json", bad)
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "table"
    assert meta["skip_reason"] == "significance_not_corrected"


def test_bool_p_adjusted_is_not_accepted_as_number(tmp_path):
    """p_adjusted 被写成布尔（Python 里 bool 是 int 子类）→ 不许当成 0/1 数字"""
    bad = {f: {**_factor(-0.80), "p_adjusted": True} for f in _VALUE_FACTORS}
    bad.update({f: {**_factor(0.80), "p_adjusted": True} for f in _MOMENTUM_FACTORS})
    _write_ic_cache(tmp_path / "factor_ic.json", bad)
    assert ss.get_weights_for_regime_meta("牛市")["weights_source"] == "table"


def test_live_20260912_payload_shape_is_rejected(tmp_path):
    """按线上 2026-09-12 factor_ic.json 的**真实键集合**复刻一份载荷。

    线上实测（team-lead 核对）：17 个因子，`significant` 全为 False，且
    载荷里**根本没有** significant / n_periods / p_adjusted / n_tested 这些键，
    只有 ic / abs_ic / samples / level / effective / direction /
    invalid_reason / rank / name_cn —— 这是 V1 时期的旧 schema。

    预期：三重拦截里最先命中的是 n_periods 缺失（periods=0 < 门槛）→
    no_qualified_factor，权重逐位等于查表值。即便补上 n_periods，
    也会因为 significant 全 False 走到 no_significant_factor；
    再即便有 significant=True，缺 p_adjusted/n_tested 仍会被
    significance_not_corrected 拦下 —— 三道门彼此独立，任一道都够。
    """
    keys = ("ic", "abs_ic", "samples", "level", "effective",
            "direction", "invalid_reason", "rank", "name_cn")
    live = {}
    for i, f in enumerate(ss._IC_FACTORS_BY_DIM["value"] +
                           ss._IC_FACTORS_BY_DIM["momentum"]):
        rec = {"ic": 0.02 + 0.001 * i, "abs_ic": 0.02 + 0.001 * i,
               "samples": 198, "level": "微弱", "effective": False,
               "direction": "正向", "invalid_reason": "ic_low",
               "rank": i + 1, "name_cn": f}
        assert set(rec) == set(keys)
        live[f] = rec
    _write_ic_cache(tmp_path / "factor_ic.json", live)

    meta = ss.get_weights_for_regime_meta("牛市")
    assert ss.get_weights_for_regime("牛市") == _table("牛市")
    assert meta["weights_source"] == "table"
    assert meta["ic_qualified_factors"] == 0, "缺 n_periods → 一律不算合格"
    assert meta["ic_significant_factors"] == 0
    assert meta["ic_corrected_significant_factors"] == 0
    assert meta["skip_reason"] == "no_qualified_factor"


def test_live_shaped_payload_with_n_periods_but_no_significance(tmp_path):
    """把线上载荷补齐 n_periods（其余不变）→ 拦截点前移到 no_significant_factor。

    这份用例钉住"三道门互相独立"这件事：补一道不解决问题。
    """
    live = {}
    for call_i, f in enumerate(ss._IC_FACTORS_BY_DIM["value"] +
                               ss._IC_FACTORS_BY_DIM["momentum"]):
        live[f] = {"ic": 0.02 + 0.001 * call_i, "abs_ic": 0.02,
                   "samples": 198, "n_periods": 11, "level": "微弱",
                   "effective": False, "direction": "正向",
                   "invalid_reason": "ic_low", "rank": call_i + 1,
                   "name_cn": f}
    _write_ic_cache(tmp_path / "factor_ic.json", live)
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["ic_qualified_factors"] == len(live)
    assert meta["ic_significant_factors"] == 0
    assert meta["skip_reason"] == "no_significant_factor"


def test_corrected_payload_is_admitted_with_provenance(tmp_path):
    """新载荷（三项齐备）必须放行，并把校正后显著因子数如实报出来"""
    _write_ic_cache(tmp_path / "factor_ic.json", {
        **{f: _factor(-0.30) for f in _VALUE_FACTORS},
        **{f: _factor(0.02) for f in _MOMENTUM_FACTORS},
    })
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "ic_blended"
    assert meta["skip_reason"] == ""
    assert meta["ic_corrected_significant_factors"] == 7
    assert meta["ic_significant_factors"] == 7
    # 逐因子明细里必须带上 p_adjusted / n_tested，便于事后核对
    used = meta["ic_dims_used"]
    assert set(used) == {"value", "momentum"}
    for dim in used:
        for rec in meta["ic_factors_by_dim"][dim]:
            assert rec["p_adjusted"] is not None
            assert rec["n_tested"] == 17


# ── 4. 返回值形状契约（既有测试锁死了这条，这里再显式钉一次）─────────

def test_return_shape_is_exactly_seven_dims(tmp_path):
    """get_weights_for_regime 必须只返回 7 个维度，元信息走独立接口"""
    _write_ic_cache(tmp_path / "factor_ic.json",
                    {**{f: _factor(-0.30) for f in _VALUE_FACTORS},
                     **{f: _factor(0.02) for f in _MOMENTUM_FACTORS}})
    for regime in ("牛市", "熊市", "火星牛市", None, ""):
        w = ss.get_weights_for_regime(regime)
        assert set(w) == set(DIM_KEYS)
        assert not any(k.startswith("_") for k in w)
        assert abs(sum(w.values()) - 1.0) < 1e-9


def test_blend_is_deterministic_across_calls(tmp_path):
    _write_ic_cache(tmp_path / "factor_ic.json",
                    {**{f: _factor(-0.30) for f in _VALUE_FACTORS},
                     **{f: _factor(0.02) for f in _MOMENTUM_FACTORS}})
    assert ss.get_weights_for_regime("牛市") == ss.get_weights_for_regime("牛市")


# ── 5. 缓存路径契约（防与 cache_warmer 写入口径漂移）─────────────────

# 在 autouse fixture 改写模块常量之前，先把 import 期的真实路径记下来
_IMPORT_TIME_IC_FILE = ss._IC_WEIGHTS_CACHE_FILE


def test_cache_path_matches_cache_warmer_writer():
    """读取路径必须与 scripts/cache_warmer._save_cache 的写入口径一致。

    cache_warmer: CACHE_DIR = <DATA_DIR>/_cache，文件名 f"{name}.json"
    本模块必须读同一棵树，否则「IC 写了没人读」会变成「IC 写了读错地方」。
    """
    assert _IMPORT_TIME_IC_FILE == _DATA_DIR_DEFAULT / "_cache" / "factor_ic.json"
    assert _IMPORT_TIME_IC_FILE.name == "factor_ic.json"


def test_reads_real_cache_warmer_payload_shape(tmp_path):
    """按 cache_warmer._save_cache 的真实 payload（data/cached_at/ttl_hours/expires_at）读取"""
    import time as _t
    payload = {
        "data": {"factors": {**{f: _factor(-0.30) for f in _VALUE_FACTORS},
                             **{f: _factor(0.02) for f in _MOMENTUM_FACTORS}}},
        "cached_at": "2026-09-13T18:10:00",
        "ttl_hours": 18,
        "expires_at": _t.time() + 18 * 3600,
    }
    (tmp_path / "factor_ic.json").write_text(
        __import__("json").dumps(payload), encoding="utf-8")
    meta = ss.get_weights_for_regime_meta("牛市")
    assert meta["weights_source"] == "ic_blended"
    assert meta["ic_cache_age_hours"] is not None
