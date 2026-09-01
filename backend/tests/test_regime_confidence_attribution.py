"""
Regime判断置信度归因守护测试
============================================================================
背景（2026-09 周度自检 medium 级问题）：LLM 审计层给出
`{"severity": "medium", "category": "功能风险",
"description": "13维信号置信度仅45%、Regime判断置信度仅55%，均处于较低区间，
易让用户对信号可靠性产生误判"}`。

根因链（team-lead 排查确认，本文件测试的正是修复后的行为）：
  1. `services/signal.py` 的置信度计算本身设计正确：低置信度分
     `market_divergence`（多空分歧大）/`weak_signal`（信号强度弱）两类
     归因，都是震荡市正常特征，只有 `data_missing` 才是真正的问题。
  2. `use_cases/self_audit.py` 给 LLM 的豁免说明只提到了"13维信号"，完全
     没提"Regime判断"，导致 LLM 看到"Regime判断confidence=55"时没有对应
     的豁免依据，把两者混在一起误判成同一类"功能风险"。
  3. `services/regime_engine.py::classify()` 的置信度设计上按市场状态分
     区间（震荡市40-80，轮动市70-85，趋势牛/高波熊55-95），但返回结果里
     从未携带任何类似 `confidence_note` 的归因字段——跟 signal.py 已经
     做过的模式（数据源头带归因）不一致。

修复内容（采纳"数据源头补充confidence_note字段"的更彻底方案，而不是只改
prompt静态豁免规则）：
  1. `services/regime_engine.py::classify()` 新增 `confidence_note` 字段，
     基于 `_REGIME_CONFIDENCE_DESIGN_RANGES` 给出该 regime 的置信度设计
     区间说明；地缘风险覆盖场景（geo_override=True）单独给出说明，不套用
     常规区间校验；置信度真的超出设计区间时给出 ⚠️ 警示文案。
  2. `use_cases/self_audit.py` 第590行左右豁免说明加入"Regime判断"，明确
     区间标准；`run_smoke_tests()` 里 Regime 判断的 `value_summary` 拼接
     逻辑新增 `confidence_note`（参照13维信号已有的 `note` 拼接写法）。

这个文件测什么：
  - `_confidence_note_for_regime()` 纯函数：4种regime × 落在/超出设计区间
    的组合，以及地缘风险覆盖场景，返回的说明文案是否正确区分"正常"和
    "异常"。
  - `classify()` 整体（用 monkeypatch 隔离 `_get_market_params`/
    `_classify_regime`/地缘风险模块，不发真实网络请求）：返回结果里
    `confidence_note` 字段是否存在、内容是否与置信度/regime匹配；异常
    降级路径（数据获取失败）是否也带有说明性的 confidence_note。
  - `run_smoke_tests()` 里 Regime 判断部分：`value_summary` 是否正确
    拼接了 `confidence_note`（用 monkeypatch 替换 `services.regime_engine`
    整个模块，不依赖真实市场数据）。
  - `use_cases/self_audit.py` 源码里的 LLM 豁免说明文本是否真的提到了
    "Regime判断"（防止未来重构时又把这条豁免说明漏掉，回归成本次bug的
    症状）。

不测什么：
  - `_get_market_params()`/`_classify_regime()` 的具体打分规则是否合理
    （那是产品/策略判断，属于另一个话题，不是本次bug的范畴）。
  - LLM 审计层是否真的读懂了这条豁免说明并据此不上报（那需要真实 LLM
    调用，属于集成测试范畴）。
"""
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import services.regime_engine as regime_engine


# ============================================================
# _confidence_note_for_regime() —— 纯函数，覆盖所有分支
# ============================================================

@pytest.mark.parametrize(
    "regime,confidence",
    [
        ("oscillating", 40),
        ("oscillating", 55),
        ("oscillating", 80),
        ("trending_bull", 60),
        ("trending_bull", 95),
        ("high_vol_bear", 55),
        ("high_vol_bear", 95),
        ("rotation", 70),
        ("rotation", 85),
    ],
)
def test_confidence_note_marks_in_range_values_as_normal(regime, confidence):
    """置信度落在该 regime 的设计区间内（包含边界值）时，说明文案必须
    标注为"正常范围"，不能触发警示——这是本次 bug 修复的核心场景：
    Regime判断 confidence=55（震荡市）不应被误判为异常。"""
    note = regime_engine._confidence_note_for_regime(regime, confidence, geo_override=False)
    assert "正常范围" in note
    assert "⚠️" not in note


