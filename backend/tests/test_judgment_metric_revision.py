#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断追踪「成绩单口径」修订回归测试 —— 2026-09-13。

背景（全部为线上实测证据，不是推测）
------------------------------------
线上 `data/judgments/LeiJiang/` 有 92 条判断记录（71 条已验证，2026-05 ~ 2026-09），
成绩单显示的准确率是 8.5%，而同期「永远喊多」的基线是 56%。查明三个叠加缺陷：

  D1 取数窗口与预测日无关
     `_get_actual_return` 写的是 `recent = data[0]; older = data[VERIFY_DAYS]`，
     并配注释「data 是按日期降序排列」。实际 `tushare_data.get_index_daily`
     的 docstring 与 sort 都是**升序**。
     线上实测：`get_index_daily("000300.SH", days=25)` 返回 40 行，
     data[0]=20260720 / data[15]=20260810 → 算出的是「今天往前 34~55 天」
     这段窗口。71 条已验记录只有 23 个不同的 actual_return 值（同日批量验证
     必然同值），前 12 条全是 -7.28。

  D2 判决口径禁用了「中性」这个类别
     固定 ±0.5% 阈值 + `dir == "neutral"` 才算对，实测 neutral 命中 0/70。
     且 blocked（风控拦截、根本没做预测）被算进方向命中率分母，属类别错误。

  D3 一致分单位混算
     `pipeline_runner.step_confidence_gate` 用 `result.get("score", 0.5)`：
     三套互不兼容的量纲（broker_research 0~1 / market_factors、sector_rotation
     0~100 / factor_data -100~100），而十来个模块根本不写 score 被静默填 0.5；
     且缺 /100 归一化，下游 `int(confidence_score * 100)` 再乘一次。
     线上实测：92 条记录 14 条 confidence > 100，最高 1041。

本文件的写法遵循项目规矩：**每条判据都配故障注入断言** —— 摘掉修复后测试必须转红，
不接受只在修复后才成立的"死测试"。因此多处同时断言「新口径的值」与
「旧口径的值必须不同」，让回退修复立刻暴露。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from services import judgment_tracker as jt
from services.decision_context import DecisionContext
from services.pipeline_runner import step_confidence_gate


# ============================================================
# 工具
# ============================================================

def _ascending_index(days_back: int = 90, base_close: float = 100.0) -> list:
    """构造一段**升序**的指数序列（与 get_index_daily 真实返回一致），
    每个自然日一条，close = base_close + i（便于手算期望值）。"""
    today = datetime.now().date()
    rows = []
    for i in range(days_back, -1, -1):
        d = today - timedelta(days=i)
        rows.append({
            "trade_date": d.strftime("%Y%m%d"),
            "close": base_close + (days_back - i),
        })
    return rows


def _patch_index(monkeypatch, rows):
    """patch services.tushare_data.get_index_daily（被测函数内部是按这个名字 import 的）"""
    import services.tushare_data as td
    monkeypatch.setattr(td, "get_index_daily", lambda *a, **k: rows, raising=True)


