"""
周期权重回归测试：`active_weights` 必须真正参与评分
====================================================

背景（2026-09-09）：`get_stock_recommendations(period=...)` 会根据
「短线 / 中线 / 长线」从 `PERIOD_WEIGHTS` 取一套权重（还会叠加用户风险
偏好调整），算成 `active_weights`，并塞进返回体的 `weights` 字段。
但 `_calc_composite_score` 用的是模块级全局 `RECOMMEND_WEIGHTS`，
`active_weights` **从没被传进去** —— 于是：

  - 用户选短线还是长线，排序**完全一样**；
  - 返回体里的 `weights` 字段写着 medium 的权重，而分数是按
    `RECOMMEND_WEIGHTS` 算的，两者互相矛盾（线上 9-09 快照：
    `weights` 显示 `valuation 25%`，实际按 `28%` 算）。

修复：`_calc_composite_score(stock, weights=None)` 新增可选权重参数，
由 `get_stock_recommendations` 把 `active_weights` 传进来。

本文件锁定六件事：
 1. **三套权重表写死**（值 + 和为 1.0）：改权重必须改测试，不能悄悄改；
 2. **`_resolve_weights` 的兜底**：None/空 → 默认表；缺维 → 取默认值；
    和 <= 0 → 默认表；
 3. **权重归一化**：风险偏好调整里的 `max(0.05, ...)` 会让权重和超过 1
    （long + 保守型 实测 1.11），不归一化 `total_score` 会超过 100；
 4. **方向性**：短线偏技术/资金、长线偏估值/盈利/低回撤 —— 两只画像相反
    的股票在三档周期下排序必须翻转，**且长线绝不能把高换手标的排在
    低换手价值股前面**（team-lead 点名的「反向结果」）；
 5. **真实快照数字**：920982 锦波生物在 9-09 线上 Top10 快照里的
    分数（53.3 / 61.9 / 62.6）与排名（9 / 6 / 1）写死；
 6. **端到端**：`active_weights` 必须真的传到 `_calc_composite_score`
    —— 只改返回体、不传参的话，三档周期排序会变得一样，用例必挂。

设计原则（与 `test_bse_code_mapping.py` / `test_recommend_price_data_caveat.py`
一致）：不复制实现里的分支表，直接用真实的 `PERIOD_WEIGHTS` /
`_resolve_weights` / `_calc_composite_score` / `get_stock_recommendations`，
只把外部数据源与 LLM 换成假实现。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

import pytest

from services import recommend_engine as re_mod


# ============================================================
# 1. 三套周期权重表（写死，含 label / icon）
# ============================================================
EXPECTED_PERIOD_WEIGHTS: Dict[str, Dict[str, object]] = {
    "short": {
        "valuation": 0.05, "earnings": 0.10, "technical": 0.35,
        "capital": 0.30, "risk": 0.10, "theme": 0.10,
        "label": "短线（1-2周）", "icon": "⚡",
    },
    "medium": {
        "valuation": 0.25, "earnings": 0.28, "technical": 0.15,
        "capital": 0.17, "risk": 0.05, "theme": 0.10,
        "label": "中线（1-3月）", "icon": "📊",
    },
    "long": {
        "valuation": 0.33, "earnings": 0.28, "technical": 0.05,
        "capital": 0.09, "risk": 0.15, "theme": 0.10,
        "label": "长线（6月+）", "icon": "🏦",
    },
}

DIMS = ("valuation", "earnings", "technical", "capital", "risk", "theme")


@pytest.mark.parametrize("period", ["short", "medium", "long"])
def test_period_weight_table_is_locked(period: str) -> None:
    """三套权重表的每一个数字都写死；改权重必须连测试一起改。"""
    assert re_mod.PERIOD_WEIGHTS[period] == EXPECTED_PERIOD_WEIGHTS[period]


@pytest.mark.parametrize("period", ["short", "medium", "long"])
def test_period_weight_table_sums_to_one(period: str) -> None:
    """6 个维度权重和必须是 1.0，否则 total_score 会系统性偏移。"""
    table = re_mod.PERIOD_WEIGHTS[period]
    assert sum(table[d] for d in DIMS) == pytest.approx(1.0, abs=1e-9)


def test_period_tables_are_not_all_the_same() -> None:
    """三套表必须真的不同 —— 否则「周期选择」这个功能是空的。"""
    pairs = [
        (re_mod.PERIOD_WEIGHTS["short"], re_mod.PERIOD_WEIGHTS["medium"]),
        (re_mod.PERIOD_WEIGHTS["medium"], re_mod.PERIOD_WEIGHTS["long"]),
        (re_mod.PERIOD_WEIGHTS["short"], re_mod.PERIOD_WEIGHTS["long"]),
    ]
    for a, b in pairs:
        assert any(a[d] != b[d] for d in DIMS)


# ============================================================
# 2. `_resolve_weights` 兜底
# ============================================================
def test_resolve_weights_none_falls_back_to_module_default() -> None:
    assert re_mod._resolve_weights(None) == re_mod.RECOMMEND_WEIGHTS
    assert re_mod._resolve_weights({}) == re_mod.RECOMMEND_WEIGHTS


def test_resolve_weights_fills_missing_dims_from_default() -> None:
    """只传一部分维度时，缺的那几个用默认表补齐，不许按 0 处理。"""
    partial = {"technical": 0.40, "capital": 0.40}
    got = re_mod._resolve_weights(partial)
    assert set(got) == set(DIMS)
    # 未指定的 4 个维度按默认权重补齐后整体归一化
    total = 0.80 + sum(re_mod.RECOMMEND_WEIGHTS[d] for d in DIMS if d not in partial)
    assert got["technical"] == pytest.approx(0.40 / total)
    assert got["valuation"] == pytest.approx(
        re_mod.RECOMMEND_WEIGHTS["valuation"] / total)


def test_resolve_weights_ignores_non_numeric_and_unknown_keys() -> None:
    """`label` / `icon` 这类非维度键要忽略；维度值非数字时退回默认值。"""
    got = re_mod._resolve_weights({
        "valuation": "0.5",          # 字符串不算数字 → 用默认值
        "earnings": None,            # None → 用默认值
        "label": "短线（1-2周）",     # 非维度键 → 忽略
        "icon": "⚡",
    })
    assert set(got) == set(DIMS)
    assert got["valuation"] == pytest.approx(re_mod.RECOMMEND_WEIGHTS["valuation"] / 1.0)
    assert got["earnings"] == pytest.approx(re_mod.RECOMMEND_WEIGHTS["earnings"] / 1.0)


def test_resolve_weights_non_positive_sum_falls_back() -> None:
    """权重和 <= 0（全 0 / 负数）时退回默认表，不许除零或产生负权重。"""
    zeros = {d: 0.0 for d in DIMS}
    assert re_mod._resolve_weights(zeros) == re_mod.RECOMMEND_WEIGHTS
    negs = {d: -1.0 for d in DIMS}
    assert re_mod._resolve_weights(negs) == re_mod.RECOMMEND_WEIGHTS


def test_resolve_weights_does_not_mutate_input() -> None:
    src = {d: 2.0 for d in DIMS}
    re_mod._resolve_weights(src)
    assert src == {d: 2.0 for d in DIMS}


# ============================================================
# 3. 归一化：风险偏好调整后权重和会破 1，必须归一化
# ============================================================
def _risk_adjusted(period: str, risk_type: str) -> Dict[str, float]:
    """复刻 `get_stock_recommendations` 里的风险偏好调整，得到未归一化的权重。

    直接抄实现里的加减（含 `max(0.05, ...)` 触底），用于验证「和调整会 > 1」
    这个前提本身还成立 —— 若哪天调整改成了保和形式，这条用例会先挂，
    提示归一化不再是必需项（那时再看要不要简化 `_resolve_weights`）。
    """
    w = dict(re_mod.PERIOD_WEIGHTS[period])
    if risk_type == "growth":
        w["technical"] = w["technical"] + 0.05
        w["capital"] = w["capital"] + 0.05
        w["risk"] = max(0.05, w["risk"] - 0.10)
    elif risk_type == "conservative":
        w["valuation"] = w["valuation"] + 0.10
        w["risk"] = w["risk"] + 0.05
        w["technical"] = max(0.05, w["technical"] - 0.10)
        w["capital"] = max(0.05, w["capital"] - 0.05)
    return {d: w[d] for d in DIMS}


def test_risk_adjusted_long_conservative_exceeds_one_before_normalizing() -> None:
    """前提验证：long + 保守型 的权重和 = 1.11（technical/capital 触底不再减）。"""
    raw = _risk_adjusted("long", "conservative")
    assert sum(raw.values()) == pytest.approx(1.11, abs=1e-9)
    assert raw["technical"] == 0.05   # max(0.05, 0.05-0.10) = 0.05，没减成负的
    assert raw["capital"] == 0.05     # max(0.05, 0.09-0.05) = 0.05，只减了 0.04


def test_risk_adjusted_weights_are_normalized() -> None:
    raw = _risk_adjusted("long", "conservative")
    got = re_mod._resolve_weights(raw)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-9)
    assert got["technical"] == pytest.approx(0.05 / 1.11)


def test_score_stays_within_100_with_risk_adjusted_weights(monkeypatch) -> None:
    """不归一化的话，6 维全 100 分会算出 111 分 —— 这里钉死上限。"""
    raw = _risk_adjusted("long", "conservative")           # 和 = 1.11
    for dim in DIMS:
        monkeypatch.setattr(re_mod, f"_score_{dim}", lambda s, _d=dim: 100)
    got = re_mod._calc_composite_score({"code": "600000", "name": "满分股"}, raw)
    assert got["total_score"] == pytest.approx(100.0, abs=0.05)
    assert got["total_score"] <= 100.0


# ============================================================
# 4. 方向性：短线/长线排序必须翻转，且不出现反向结果
# ============================================================
# 两只画像相反的股票：
#   HOT   = 高换手短线票（技术/资金极高，估值/盈利/低回撤很差）
#   VALUE = 低换手价值股（估值/盈利/低回撤极好，技术/资金很差）
PROFILES: Dict[str, Dict[str, int]] = {
    "600HOT": {"valuation": 20, "earnings": 20, "technical": 90,
               "capital": 95, "risk": 10, "theme": 50},
    "600VAL": {"valuation": 90, "earnings": 85, "technical": 30,
               "capital": 20, "risk": 85, "theme": 50},
}


def _patch_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 6 个打分函数换成按 code 取固定画像的假实现（不碰网络）。"""
    def _make(dim: str) -> Callable[[dict], int]:
        def _score(stock: dict) -> int:
            return PROFILES[stock["code"]][dim]
        return _score

    for dim in DIMS:
        monkeypatch.setattr(re_mod, f"_score_{dim}", _make(dim))