@pytest.mark.parametrize(
    "regime,confidence",
    [
        ("oscillating", 10),
        ("oscillating", 39),
        ("oscillating", 81),
        ("oscillating", 99),
        ("trending_bull", 10),
        ("trending_bull", 59),
        ("high_vol_bear", 5),
        ("high_vol_bear", 54),
        ("rotation", 20),
        ("rotation", 69),
        ("rotation", 86),
    ],
)
def test_confidence_note_flags_out_of_range_values_as_anomaly(regime, confidence):
    """置信度明显超出该 regime 的设计区间时，必须带 ⚠️ 警示且建议排查——
    这是真正需要人工关注的异常情况，不能被本次修复"过度豁免"掉。"""
    note = regime_engine._confidence_note_for_regime(regime, confidence, geo_override=False)
    assert "⚠️" in note
    assert "超出" in note


def test_confidence_note_geo_override_scenario_skips_range_check():
    """地缘风险覆盖（geo_override=True）时，confidence 被人为提升到 ≥70
    作为风控保护性判断，不是常规市场状态分类结果，不应套用设计区间校验
    ——即使这个置信度数值本身在 high_vol_bear 的正常区间内，也应该走
    地缘覆盖的专属说明分支，而不是常规区间说明。"""
    note = regime_engine._confidence_note_for_regime("high_vol_bear", 70, geo_override=True)
    assert "地缘风险覆盖" in note
    assert "⚠️" not in note
    assert "正常范围" not in note  # 走的是专属文案分支，不是区间校验分支


def test_confidence_note_unknown_regime_falls_back_to_full_range():
    """未知的 regime 名称（防御性兜底）应该落到 (0,100) 的宽松区间，
    不应该抛异常或误报警示——避免未来新增 regime 类型时忘记更新
    `_REGIME_CONFIDENCE_DESIGN_RANGES` 导致的连带故障。"""
    note = regime_engine._confidence_note_for_regime("unknown_regime_xyz", 50, geo_override=False)
    assert "⚠️" not in note


# ============================================================
# classify() —— 整体行为（monkeypatch 隔离外部依赖）
# ============================================================

def _fake_geo_module_no_override():
    fake = types.ModuleType("services.geopolitical")
    fake.get_geopolitical_events = lambda: {"max_severity": 0, "top_category": ""}
    return fake


def test_classify_normal_oscillating_scenario_includes_confidence_note(monkeypatch):
    """正常震荡市场景（无地缘覆盖）：classify() 返回结果必须包含
    confidence_note 字段，且内容与 oscillating 的设计区间说明一致。"""
    regime_engine._regime_cache.clear()

    monkeypatch.setattr(
        regime_engine, "_get_market_params",
        lambda: {"volatility_20d": 22, "return_20d": 1.0, "return_5d": 0.2,
                 "ma_bullish": False, "ma_bearish": False, "above_ma60": True,
                 "vol_ratio": 1.0},
    )
    monkeypatch.setitem(sys.modules, "services.geopolitical", _fake_geo_module_no_override())

    result = regime_engine.classify(force=True)

    assert result["regime"] == "oscillating"
    assert "confidence_note" in result
    assert "震荡市" in result["confidence_note"]
    assert "⚠️" not in result["confidence_note"]


def test_classify_geo_override_scenario_confidence_note_mentions_override(monkeypatch):
    """地缘风险覆盖场景：classify() 强制切到 high_vol_bear 且 confidence
    被提升到≥70时，confidence_note 必须说明这是地缘覆盖导致的，而不是
    常规市场状态判断——否则审计层会拿常规区间标准去校验一个非常规值，
    产生新的误判。"""
    regime_engine._regime_cache.clear()

    monkeypatch.setattr(
        regime_engine, "_get_market_params",
        lambda: {"volatility_20d": 15, "return_20d": 0.5, "return_5d": 0.1,
                 "ma_bullish": False, "ma_bearish": False, "above_ma60": True,
                 "vol_ratio": 1.0},
    )
    fake_geo = types.ModuleType("services.geopolitical")
    fake_geo.get_geopolitical_events = lambda: {"max_severity": 5, "top_category": "地缘冲突"}
    monkeypatch.setitem(sys.modules, "services.geopolitical", fake_geo)

    result = regime_engine.classify(force=True)

    assert result["geo_override"] is True
    assert result["regime"] == "high_vol_bear"
    assert result["confidence"] >= 70
    assert "地缘风险覆盖" in result["confidence_note"]