def _write_records(uid: str, month: str, records: list) -> None:
    d = jt._judgments_dir(uid)
    (d / f"{month}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _rec(recorded_at: str, direction: str, verify_at: str, **kw) -> dict:
    base = {
        "id": "j_" + recorded_at,
        "user_id": "qa_metric",
        "recorded_at": recorded_at,
        "verify_at": verify_at,
        "verified": False,
        "verdict": None,
        "actual_return": None,
        "direction": direction,
        "confidence": 55,
        "regime": "neutral",
        "weighted_score": 0.55,
        "divergence": 0.1,
        "gate_decision": "llm_arbitration",
        "module_snapshots": {},
    }
    base.update(kw)
    return base


# ============================================================
# D1 取数窗口
# ============================================================

def test_window_return_is_anchored_to_recorded_at(monkeypatch):
    """必须算 [recorded_at, recorded_at+15个交易日]，不能算「今天往前 N 天」。"""
    rows = _ascending_index(days_back=90)
    _patch_index(monkeypatch, rows)

    recorded = (datetime.now().date() - timedelta(days=60)).isoformat()
    got = jt._index_window_return(recorded)

    # 期望值：从 recorded 起的第 0 个交易日 → 第 15 个交易日
    base_c = recorded.replace("-", "")
    i0 = next(i for i, r in enumerate(rows) if r["trade_date"] >= base_c)
    expect = round(
        (rows[i0 + jt.VERIFY_DAYS]["close"] - rows[i0]["close"])
        / rows[i0]["close"] * 100, 2
    )
    assert got == pytest.approx(expect), f"锚定窗口算错: got={got} expect={expect}"

    # ── 故障注入：旧实现取 data[0]/data[15]，结果必然不同 ──
    old_style = round(
        (rows[0]["close"] - rows[jt.VERIFY_DAYS]["close"])
        / rows[jt.VERIFY_DAYS]["close"] * 100, 2
    )
    assert got != pytest.approx(old_style), (
        "新旧口径算出同一个数，说明这条测试没有区分能力（旧实现取 data[0]/data[15]）"
    )


def test_window_return_detects_descending_source_and_fails_loudly(monkeypatch):
    """取数源若变成降序，必须抛错而不是静默算错 —— 静默会让整张成绩单失真。"""
    rows = list(reversed(_ascending_index(days_back=90)))
    _patch_index(monkeypatch, rows)

    recorded = (datetime.now().date() - timedelta(days=60)).isoformat()
    with pytest.raises(AssertionError):
        jt._index_window_return(recorded)

    # _get_actual_return 必须把 AssertionError 透传（不许被 except Exception 吞掉，
    # 否则「口径被改坏」会伪装成「数据不可用」而被静默跳过）
    with pytest.raises(AssertionError):
        jt._get_actual_return("qa_metric", {"recorded_at": recorded})


def test_window_return_none_when_observation_not_elapsed(monkeypatch):
    """观察期没走完 → None（这是「还没到期」，不是「判错」）。"""
    rows = _ascending_index(days_back=90)
    _patch_index(monkeypatch, rows)
    recorded = (datetime.now().date() - timedelta(days=3)).isoformat()
    assert jt._index_window_return(recorded) is None


def test_window_return_none_when_recorded_at_missing():
    assert jt._index_window_return(None) is None
    assert jt._index_window_return("") is None
    assert jt._index_window_return("not-a-date") is None


# ============================================================
# D2 判决口径
# ============================================================

@pytest.mark.parametrize("direction", ["neutral", "blocked"])
def test_neutral_and_blocked_are_no_view_not_wrong(direction):
    """neutral / blocked 不进方向命中率分母。

    blocked = 风控拦截、根本没做预测，判它「错」是类别错误。
    故障注入：旧口径下 neutral 遇大涨会被判 "wrong"，这条断言立刻挂。
    """
    v = jt.judge_verdict(direction, 8.0, 0.5)
    assert v == "no_view"
    assert v != "wrong"
    assert v != "correct"


def test_within_band_is_partial_not_directional():
    """市场没动（落在中性带内）→ partial，方向对错都不算。"""
    assert jt.judge_verdict("bullish", 0.2, 0.5) == "partial"
    assert jt.judge_verdict("bearish", -0.2, 0.5) == "partial"


def test_directional_sign_judgement():
    assert jt.judge_verdict("bullish", 5.0, 0.5) == "correct"
    assert jt.judge_verdict("bearish", -5.0, 0.5) == "correct"
    assert jt.judge_verdict("bullish", -5.0, 0.5) == "wrong"
    assert jt.judge_verdict("bearish", 5.0, 0.5) == "wrong"


def test_band_is_adaptive_not_fixed_half_percent(monkeypatch):
    """中性带必须随波动率变，不能是写死的 ±0.5%。

    故障注入：若有人把它改回固定 0.5，低波动/高波动两段数据的带宽会相同 → 挂。
    """
    quiet = [{"trade_date": f"2026{i:04d}", "close": 100.0 + i * 0.001}
             for i in range(1, 41)]
    _patch_index(monkeypatch, quiet)
    band_quiet = jt.neutral_band()

    wild = [{"trade_date": f"2026{i:04d}",
             "close": 100.0 * (1 + (0.05 if i % 2 else -0.05)) ** (i // 2)}
            for i in range(1, 41)]
    _patch_index(monkeypatch, wild)
    band_wild = jt.neutral_band()

    assert band_quiet >= jt.NEUTRAL_BAND_FLOOR
    assert band_wild > band_quiet, "中性带没有随波动率变化，说明又变成固定阈值了"


def test_band_falls_back_explicitly_when_no_data(monkeypatch):
    """取不到波动率 → 显式降级为常量并返回，不是造一个数。"""
    _patch_index(monkeypatch, [])
    assert jt.neutral_band() == jt.NEUTRAL_BAND_FALLBACK


# ============================================================
# D1 附带：verify_pending 必须扫全部月份（旧实现只扫今天和上一个月）
# ============================================================

def test_verify_pending_scans_all_month_files(monkeypatch):
    """三个月前的到期记录也必须被补验 —— 旧实现只扫 [0, -1]，
    线上实测导致 2026-06 的 10 条记录永久丢失。"""
    uid = "qa_all_months"
    today = datetime.now().date()
    old_day = (today - timedelta(days=120)).isoformat()
    due = (today - timedelta(days=100)).isoformat()

    for month in ["2026-03", "2026-04", "2026-05"]:
        _write_records(uid, month, [_rec(f"{month}-01T09:00:00", "bullish", due)])

    monkeypatch.setattr(jt, "neutral_band", lambda: 0.5)
    monkeypatch.setattr(jt, "_get_actual_return",
                        lambda u, r: 3.5)   # 不触网

    done = jt.verify_pending(uid)
    assert len(done) == 3, f"只补验了 {len(done)} 条，应扫全部月份文件"
    assert all(r["verdict"] == "correct" for r in done)
    assert all(r["neutral_band"] == 0.5 for r in done)


# ============================================================
# scorecard 的诚实字段
# ============================================================

def _seed_scorecard(uid: str):
    """4 条有方向 + 2 条 neutral + 1 条 blocked，全部已验。

    构造：3 条方向命中 / 1 条方向错误 → directional_accuracy = 75%。
    基线要在**全部 7 条已验证窗口**上算（每个窗口你都只能选多或空）：
    actual_return 有 4 条 > 0.5 → baseline_always_bullish = 4/7 = 57.1%。
    这个 57.1% 正是「脱离基线看 75% 无法解释」的实证。
    """
    recs = [
        _rec("2026-09-01T09:00:00", "bullish", "2026-09-10", verified=True,
             verdict="correct", actual_return=4.0, neutral_band=0.5),
        _rec("2026-09-02T09:00:00", "bullish", "2026-09-10", verified=True,
             verdict="correct", actual_return=4.0, neutral_band=0.5),
        _rec("2026-09-03T09:00:00", "bearish", "2026-09-10", verified=True,
             verdict="correct", actual_return=-3.0, neutral_band=0.5),
        _rec("2026-09-04T09:00:00", "bearish", "2026-09-10", verified=True,
             verdict="wrong", actual_return=4.0, neutral_band=0.5),
        _rec("2026-09-05T09:00:00", "neutral", "2026-09-10", verified=True,
             verdict="no_view", actual_return=4.0, neutral_band=0.5),
        _rec("2026-09-06T09:00:00", "neutral", "2026-09-10", verified=True,
             verdict="no_view", actual_return=0.1, neutral_band=0.5),
        _rec("2026-09-07T09:00:00", "blocked", "2026-09-10", verified=True,
             verdict="no_view", actual_return=-9.0, neutral_band=0.5),
    ]
    _write_records(uid, datetime.now().strftime("%Y-%m"), recs)


def test_scorecard_directional_accuracy_excludes_no_view(monkeypatch):
    uid = "qa_scorecard"
    _seed_scorecard(uid)
    monkeypatch.setattr(jt, "neutral_band", lambda: 0.5)
    monkeypatch.setattr(jt, "verify_pending", lambda u: [])

    card = jt.scorecard(uid, months=3)

    assert card["directional_total"] == 4, "neutral/blocked 被算进了方向分母"
    assert card["directional_correct"] == 3
    assert card["directional_accuracy"] == 75.0
    assert card["no_view"] == 3
    assert card["no_view_rate"] == pytest.approx(42.9, abs=0.1)

    # 故障注入：旧口径把 7 条全算进分母 → 3/7 = 42.9%，与新值不同
    assert card["directional_accuracy"] != pytest.approx(3 / 7 * 100, abs=0.1)


def test_scorecard_exposes_baseline_and_sample_adequacy(monkeypatch):
    """没有基线对照，命中率无法解释；样本不足时必须能看出来。"""
    uid = "qa_scorecard2"
    _seed_scorecard(uid)
    monkeypatch.setattr(jt, "neutral_band", lambda: 0.5)
    monkeypatch.setattr(jt, "verify_pending", lambda u: [])

    card = jt.scorecard(uid, months=3)

    # 基线在全部 7 条已验证窗口上算：4 条上涨 → 57.1%
    assert card["baseline_always_bullish"] == pytest.approx(57.1, abs=0.1)
    assert card["baseline_always_bearish"] == pytest.approx(42.9, abs=0.1)
    # 命中率 75% 高于基线 57.1%，但 4 条样本下这个差距毫无统计意义
    assert card["directional_accuracy"] > card["baseline_always_bullish"]
    assert card["accuracy_ci95"][1] - card["accuracy_ci95"][0] > 40, \
        "小样本的置信区间应该宽到足以说明「还不能判定」"
    assert card["sample_adequate"] is False, "4 条样本不该被判为充足"
    assert card["required_samples"] == jt.MIN_DIRECTIONAL_FOR_SIGNIFICANCE == 194
    lo, hi = card["accuracy_ci95"]
    assert lo < card["directional_accuracy"] < hi
    assert card["metric_revision"] == "2026-09-13"


def test_module_accuracy_returns_none_not_zero_when_no_directional_sample():
    """模块从未表达方向时返回 None，不能返回 0 —— 0% 和「没样本」是两回事。"""
    uid = "qa_module_none"
    recs = [
        _rec("2026-09-01T09:00:00", "bullish", "2026-09-10", verified=True,
             verdict="correct", actual_return=4.0, neutral_band=0.5,
             module_snapshots={"risk": {"direction": "neutral", "confidence": 50}}),
    ]
    _write_records(uid, datetime.now().strftime("%Y-%m"), recs)
    stats = _module_accuracy_without_network(uid)
    assert stats["risk"]["total"] == 0
    assert stats["risk"]["accuracy"] is None
    assert stats["risk"]["no_view"] == 1


def _module_accuracy_without_network(uid: str) -> dict:
    """直接跑 scorecard 的模块统计路径，绕开网络（neutral_band 已被显式传入）。"""
    import types
    orig = jt.neutral_band
    jt.neutral_band = lambda: 0.5
    try:
        card = jt.scorecard(uid, months=3)
    finally:
        jt.neutral_band = orig
    return card["module_accuracy"]


# ============================================================
# D3 一致分单位
# ============================================================

def _ctx(**kw) -> DecisionContext:
    ctx = DecisionContext(user_id="qa", question="q", trigger="test")
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_confidence_score_uses_confidence_not_mixed_scale_score():
    """线上实测的三个真实模块（broker_research 0~1 / market_factors 0~100 /
    factor_data -100~100）。旧实现算出 10.2（→ confidence 1020），新实现 0.6。"""
    ctx = _ctx(modules_results={
        "broker_research": {"available": True, "direction": "bullish",
                            "score": 0.6, "confidence": 55},
        "market_factors": {"available": True, "direction": "bullish",
                           "score": 50, "confidence": 60},
        "factor_data": {"available": True, "direction": "neutral",
                        "score": -20, "confidence": 65},
    })
    step_confidence_gate(ctx)

    assert ctx.confidence_score == pytest.approx(0.60, abs=1e-9)
    assert 0.0 <= ctx.confidence_score <= 1.0

    # 故障注入：旧口径 (0.6 + 50 + (-20)) / 3 = 10.2，与本值不同
    assert ctx.confidence_score != pytest.approx(10.2, abs=0.01)
    # 下游 output 阶段做 int(confidence_score*100) —— 旧值会产出 1020
    assert int(ctx.confidence_score * 100) == 60


def test_modules_without_confidence_are_skipped_not_defaulted_to_half():
    """旧实现 `result.get("score", 0.5)` 让不写 score 的模块静默贡献 0.5，
    一致分 p50 因此恒在 0.6 附近、恰好压在阈值 0.7 之下。
    正确行为：完全没有可用置信度的模块不参与，一致分归零并走仲裁。"""
    ctx = _ctx(modules_results={
        "signal": {"available": True, "direction": "bullish"},
        "risk": {"available": True, "direction": "neutral"},
    })
    step_confidence_gate(ctx)

    assert ctx.confidence_score == 0.0, "无置信度模块不该贡献 0.5"
    assert ctx.confidence_score != pytest.approx(0.5)
    assert ctx.gate_decision == "llm_arbitration"


def test_unavailable_modules_do_not_participate():
    ctx = _ctx(modules_results={
        "signal": {"available": True, "direction": "bullish", "confidence": 80},
        "risk": {"available": False, "error": "boom", "confidence": 90},
    })
    step_confidence_gate(ctx)
    assert ctx.confidence_score == pytest.approx(0.80, abs=1e-9)


def test_out_of_range_confidence_fails_loudly():
    """模块置信度越界 → 抛错。静默截断会让「模块换了口径」永远查不出来。"""
    ctx = _ctx(modules_results={
        "weird": {"available": True, "direction": "bullish", "confidence": 1041},
    })
    with pytest.raises(ValueError) as ei:
        step_confidence_gate(ctx)
    assert "1041" in str(ei.value)


def test_gate_decision_respects_threshold_after_normalization():
    """一致分 0.72 ≥ 0.7 且方向一致 → 直出；0.62 → 仲裁。"""
    hi = _ctx(modules_results={
        "a": {"available": True, "direction": "bullish", "confidence": 72},
    })
    step_confidence_gate(hi)
    assert hi.confidence_score == pytest.approx(0.72)
    assert hi.gate_decision == "direct_output"

    lo = _ctx(modules_results={
        "a": {"available": True, "direction": "bullish", "confidence": 62},
    })
    step_confidence_gate(lo)
    assert lo.gate_decision == "llm_arbitration"


def test_gate_reason_exposes_module_coverage():
    """gate_reason 要能看出几个模块计入了一致分，便于事后核对。"""
    ctx = _ctx(modules_results={
        "a": {"available": True, "direction": "bullish", "confidence": 80},
        "b": {"available": False, "error": "x"},
    })
    step_confidence_gate(ctx)
    assert "1个模块计入一致分" in ctx.gate_reason
    assert "1个无可用置信度" in ctx.gate_reason


# ============================================================
# 校准闸门
# ============================================================

def test_min_records_thresholds_raised():
    """n=3~10 的置信区间宽到证明不了任何事，门槛必须提上来。"""
    assert jt.MIN_RECORDS_FOR_EMA == 30
    assert jt.MIN_MODULE_RECORDS_FOR_EMA == 30


def test_walk_forward_rejects_when_insufficient_samples(monkeypatch):
    monkeypatch.setattr(jt, "_verified_records", lambda u: [{"verified": True}] * 5)
    wf = jt._walk_forward_check("qa", dict(jt.DEFAULT_WEIGHTS))
    assert wf["passed"] is False
    assert "5" in wf["reason"]


def test_walk_forward_accepts_when_oos_not_worse_than_equal_weight(monkeypatch):
    """样本外可判方向样本充足且不劣于等权 → 通过。"""
    recs = []
    for i in range(40):
        recs.append({
            "verified": True,
            "recorded_at": f"2026-07-{i % 28 + 1:02d}T09:00:00",
            "actual_return": 5.0 if i % 2 else -5.0,
            "neutral_band": 0.5,
            "module_snapshots": {
                "signal": {"direction": "bullish" if i % 2 else "bearish"},
            },
        })
    monkeypatch.setattr(jt, "_verified_records", lambda u: recs)
    wf = jt._walk_forward_check("qa", dict(jt.DEFAULT_WEIGHTS))
    assert wf["passed"] is True
    assert wf["oos_directional"] >= 10


def test_walk_forward_rejects_when_oos_worse_than_equal_weight(monkeypatch):
    """构造：训练段 A 模块很准，样本外段 A 模块反向；加权后应劣于等权 → 拒绝。

    这是 EMA 调权最典型的失败模式，必须被这道闸门拦住。
    """
    recs = []
    for i in range(30):        # 训练段：signal 永远对
        recs.append({
            "verified": True, "recorded_at": f"2026-01-{i % 28 + 1:02d}T09:00:00",
            "actual_return": 5.0, "neutral_band": 0.5,
            "module_snapshots": {
                "signal": {"direction": "bullish"},
                "stock_screen": {"direction": "bearish"},
            },
        })
    for i in range(20):        # 样本外：signal 永远错，stock_screen 永远对
        recs.append({
            "verified": True, "recorded_at": f"2026-06-{i % 28 + 1:02d}T09:00:00",
            "actual_return": 5.0, "neutral_band": 0.5,
            "module_snapshots": {
                "signal": {"direction": "bearish"},
                "stock_screen": {"direction": "bullish"},
            },
        })
    monkeypatch.setattr(jt, "_verified_records", lambda u: recs)

    skewed = {k: 0.02 for k in jt.DEFAULT_WEIGHTS}
    skewed["signal"] = 0.80                       # 训练集上捧出来的"明星模块"
    skewed["stock_screen"] = 0.02
    wf = jt._walk_forward_check("qa", skewed)

    assert wf["passed"] is False, "样本外劣于等权却通过了闸门"
    assert "等权" in wf["reason"]
    assert wf["oos_weighted_accuracy"] < wf["oos_equal_weight_accuracy"]


def test_weight_table_and_actual_modules_are_disjoint():
    """锁定一个已知结构性事实：DEFAULT_WEIGHTS 的键与实际出场模块大面积不重合。

    DEFAULT_WEIGHTS 8 个键里 monte_carlo / rl_position / portfolio_optimizer /
    genetic_factor / alt_data 在线上判断记录的 module_snapshots 里**从未出现过**；
    而实际出场的 broker_research / factor_data / geopolitical / market_factors /
    news_data / sector_rotation 在权重表里**没有键**。
    这不是本次修的目标，但必须钉住 —— 它意味着即便将来把权重接进门控，
    也只覆盖 10 个模块里的 2~3 个。
    """
    actual = {"broker_research", "factor_data", "geopolitical", "market_factors",
              "news_data", "sector_rotation", "signal", "signal_scout",
              "stock_screen", "risk"}
    table = set(jt.DEFAULT_WEIGHTS)
    assert len(table & actual) == 3, f"交集变了: {sorted(table & actual)}"
    assert len(table - actual) == 5


def test_weighted_vote_does_not_silently_ignore_unlisted_modules():
    """权重表查不到的模块按表中非零权重均值计入，不能被当成 0 静默忽略。"""
    recs = [{
        "actual_return": 5.0, "neutral_band": 0.5,
        "module_snapshots": {"broker_research": {"direction": "bullish"}},
    }]
    hit, total = jt._weighted_vote_hit(recs, dict(jt.DEFAULT_WEIGHTS))
    assert total == 1, "权重表未覆盖的模块被静默忽略了"
    assert hit == 1