def _score_all(period: str) -> Dict[str, float]:
    table = re_mod.PERIOD_WEIGHTS[period]
    return {
        code: sum(profile[d] * table[d] for d in DIMS)
        for code, profile in PROFILES.items()
    }


@pytest.mark.parametrize("period,winner", [
    ("short", "600HOT"),
    ("long", "600VAL"),
])
def test_period_selects_the_matching_profile(
    monkeypatch: pytest.MonkeyPatch, period: str, winner: str
) -> None:
    """短线选高换手票、长线选价值股 —— 这是「周期真的生效」的直接证据。"""
    _patch_scores(monkeypatch)
    scored = {
        code: re_mod._calc_composite_score(
            {"code": code, "name": code},
            re_mod.PERIOD_WEIGHTS[period],
        )["total_score"]
        for code in PROFILES
    }
    assert max(scored, key=scored.get) == winner


def test_ranking_flips_between_short_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一批标的在 short / long 下的排序必须有可观测差异（不能三档一样）。"""
    _patch_scores(monkeypatch)
    short = _rank(monkeypatch, "short")
    long_ = _rank(monkeypatch, "long")
    assert short == ["600HOT", "600VAL"]
    assert long_ == ["600VAL", "600HOT"]
    assert short != long_


def _rank(monkeypatch: pytest.MonkeyPatch, period: str) -> list:
    _patch_scores(monkeypatch)
    scored = [
        (re_mod._calc_composite_score({"code": code, "name": code},
                                      re_mod.PERIOD_WEIGHTS[period])["total_score"],
         code)
        for code in PROFILES
    ]
    scored.sort(reverse=True)
    return [code for _, code in scored]


def test_long_never_puts_high_turnover_above_value_stock() -> None:
    """team-lead 点名的「反向结果」：长线不该给出高换手标的。

    用 `_score_all` 直接算（不依赖打桩），当作第二重独立校验：
    长线口径下 600VAL 必须领先 600HOT，且差距要够大（不是浮点噪声）。
    """
    short_scores = _score_all("short")
    long_scores = _score_all("long")
    assert short_scores["600HOT"] > short_scores["600VAL"]
    assert long_scores["600VAL"] > long_scores["600HOT"]
    assert long_scores["600VAL"] - long_scores["600HOT"] > 10.0


# ============================================================
# 5. 真实快照：920982 锦波生物在三档周期下的分数与排名
# ============================================================
# 取自线上 9-09 01:05 的 hot Top10 快照
# （data/_cache/recommend_rec__hot_10_medium.json，period=medium）。
# 注意：快照里的 total_score 是按模块级 RECOMMEND_WEIGHTS 算的（修复前的 bug），
# 所以 920982 显示 60.6；按 medium 权重应为 61.9。
REAL_TOP10: Dict[str, Dict[str, int]] = {
    "300502": {"valuation": 50, "earnings": 95, "technical": 65,
               "capital": 80, "risk": 20, "theme": 50},
    "920982": {"valuation": 65, "earnings": 80, "technical": 50,
               "capital": 50, "risk": 45, "theme": 50},
    "300866": {"valuation": 50, "earnings": 80, "technical": 55,
               "capital": 80, "risk": 25, "theme": 50},
    "002916": {"valuation": 50, "earnings": 85, "technical": 65,
               "capital": 65, "risk": 20, "theme": 50},
    "300394": {"valuation": 50, "earnings": 95, "technical": 35,
               "capital": 80, "risk": 20, "theme": 50},
    "688603": {"valuation": 50, "earnings": 80, "technical": 55,
               "capital": 80, "risk": 20, "theme": 50},
    "300054": {"valuation": 50, "earnings": 80, "technical": 45,
               "capital": 80, "risk": 25, "theme": 50},
    "605305": {"valuation": 50, "earnings": 70, "technical": 55,
               "capital": 80, "risk": 25, "theme": 50},
    "300308": {"valuation": 50, "earnings": 95, "technical": 65,
               "capital": 25, "risk": 20, "theme": 50},
    "300661": {"valuation": 50, "earnings": 80, "technical": 35,
               "capital": 80, "risk": 20, "theme": 50},
}


def _weighted(profile: Dict[str, int], weights: Dict[str, float]) -> float:
    return round(sum(profile[d] * weights[d] for d in DIMS), 1)


@pytest.mark.parametrize("period,expected", [
    ("short", 53.2),
    ("medium", 61.9),
    ("long", 62.6),
])
def test_920982_score_per_period(period: str, expected: float) -> None:
    """920982 锦波生物在三档周期下的分数写死（它是本项目的探针标的）。

    它估值 65 / 风险 45 全池最好，技术 50 / 资金 50 只算中游，
    所以「周期越长分越高」—— 这正是修复后用户能感知到的差异。
    """
    table = {d: re_mod.PERIOD_WEIGHTS[period][d] for d in DIMS}
    assert _weighted(REAL_TOP10["920982"], table) == pytest.approx(expected, abs=0.05)


def test_920982_score_rises_with_horizon() -> None:
    scores = [
        _weighted(REAL_TOP10["920982"],
                  {d: re_mod.PERIOD_WEIGHTS[p][d] for d in DIMS})
        for p in ("short", "medium", "long")
    ]
    assert scores[0] < scores[1] < scores[2]


@pytest.mark.parametrize("period,expected_rank,expected_top1", [
    ("short", 9, "300502"),
    ("medium", 6, "300502"),
    ("long", 1, "920982"),
])
def test_920982_rank_in_real_top10(
    period: str, expected_rank: int, expected_top1: str
) -> None:
    """同一批真实标的，920982 的排名随周期从 9 → 6 → 1，榜首也换了人。"""
    table = {d: re_mod.PERIOD_WEIGHTS[period][d] for d in DIMS}
    ranked = sorted(
        REAL_TOP10.items(),
        key=lambda kv: _weighted(kv[1], table),
        reverse=True,
    )
    codes = [code for code, _ in ranked]
    assert codes[0] == expected_top1
    assert codes.index("920982") + 1 == expected_rank


def test_snapshot_total_score_was_computed_with_module_weights() -> None:
    """锁住「修复前」的口径，防止有人把 60.6 当成本该如此的数字。

    线上快照 60.6 是按 `RECOMMEND_WEIGHTS` 算的；同画像按 medium 权重是
    61.9。两者并存过，正是这次 bug 的表现 —— 留个用例说明差异来源。
    """
    profile = REAL_TOP10["920982"]
    assert _weighted(profile, re_mod.RECOMMEND_WEIGHTS) == pytest.approx(60.6, abs=0.05)
    medium = {d: re_mod.PERIOD_WEIGHTS["medium"][d] for d in DIMS}
    assert _weighted(profile, medium) == pytest.approx(61.9, abs=0.05)
    assert _weighted(profile, medium) != _weighted(profile, re_mod.RECOMMEND_WEIGHTS)


# ============================================================
# 6. evidence 里的权重必须跟着周期走
# ============================================================
@pytest.mark.parametrize("period,technical_weight,valuation_weight", [
    ("short", "35%", "5%"),
    ("medium", "15%", "25%"),
    ("long", "5%", "33%"),
])
def test_evidence_weight_reflects_period(
    monkeypatch: pytest.MonkeyPatch,
    period: str,
    technical_weight: str,
    valuation_weight: str,
) -> None:
    """evidence 显示的权重必须和算分用的权重一致，否则又是「显示与计算两张皮」。"""
    _patch_scores(monkeypatch)
    got = re_mod._calc_composite_score(
        {"code": "600HOT", "name": "高换手"}, re_mod.PERIOD_WEIGHTS[period]
    )
    assert got["evidence"]["technical"]["weight"] == technical_weight
    assert got["evidence"]["valuation"]["weight"] == valuation_weight


def test_scored_item_carries_the_weights_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """每条评分结果自带「按哪套权重算的」，方便事后核对（防重演这次的 bug）。"""
    _patch_scores(monkeypatch)
    got = re_mod._calc_composite_score(
        {"code": "600HOT", "name": "高换手"}, re_mod.PERIOD_WEIGHTS["short"]
    )
    assert got["weights"]["technical"] == pytest.approx(0.35)


def test_default_call_still_uses_module_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    """老调用方（不传 weights）行为不变 —— A3 那批用例的 60.6 不受影响。"""
    _patch_scores(monkeypatch)
    got = re_mod._calc_composite_score({"code": "600HOT", "name": "高换手"})
    assert got["weights"] == pytest.approx(re_mod.RECOMMEND_WEIGHTS)
    assert got["evidence"]["technical"]["weight"] == "15%"   # RECOMMEND_WEIGHTS


# ============================================================
# 7. 端到端：`active_weights` 必须真的传到评分函数
# ============================================================
@pytest.mark.parametrize("period,expected_order", [
    ("short", ["600HOT", "600VAL"]),
    ("long", ["600VAL", "600HOT"]),
])
def test_end_to_end_period_changes_ranking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, period: str, expected_order: list
) -> None:
    """最关键的一条：从 `get_stock_recommendations` 入口验证周期真的生效。

    变异检测：如果有人把调用改回 `_calc_composite_score(stock)`（不传权重），
    三档周期会算出同一个排序，这条用例立刻挂 —— 只改返回体 `weights`
    字段、不改计算，骗不过它。
    """
    _patch_scores(monkeypatch)
    monkeypatch.setattr(
        re_mod, "_get_candidate_pool",
        lambda pool: [{"code": c, "name": c} for c in PROFILES],
    )
    monkeypatch.setattr(re_mod, "_generate_reasons", lambda items: None)
    # 文件缓存写到临时目录，绝不碰真实 DATA_DIR
    monkeypatch.setattr(re_mod, "DATA_DIR", tmp_path)

    result = re_mod.get_stock_recommendations(user_id="", top_n=10, period=period)

    assert [r["code"] for r in result["recommendations"]] == expected_order
    # 返回体 advertised 的权重 == 真正参与计算的权重
    advertised = {d: re_mod.PERIOD_WEIGHTS[period][d] for d in DIMS}
    assert result["weights"] == pytest.approx(advertised)
    for item in result["recommendations"]:
        assert item["weights"] == pytest.approx(advertised)


def test_end_to_end_three_periods_give_two_distinct_rankings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """三档周期至少产生两种不同的排序 —— 「三档一样」就是没接上。"""
    _patch_scores(monkeypatch)
    monkeypatch.setattr(
        re_mod, "_get_candidate_pool",
        lambda pool: [{"code": c, "name": c} for c in PROFILES],
    )
    monkeypatch.setattr(re_mod, "_generate_reasons", lambda items: None)
    monkeypatch.setattr(re_mod, "DATA_DIR", tmp_path)

    orders = []
    for period in ("short", "medium", "long"):
        result = re_mod.get_stock_recommendations(user_id="", top_n=10, period=period)
        orders.append(tuple(r["code"] for r in result["recommendations"]))
    assert len(set(orders)) >= 2