def test_classify_data_failure_fallback_has_explanatory_confidence_note(monkeypatch):
    """数据获取失败降级为默认震荡（confidence=30，低于震荡市正常区间
    40-80下限）时，confidence_note 必须说明这是保底值、不代表真实置信度
    评估——否则这个 confidence=30 会被常规区间校验误判为"异常需排查"，
    但实际上它本来就是已知的降级路径，另有专门说明。"""
    regime_engine._regime_cache.clear()

    def _boom():
        raise RuntimeError("模拟数据源全部失败")

    monkeypatch.setattr(regime_engine, "_get_market_params", _boom)

    result = regime_engine.classify(force=True)

    assert result["regime"] == "oscillating"
    assert result["confidence"] == 30
    assert "confidence_note" in result
    assert "数据获取失败" in result["confidence_note"] or "保底值" in result["confidence_note"]


def test_classify_cache_hit_returns_same_confidence_note(monkeypatch):
    """未强制刷新时应命中缓存，且缓存内容（包括 confidence_note）保持
    一致——避免"缓存命中路径漏填 confidence_note"这种新老代码不一致的
    回归。"""
    regime_engine._regime_cache.clear()

    monkeypatch.setattr(
        regime_engine, "_get_market_params",
        lambda: {"volatility_20d": 15, "return_20d": 5.0, "return_5d": 2.0,
                 "ma_bullish": True, "ma_bearish": False, "above_ma60": True,
                 "vol_ratio": 1.0},
    )
    monkeypatch.setitem(sys.modules, "services.geopolitical", _fake_geo_module_no_override())

    first = regime_engine.classify(force=True)
    second = regime_engine.classify(force=False)  # 应该命中缓存

    assert first["regime"] == "trending_bull"
    assert second["confidence_note"] == first["confidence_note"]


# ============================================================
# use_cases/self_audit.py —— run_smoke_tests() 的 Regime 判断段落
# ============================================================

def test_run_smoke_tests_regime_section_includes_confidence_note_in_summary(monkeypatch):
    """run_smoke_tests() 里 Regime 判断部分的 value_summary 必须携带
    confidence_note（跟13维信号已有的 note 拼接方式保持一致），否则
    LLM 审计层只能看到孤零零一个数字，无法判断这个置信度是否符合设计
    预期。"""
    import use_cases.self_audit as self_audit

    fake_regime_module = types.ModuleType("services.regime_engine")
    fake_regime_module.classify = lambda: {
        "regime": "oscillating",
        "confidence": 55,
        "confidence_note": "震荡市（默认状态）：置信度天然落在40-80区间（设计区间40-80，当前55属于正常范围）",
    }
    monkeypatch.setitem(sys.modules, "services.regime_engine", fake_regime_module)

    # 13维信号/用户记忆/RAG/LLM配置等其余 smoke test 分支各自独立 try/except，
    # 让它们按各自逻辑失败也不影响本测试要验证的 Regime 判断段落。
    results = self_audit.run_smoke_tests()

    regime_result = next(r for r in results if r["name"] == "Regime判断")
    assert regime_result["status"] == "pass"
    assert "confidence=55" in regime_result["value_summary"]
    assert "note=" in regime_result["value_summary"]
    assert "正常范围" in regime_result["value_summary"]


def test_run_smoke_tests_regime_section_without_confidence_note_still_works(monkeypatch):
    """向后兼容：如果 regime_engine.classify() 某次意外没有返回
    confidence_note（比如老版本缓存数据还未刷新），run_smoke_tests() 也
    不应该崩溃，只是 value_summary 里不带 note 部分。"""
    import use_cases.self_audit as self_audit

    fake_regime_module = types.ModuleType("services.regime_engine")
    fake_regime_module.classify = lambda: {"regime": "rotation", "confidence": 70}
    monkeypatch.setitem(sys.modules, "services.regime_engine", fake_regime_module)

    results = self_audit.run_smoke_tests()

    regime_result = next(r for r in results if r["name"] == "Regime判断")
    assert regime_result["status"] == "pass"
    assert "regime=rotation, confidence=70" == regime_result["value_summary"]


def test_llm_audit_prompt_exemption_note_mentions_regime_judgement():
    """守护"豁免说明遗漏Regime判断"这个具体bug不再回归：
    use_cases/self_audit.py 源码里必须存在一条提及"Regime判断"和置信度
    设计区间的豁免说明，且要区分开"13维信号"和"Regime判断"是两条独立
    的豁免规则（本次bug的根因正是两者被合并成一条、只覆盖了13维信号）。
    """
    source = (BACKEND_DIR / "use_cases" / "self_audit.py").read_text(encoding="utf-8")

    assert "Regime判断" in source
    # 豁免说明必须明确给出设计区间数字，而不是笼统一句话
    assert "40-80" in source
    assert "70-85" in source
    assert "55-95" in source
    # 必须仍然保留13维信号原有的豁免说明（不能因为加了Regime就把原有的删掉）
    assert "13维信号" in source
    assert "confidence_reason" in source


# ============================================================
# 防脱节性质测试（QA复核发现，2026-09）：_REGIME_CONFIDENCE_DESIGN_RANGES
# 常量表必须与 _classify_regime() 的真实可达置信度范围精确一致。
# ============================================================
#
# 背景：QA用穷举脚本核对时发现 rotation 常量原写(65,85)，但
# rotation_score 的4个加分项{30,25,25,20}子集和永远凑不出65（可能值仅
# 0,20,25,30,45,50,55,70,75,80,100），触发阈值>=65时实际最小可达值是70，
# 65-69是不可达死区。已改成(70,85)修复这个死区，但如果不加自动化测试，
# 未来有人改了 _classify_regime() 的打分权重（比如把某个+20改成+15）却
# 忘记同步这个常量表，现有的手工参数化测试不会报错——因为它们只是拿
# 常量表里写死的边界值去测函数本身逻辑对不对，不会反向验证常量表是否仍
# 精确匹配打分逻辑的真实输出范围。这里用穷举全部布尔条件组合 ×
# 关键阈值内外采样点，重新计算每个 regime 分支真实能达到的
# confidence 最小/最大值，与 _REGIME_CONFIDENCE_DESIGN_RANGES 逐一核对。

def _enumerate_reachable_regime_confidence_ranges() -> dict[str, tuple[int, int]]:
    """穷举 _classify_regime() 在所有输入组合下，每个 regime 真实能
    达到的 confidence 最小值/最大值。跨越阈值两侧各采样若干点，覆盖
    "刚好命中"和"远超阈值"两种情况。"""
    from services.regime_engine import _classify_regime

    bool_combos = [
        (mb, mr, a60)
        for mb in (True, False)
        for mr in (True, False)
        for a60 in (True, False)
    ]
    # 数值型参数跨阈值采样：阈值本身、阈值内侧一点、阈值外侧一点、极值
    vol_samples = [5, 15, 19.9, 20, 24.9, 25, 29.9, 30, 30.1, 50, 80]
    ret20_samples = [-20, -10, -5.1, -5, -2.9, 0, 2.9, 3, 3.1, 10, 20]
    ret5_samples = [-5, 0, 0.9, 1, 1.1, 5]
    vol_ratio_samples = [0.3, 0.79, 0.8, 0.81, 1.0, 1.5]

    ranges: dict[str, list[float]] = {
        "trending_bull": [], "high_vol_bear": [], "rotation": [], "oscillating": [],
    }
    for ma_bull, ma_bear, above_60 in bool_combos:
        for vol in vol_samples:
            for ret_20 in ret20_samples:
                for ret_5 in ret5_samples:
                    for vol_ratio in vol_ratio_samples:
                        regime, conf, _ = _classify_regime({
                            "volatility_20d": vol, "return_20d": ret_20,
                            "return_5d": ret_5, "ma_bullish": ma_bull,
                            "ma_bearish": ma_bear, "above_ma60": above_60,
                            "vol_ratio": vol_ratio,
                        })
                        ranges[regime].append(conf)

    return {
        regime: (min(vals), max(vals))
        for regime, vals in ranges.items() if vals
    }


def test_design_ranges_constant_matches_actual_reachable_confidence():
    """_REGIME_CONFIDENCE_DESIGN_RANGES 常量表必须与 _classify_regime()
    穷举得到的真实可达范围精确一致——这是QA复核发现rotation死区问题后
    补充的防脱节测试。如果这个测试失败，说明有人改了打分权重但没有
    同步更新常量表，_confidence_note_for_regime() 给出的"设计区间"
    说明就会与真实情况脱节（比如把某个真实无法达到的死区误标为"正常"，
    或者反过来把真实能达到的值误标为"⚠️异常"）。
    """
    actual = _enumerate_reachable_regime_confidence_ranges()

    for regime, (doc_lo, doc_hi, _desc) in regime_engine._REGIME_CONFIDENCE_DESIGN_RANGES.items():
        assert regime in actual, f"穷举样本未覆盖到 {regime}，需要扩大采样点"
        real_lo, real_hi = actual[regime]
        assert doc_lo == real_lo, (
            f"{regime} 常量下限={doc_lo} 与穷举得到的真实下限={real_lo} 不一致——"
            f"常量表与 _classify_regime() 打分逻辑已脱节，请同步更新"
            f"_REGIME_CONFIDENCE_DESIGN_RANGES"
        )
        assert doc_hi == real_hi, (
            f"{regime} 常量上限={doc_hi} 与穷举得到的真实上限={real_hi} 不一致——"
            f"常量表与 _classify_regime() 打分逻辑已脱节，请同步更新"
            f"_REGIME_CONFIDENCE_DESIGN_RANGES"
        )
