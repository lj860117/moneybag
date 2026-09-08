"""
A3 回归测试：价格数据缺失「保留在推荐池 + 显式标注」
====================================================

背景（2026-09-09 线上事故）：北交所 920xxx（920826 盖世食品 / 920982 锦波生物）
在「Tushare → AKShare → Baostock」三级降级链上取不到日线，但推荐引擎**不报错**：

    `_score_technical`  → `if df is None or len(df) < 30: return 50`（静默）
    `_score_risk`       → `score = 60` 默认值一路带到底（静默）

于是 920982 以 `technical=50 / risk=45`、`total_score=60.6`、`rating=买入`
出现在 9-09 01:05 的 hot Top10，08:30 直接推给用户。**用户看到的分数字面完整，
其实技术面/风险面是凭空编的中性分。**

口径（team-lead 2026-09-09 拍板）：**保留在推荐池 + 显式标注数据不足**，
不静默改变推荐池构成。

实现范式照抄本文件同模块已有的 `_north_fallback_skipped`（`_score_capital`）：
注释原话是「让『没有这个兜底』可被观测到，而不是伪装成中性分」。

本文件锁定四件事：
  1. 取不到 K 线时**必须打标记**（技术面 <30 行 / 风险面 <20 行 / 无代码）；
  2. 标记必须变成用户可见的东西：`evidence[dim].available=False` +
     `display="数据不足"` + `data_completeness` + `reason` 末尾的 ⚠️ 提示；
  3. **LLM 路径也必须带提示**（原 `_generate_reasons` 在 LLM 成功时直接
     `return`，会把提示吞掉 —— 这是最容易被漏掉的一条）；
  4. `total_score` 口径：缺失维度**照旧贡献中性分，不按可用维度归一化**。
     归一化会让「数据越缺 → 剩余维度权重被放大 → 分数被抬高」，等价于
     用缺失数据奖励这只股票（见 `test_..._would_inflate_...`）。

第二轮（QA 严过关自设计变异 X1-X7 后补的，2026-09-09）：
  5. **多条目逐条标注**：`_append_data_caveat` 不许中途 break，Top10 里
     每一条缺数据的都必须带 ⚠️（X7 存活项：加 break 时旧用例全绿）。
  6. **阈值钉在精确边界**：technical 用 30 行、risk 用 19/20 行，
     防 off-by-one 与阈值放宽（X2 / X5 存活项）。
  7. **幂等**：重复调用不许套娃成「…（⚠️ …）（⚠️ …）」。
  8. **异常路径也打标记**：两个数据源都抛异常（不是返回 None）时，
     `_score_technical` 外层 except 不能黑洞（本轮自查挖出来的洞）。

第三轮（QA 第二轮 Z 系列，2026-09-09）：
  9. **`_score_risk` 的 except 也要覆盖**（Z5 / Z10 存活项）：波动率计算
     抛异常时必须标记，且必须标在 **risk** 上（标串到 technical 会让风险面
     变回黑洞）。与第 8 条的 technical 版成对。
 10. **幂等守卫比对完整 caveat 而不是只看 ⚠️**（Z2）：否则一条本就带别的
     ⚠️ 提示的理由会被误判成"已标注"从而漏标。
 11. **地缘高危门槛钉住**（Z11）：`severity >= 4` 时 60-30=30，
     且不管地缘怎么压，标记都必须在。

第四轮（QA 第三轮 W 系列，2026-09-09）：
 12. **维度顺序取显式表序，不依赖打标先后、不能用 set**（W4 存活项）。
     只有两个维度时 `list({'risk','technical'})` 碰巧等于插入序，所以
     set 化后 40 条用例全绿；三个维度以上就乱了。现在 `_price_missing_dims`
     按 `_DIM_LABEL_CN` 表序输出，本文件用「乱序打标 → 结果仍一致」
     钉住机制本身，而不是钉住某一次的输出。
 13. **表外维度不许被截断**（V8 / V8b，最后一个存活项）：现实风险趋近于零
     （6 个维度全在表里，`extra` 生产上恒为空），但成本只有一条用例，
     顺手关掉 —— 「以后再说」的尾巴基本不会再被捡起来。

设计原则：不复制实现里的分支表，直接调用真实的 `_score_technical` /
`_score_risk` / `_calc_composite_score` / `_generate_reasons`，只把外部数据
源（K 线 / 地缘 / LLM）换成假实现。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np
import pytest

from services import recommend_engine as re_mod
from services.geopolitical import get_geopolitical_risk_score as _real_geo  # noqa: F401  (确保模块可导入)


# ============================================================
# 假数据源
# ============================================================
class _Col:
    """最小化的 `df["收盘"]`：`len(df)` + `df["收盘"].values.astype(float)`。"""

    def __init__(self, values: Sequence[float]) -> None:
        self.values = np.asarray(values, dtype=float)


class _FakeDF:
    """最小化的日线 DataFrame 替身。"""

    def __init__(self, closes: Sequence[float]) -> None:
        self._col = _Col(closes)

    def __len__(self) -> int:
        return len(self._col.values)

    def __getitem__(self, key: str) -> _Col:
        assert key == "收盘", f"被测代码只应读「收盘」列，实际读了 {key!r}"
        return self._col


class _BoomDF:
    """`len()` 够、但一读列就抛异常 —— 用来把代码逼进「计算失败」的 except。"""

    def __init__(self, rows: int = 30) -> None:
        self._rows = rows

    def __len__(self) -> int:
        return self._rows

    def __getitem__(self, key: str) -> _Col:
        raise RuntimeError("上游返回了脏数据")


def _closes(n: int, start: float = 10.0) -> List[float]:
    """生成 n 个轻微波动的收盘价（保证不会触发除零 / NaN）。"""
    return [round(start + 0.1 * (i % 5), 4) for i in range(n)]


def _patch_daily_df(monkeypatch: pytest.MonkeyPatch, rows: int | None) -> None:
    """把 K 线数据源换成 `rows` 行的假数据（`rows=None` 表示取不到，返回 None）。"""
    df = None if rows is None else _FakeDF(_closes(rows))

    def _fake_get_daily_df(code: str, days: int = 90):
        return df

    # `_score_technical` / `_score_risk` 先试 stock_price_provider，
    # 失败再退回 get_stock_daily_hist —— 两条路都堵上，避免测试只覆盖其一
    try:
        from services import stock_price_provider as _spp
        monkeypatch.setattr(_spp, "get_daily_df", _fake_get_daily_df, raising=False)
    except Exception:  # pragma: no cover - 模块缺失时退回分支仍会被下面这行堵住
        pass
    try:
        from infra.data_source.market import stocks as _stocks
        monkeypatch.setattr(
            _stocks, "get_stock_daily_hist",
            lambda **kwargs: df,
            raising=False,
        )
    except Exception:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _no_geo_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """地缘风险是外部调用，测试里固定为 0，保证风险面分数确定性。"""
    from services import geopolitical as _geo

    monkeypatch.setattr(_geo, "get_geopolitical_risk_score",
                        lambda: {"max_severity": 0}, raising=False)


def _scorer(dim: str, value: int, mark: bool, reason: str = "测试用缺失"):
    """返回一个假的 `_score_<dim>`：可选顺带打缺失标记。"""

    def _fake(stock: dict) -> int:
        if mark:
            re_mod._mark_price_data_missing(stock, dim, reason)
        return value

    return _fake


# ============================================================
# 1. 打标记：技术面 / 风险面
# ============================================================
@pytest.mark.parametrize("rows", [0, 10, 29])
def test_technical_marks_when_kline_rows_below_30(
    monkeypatch: pytest.MonkeyPatch, rows: int
) -> None:
    """技术面不足 30 行 K 线（含取不到）→ 打标记，不再静默 return 50。"""
    _patch_daily_df(monkeypatch, rows)
    stock = {"code": "920982", "name": "锦波生物"}

    assert re_mod._score_technical(stock) == 50
    assert re_mod._price_missing_dims(stock) == ["technical"]
    assert "K线数据不足" in stock[re_mod._PRICE_MISSING_KEY]["technical"]
    assert f"{rows} 行" in stock[re_mod._PRICE_MISSING_KEY]["technical"]


@pytest.mark.parametrize("rows", [30, 31, 40, 60])
def test_technical_does_not_mark_when_kline_is_enough(
    monkeypatch: pytest.MonkeyPatch, rows: int
) -> None:
    """K 线足够 → 真实评分，绝不能误标「数据不足」（过度标注 = 假警）。

    行数刻意从 **30** 起步（正好压在阈值上）：把 `< 30` 改成 `<= 30`
    （QA 的 X2 变异）会让 30 行的股票被误标，这条用例必须能杀掉它。
    """
    _patch_daily_df(monkeypatch, rows)
    stock = {"code": f"T{rows}", "name": "盖世食品"}

    score = re_mod._score_technical(stock)

    assert isinstance(score, int)
    assert re_mod._price_missing_dims(stock) == []


def test_technical_marks_when_both_sources_raise(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个数据源都**抛异常**（不是返回 None）时也要打标记。

    这是 2026-09-09 补 QA 的 X 系列变异时自己挖出来的洞：外层 `except`
    原本静默 return 50，连 `len(df) < 30` 那个标记都走不到 —— 数据源要是
    改成抛异常而不是返回 None，技术面就变成彻底的黑洞，而且测试不会红。
    （`_score_risk` 的 except 从一开始就有标记，这次把两边对齐。）
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("上游超时")

    from services import stock_price_provider as _spp
    from infra.data_source.market import stocks as _stocks

    monkeypatch.setattr(_spp, "get_daily_df", _boom, raising=False)
    monkeypatch.setattr(_stocks, "get_stock_daily_hist", _boom, raising=False)

    stock = {"code": "920826", "name": "盖世食品"}

    assert re_mod._score_technical(stock) == 50
    assert re_mod._price_missing_dims(stock) == ["technical"]
    assert "技术面计算失败" in stock[re_mod._PRICE_MISSING_KEY]["technical"]


# --- QA 的 Z5 / Z10：risk 的 except 标记一直"写了没人测" ----------------
# 与上面 technical 的 `test_technical_marks_when_both_sources_raise` 成对。
# Z5（把这段标记删掉）和 Z10（标记改写成标 technical）在补这条之前都是
# 36 条全绿 —— 风险面在异常路径下会变回黑洞，而且测试不会红。
def test_risk_marks_when_volatility_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """波动率计算抛异常 → 风险面必须打标记（防 except 黑洞）。"""
    def _fake_get_daily_df(code: str, days: int = 60):
        return _BoomDF(rows=30)  # len 够 20，但读「收盘」会炸

    from services import stock_price_provider as _spp
    from infra.data_source.market import stocks as _stocks

    monkeypatch.setattr(_spp, "get_daily_df", _fake_get_daily_df, raising=False)
    monkeypatch.setattr(_stocks, "get_stock_daily_hist",
                        lambda **kwargs: _BoomDF(rows=30), raising=False)

    stock = {"code": "920982", "name": "锦波生物"}

    # 异常被吞掉后仍是默认 60，但**必须**留下标记
    assert re_mod._score_risk(stock) == 60
    # Z10：标记必须落在 risk 上，标到 technical 去就会漏掉风险面
    assert re_mod._price_missing_dims(stock) == ["risk"]
    assert "波动率计算失败" in stock[re_mod._PRICE_MISSING_KEY]["risk"]


def test_risk_marks_risk_not_technical_when_volatility_raises(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Z10 专用：异常路径的标记维度必须是 risk，不许串到 technical。"""
    from services import stock_price_provider as _spp
    from infra.data_source.market import stocks as _stocks

    monkeypatch.setattr(_spp, "get_daily_df",
                        lambda code, days=60: _BoomDF(rows=30), raising=False)
    monkeypatch.setattr(_stocks, "get_stock_daily_hist",
                        lambda **kwargs: _BoomDF(rows=30), raising=False)

    stock = {"code": "920826", "name": "盖世食品"}
    re_mod._score_risk(stock)

    assert re_mod._price_missing_dims(stock) == ["risk"]
    assert re_mod._price_data_caveat(stock) == "⚠️ 风险面行情数据不足，评分基于部分维度"


def test_technical_marks_when_code_missing() -> None:
    """没有股票代码 → 技术面无数据可评，必须标记（原代码静默 return 50）。"""
    stock: Dict[str, object] = {"name": "无名标的"}

    assert re_mod._score_technical(stock) == 50
    assert re_mod._price_missing_dims(stock) == ["technical"]
    assert stock[re_mod._PRICE_MISSING_KEY]["technical"] == "缺少股票代码"


@pytest.mark.parametrize("rows", [0, 10, 19])
def test_risk_marks_when_kline_rows_below_20(
    monkeypatch: pytest.MonkeyPatch, rows: int
) -> None:
    """风险面不足 20 行 → 打标记，不再静默停在默认 60。

    19 行这一档是 QA 的 X5 变异（把 `>= 20` 放宽成 `>= 15`）的杀手：
    放宽后 15~19 行的股票会漏标，这条必须红。
    """
    _patch_daily_df(monkeypatch, rows)
    stock = {"code": f"R{rows}", "name": "盖世食品"}

    assert re_mod._score_risk(stock) == 60
    assert re_mod._price_missing_dims(stock) == ["risk"]
    assert f"{rows} 行 < 20" in stock[re_mod._PRICE_MISSING_KEY]["risk"]


@pytest.mark.parametrize("rows", [20, 25, 60])
def test_risk_does_not_mark_when_kline_is_enough(
    monkeypatch: pytest.MonkeyPatch, rows: int
) -> None:
    """风险面数据足够（从阈值 20 起步）→ 不标注，避免过度标注。"""
    _patch_daily_df(monkeypatch, rows)
    stock = {"code": f"S{rows}", "name": "贵州茅台"}

    score = re_mod._score_risk(stock)

    assert score in (25, 40, 60, 80)
    assert re_mod._price_missing_dims(stock) == []


def test_risk_neutral_60_minus_geo_gives_45_as_seen_on_920982(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁住线上实测值：风险面静默默认 60，地缘加成 -15 → 45。

    2026-09-09 严过关在服务器上用新代码 + 线上网络实测 920982 得到
    `risk=45`（不是 60）。这里复现这个减法，防止以后有人按注释里的
    「默认 60」去跟线上的 45 对账然后对不上。
    关键：就算地缘把它从 60 压到 45，这个分数**仍然不是真实评分**，
    标记必须还在。
    """
    from services import geopolitical as _geo

    monkeypatch.setattr(_geo, "get_geopolitical_risk_score",
                        lambda: {"max_severity": 2})
    _patch_daily_df(monkeypatch, None)
    stock = {"code": "920982", "name": "锦波生物"}

    assert re_mod._score_risk(stock) == 45
    assert re_mod._price_missing_dims(stock) == ["risk"]


def test_risk_at_high_geo_severity_stays_marked(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Z11（QA 低优先级存活项）：极端地缘（severity>=4）门槛也钉住。

    60 - 30 = 30（不是 -15 那档的 45）；且不管地缘怎么压，
    这个分数**仍然不是真实评分**，标记必须还在。
    """
    from services import geopolitical as _geo

    monkeypatch.setattr(_geo, "get_geopolitical_risk_score",
                        lambda: {"max_severity": 4})
    _patch_daily_df(monkeypatch, None)
    stock = {"code": "920982", "name": "锦波生物"}

    assert re_mod._score_risk(stock) == 30
    assert re_mod._price_missing_dims(stock) == ["risk"]


def test_risk_marks_when_code_missing() -> None:
    stock: Dict[str, object] = {"name": "无名标的"}

    assert re_mod._score_risk(stock) == 60
    assert re_mod._price_missing_dims(stock) == ["risk"]


# --- QA 的 W4：顺序不能依赖「打标先后」或集合迭代序 ----------------------
# W4 存活原因：只有 technical/risk 两个维度时，CPython 集合迭代序**碰巧**
# 与插入序一致（实测 `list({'risk','technical'}) == ['risk','technical']`），
# 所以 set 化之后 40 条用例全绿。三个维度以上就乱了：
#   list({'risk','valuation','technical'}) == ['risk','technical','valuation']
# 现在顺序改为显式表序（见 `_price_missing_dims`），这条用例钉住机制本身。
def test_price_missing_dims_follows_canonical_order_not_marking_order() -> None:
    """乱序打标 → 输出仍按 `_DIM_LABEL_CN` 表序，不随打标先后漂移。"""
    stock: Dict[str, object] = {"code": "920982"}
    for dim in ("risk", "theme", "technical", "valuation"):  # 故意乱序
        re_mod._mark_price_data_missing(stock, dim, f"{dim} 缺数据")

    assert re_mod._price_missing_dims(stock) == [
        "valuation", "technical", "risk", "theme",
    ]


def test_caveat_text_order_is_independent_of_marking_order() -> None:
    """提示语里的维度顺序同样按表序，换打标顺序结果不变。"""
    a: Dict[str, object] = {"code": "920982"}
    b: Dict[str, object] = {"code": "920982"}
    for dim in ("risk", "technical", "earnings"):
        re_mod._mark_price_data_missing(a, dim, "x")
    for dim in ("earnings", "technical", "risk"):   # 完全相反的顺序
        re_mod._mark_price_data_missing(b, dim, "x")

    assert re_mod._price_data_caveat(a) == re_mod._price_data_caveat(b)
    assert re_mod._price_data_caveat(a) == \
        "⚠️ 盈利、技术面、风险面行情数据不足，评分基于部分维度"


def test_unknown_dimension_is_kept_after_known_ones() -> None:
    """表外维度不至于被丢掉，排在已知维度之后。"""
    stock: Dict[str, object] = {"code": "920982"}
    re_mod._mark_price_data_missing(stock, "some_future_dim", "x")
    re_mod._mark_price_data_missing(stock, "technical", "x")

    assert re_mod._price_missing_dims(stock) == ["technical", "some_future_dim"]
    assert re_mod._price_data_caveat(stock) == \
        "⚠️ 技术面、some_future_dim行情数据不足，评分基于部分维度"


def test_multiple_unknown_dimensions_are_all_kept() -> None:
    """V8 / V8b（QA 最后一轮存活项）：表外维度不止一个时不能只保留头几个。

    现实风险趋近于零（`_DIM_LABEL_CN` 已覆盖全部 6 个维度，生产上 `extra`
    恒为空），所以这条纯属 future-proofing。成本只有一条用例，顺手关掉，
    不留"以后再说"的尾巴 —— 这类尾巴基本不会再被捡起来。
    """
    stock: Dict[str, object] = {"code": "920982"}
    for dim in ("zzz_a", "zzz_b", "zzz_c", "technical"):
        re_mod._mark_price_data_missing(stock, dim, "x")

    assert re_mod._price_missing_dims(stock) == [
        "technical", "zzz_a", "zzz_b", "zzz_c",
    ]
    assert re_mod._price_data_caveat(stock) == \
        "⚠️ 技术面、zzz_a、zzz_b、zzz_c行情数据不足，评分基于部分维度"


def test_first_reason_wins_and_dims_are_deduped() -> None:
    """同一维度重复标记只记第一条原因（后续重试不该覆盖最初的失败原因）。"""
    stock: Dict[str, object] = {"code": "920982"}
    re_mod._mark_price_data_missing(stock, "technical", "第一次：所有源失败")
    re_mod._mark_price_data_missing(stock, "technical", "第二次：超时")
    re_mod._mark_price_data_missing(stock, "risk", "K线数据不足（0 行 < 20）")

    assert re_mod._price_missing_dims(stock) == ["technical", "risk"]
    assert stock[re_mod._PRICE_MISSING_KEY]["technical"] == "第一次：所有源失败"


# ============================================================
# 2. 综合评分：标记 → 可观测输出
# ============================================================
FULL_SCORES: Dict[str, int] = {
    "valuation": 70,
    "earnings": 65,
    "technical": 50,
    "capital": 50,
    "risk": 45,
    "theme": 60,
}
ALL_DIMS: Tuple[str, ...] = tuple(re_mod.RECOMMEND_WEIGHTS.keys())


def _calc_with(monkeypatch: pytest.MonkeyPatch,
               missing: Sequence[str] = ()) -> Dict[str, object]:
    """用固定维度分跑一次 `_calc_composite_score`，`missing` 里的维度打缺失标记。"""
    for dim in ALL_DIMS:
        monkeypatch.setattr(
            re_mod, f"_score_{dim}",
            _scorer(dim, FULL_SCORES[dim], mark=(dim in missing)),
        )
    return re_mod._calc_composite_score({"code": "920982", "name": "锦波生物"})


def test_composite_score_flags_missing_dimensions(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺失维度必须在 evidence 里 `available=False` + `display="数据不足"`。"""
    res = _calc_with(monkeypatch, missing=("technical", "risk"))

    assert res["evidence"]["technical"]["available"] is False
    assert res["evidence"]["technical"]["display"] == "数据不足"
    assert res["evidence"]["risk"]["available"] is False
    # 未缺失的维度必须是 True 且不带 display，避免过度标注
    for dim in ("valuation", "earnings", "capital", "theme"):
        assert res["evidence"][dim]["available"] is True
        assert "display" not in res["evidence"][dim]


def test_composite_score_reports_data_completeness(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`data_completeness` 必须能回答「哪几个维度是编的、为什么」。"""
    res = _calc_with(monkeypatch, missing=("technical", "risk"))

    dc = res["data_completeness"]
    assert dc["complete"] is False
    assert dc["missing_dimensions"] == ["technical", "risk"]
    assert set(dc["reasons"]) == {"technical", "risk"}
    assert "测试用缺失" in dc["reasons"]["technical"]


def test_composite_score_complete_when_nothing_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据完整时 `data_caveat` 必须为空串 —— 否则满屏 ⚠️ 等于没标注。"""
    res = _calc_with(monkeypatch, missing=())

    assert res["data_completeness"]["complete"] is True
    assert res["data_completeness"]["missing_dimensions"] == []
    assert res["data_caveat"] == ""


def test_data_caveat_text_lists_chinese_dimension_names() -> None:
    """提示语用中文维度名，用户看得懂（不是 technical/risk 这种内部键）。"""
    stock: Dict[str, object] = {"code": "920982"}
    re_mod._mark_price_data_missing(stock, "technical", "K线数据不足（0 行 < 30）")
    re_mod._mark_price_data_missing(stock, "risk", "K线数据不足（0 行 < 20）")

    assert re_mod._price_data_caveat(stock) == \
        "⚠️ 技术面、风险面行情数据不足，评分基于部分维度"
    assert re_mod._price_data_caveat({"code": "600519"}) == ""


def test_internal_marker_is_not_exposed_in_output(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """下划线前缀的内部标记不能出现在对外输出里（对外只有 data_completeness）。"""
    res = _calc_with(monkeypatch, missing=("technical", "risk"))

    assert re_mod._PRICE_MISSING_KEY not in res
    assert not any(k.startswith("_price") for k in res)


# ============================================================
# 3. total_score 口径：不归一化（本轮选定方案）
# ============================================================
def test_missing_dimensions_do_not_change_total_score(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺失维度照旧贡献中性分 → 标注前后 total_score 完全相同（排序不变）。"""
    complete = _calc_with(monkeypatch, missing=())
    partial = _calc_with(monkeypatch, missing=("technical", "risk"))

    assert partial["dimension_scores"] == complete["dimension_scores"]
    assert partial["total_score"] == complete["total_score"]
    # 但可观测性不同：一个标注、一个不标注
    assert complete["data_caveat"] == ""
    assert partial["data_caveat"] != ""


def test_rejected_alternative_renormalization_would_inflate_920982() -> None:
    """锁住被否决的方案（按可用维度归一化）会带来的后果，防止以后有人改回去。

    事故里的 920982：`total=60.6`、`technical=50`（缺）、`risk=45`（缺），
    权重取 `RECOMMEND_WEIGHTS`。把两个缺失维度从分母里剔掉再归一化：

        缺失维度贡献 = 0.15*50 + 0.10*45 = 12.0
        其余维度贡献 = 60.6 - 12.0      = 48.6
        剩余权重     = 1 - 0.15 - 0.10  = 0.75
        归一化后     = 48.6 / 0.75      = 64.8   ← 比原来高 4.2 分

    即「数据越缺分越高」，等于用缺失数据奖励这只股票，故本轮不采用。
    """
    w = re_mod.RECOMMEND_WEIGHTS
    total, technical, risk = 60.6, 50, 45

    contributed = w["technical"] * technical + w["risk"] * risk
    rest = total - contributed
    available_weight = 1.0 - w["technical"] - w["risk"]

    assert round(rest / available_weight, 1) == 64.8
    assert round(rest / available_weight, 1) > total  # 归一化 = 抬高分数
    # 被采用的方案：保持原公式，60.6 不动，只加标注
    assert round(total, 1) == 60.6


# ============================================================
# 4. 用户可见：推荐理由必须带提示（LLM 路径最容易漏）
# ============================================================
CAVEAT = "⚠️ 技术面、风险面行情数据不足，评分基于部分维度"


def test_caveat_appended_to_existing_reason() -> None:
    items = [{"reason": "基本面稳健", "data_caveat": CAVEAT}]
    re_mod._append_data_caveat(items)

    assert items[0]["reason"] == f"基本面稳健（{CAVEAT}）"


def test_caveat_becomes_reason_when_reason_is_empty() -> None:
    items = [{"reason": "   ", "data_caveat": CAVEAT}]
    re_mod._append_data_caveat(items)

    assert items[0]["reason"] == CAVEAT


def test_caveat_not_appended_when_data_complete() -> None:
    items = [{"reason": "基本面稳健", "data_caveat": ""}]
    re_mod._append_data_caveat(items)

    assert items[0]["reason"] == "基本面稳健"


# --- X7（QA 变异存活项）：多条目必须逐条标注，不能只标第一条 -------------
# 存活原因：上面几条用例全是单条目列表，所以在循环体末尾加个 `break` 测试照绿。
# 现实风险：Top10 里缺数据的往往不止一条（北交所 920xxx 越多越是），
# 一旦只标第一条，其余全是「看着完整其实是编的」，而且**测试不会红**。
def test_caveat_is_appended_to_every_incomplete_item() -> None:
    """3 条都缺数据 → 3 条 reason 都必须带 ⚠️（防循环里 break / early return）。"""
    items = [
        {"code": "920982", "reason": "理由A", "data_caveat": CAVEAT},
        {"code": "920826", "reason": "理由B", "data_caveat": CAVEAT},
        {"code": "830799", "reason": "理由C", "data_caveat": CAVEAT},
    ]

    re_mod._append_data_caveat(items)

    for i, item in enumerate(items):
        assert "⚠️" in item["reason"], f"第 {i+1} 条漏标了（循环被提前打断？）"
        assert item["reason"].endswith(f"（{CAVEAT}）")


def test_caveat_appended_to_all_incomplete_items_in_mixed_list() -> None:
    """混合列表：缺数据的全部标注，数据完整的一条都不许标。"""
    items = [
        {"code": "920982", "reason": "理由A", "data_caveat": CAVEAT},
        {"code": "600519", "reason": "理由B", "data_caveat": ""},
        {"code": "920826", "reason": "理由C", "data_caveat": CAVEAT},
    ]

    re_mod._append_data_caveat(items)

    assert items[0]["reason"] == f"理由A（{CAVEAT}）"
    assert items[1]["reason"] == "理由B"           # 完整数据，不许标
    assert items[2]["reason"] == f"理由C（{CAVEAT}）"  # 第三条也不能漏


def test_generate_reasons_annotates_every_item(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：`_generate_reasons` 也必须逐条标注（LLM 路径同样）。"""

    def _fake_llm(items: list) -> bool:
        for item in items:
            item["reason"] = "LLM 理由"
        return True

    monkeypatch.setattr(re_mod, "_llm_generate_reasons", _fake_llm)

    items = [
        {"code": "920982", "data_caveat": CAVEAT},
        {"code": "600519", "data_caveat": ""},
        {"code": "920826", "data_caveat": CAVEAT},
    ]
    re_mod._generate_reasons(items)

    assert items[0]["reason"] == f"LLM 理由（{CAVEAT}）"
    assert items[1]["reason"] == "LLM 理由"
    assert items[2]["reason"] == f"LLM 理由（{CAVEAT}）"


# --- 幂等性（QA 提出）：重试/二次加工不能把提示套娃 ---------------------
def test_append_data_caveat_is_idempotent() -> None:
    """调两次不能变成「…（⚠️ …）（⚠️ …）」的套娃警告。"""
    items = [{"reason": "基本面稳健", "data_caveat": CAVEAT}]

    re_mod._append_data_caveat(items)
    first = items[0]["reason"]
    re_mod._append_data_caveat(items)
    re_mod._append_data_caveat(items)

    assert items[0]["reason"] == first == f"基本面稳健（{CAVEAT}）"
    assert items[0]["reason"].count("⚠️") == 1


def test_idempotency_guard_matches_the_whole_caveat_not_just_the_warning_sign() -> None:
    """Z2（QA 低优先级存活项）：幂等守卫必须比对**完整 caveat**，不是只比 ⚠️。

    若把 `caveat in reason` 弱化成 `"⚠️" in reason`，那么一条本来就有别的
    ⚠️ 提示的理由（比如地缘警示）会被误判成"已标注"，从而漏掉数据不足提示。
    """
    items = [{"reason": "⚠️ 注意地缘风险", "data_caveat": CAVEAT}]

    re_mod._append_data_caveat(items)

    assert CAVEAT in items[0]["reason"], "被无关的 ⚠️ 误判成已标注，漏标了"
    assert items[0]["reason"].startswith("⚠️ 注意地缘风险")


def test_caveat_is_appended_on_rule_fallback_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 失败 → 走规则降级，提示仍须出现。"""
    monkeypatch.setattr(re_mod, "_llm_generate_reasons", lambda items: False)
    monkeypatch.setattr(re_mod, "_rule_reason", lambda item: "规则理由")

    items = [{"code": "920982", "name": "锦波生物", "data_caveat": CAVEAT}]
    re_mod._generate_reasons(items)

    assert items[0]["reason"] == f"规则理由（{CAVEAT}）"


def test_caveat_is_appended_on_llm_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 成功 → 原实现在这里直接 return，把提示吞掉。这条专防该回归。"""
    def _fake_llm(items: list) -> bool:
        for item in items:
            item["reason"] = "LLM 生成的理由"
        return True

    monkeypatch.setattr(re_mod, "_llm_generate_reasons", _fake_llm)

    items = [{"code": "920982", "name": "锦波生物", "data_caveat": CAVEAT}]
    re_mod._generate_reasons(items)

    assert items[0]["reason"] == f"LLM 生成的理由（{CAVEAT}）"


def test_generate_reasons_leaves_complete_items_untouched(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据完整的股票，理由里不该平白多出一个 ⚠️。"""
    def _fake_llm(items: list) -> bool:
        for item in items:
            item["reason"] = "LLM 生成的理由"
        return True

    monkeypatch.setattr(re_mod, "_llm_generate_reasons", _fake_llm)

    items = [{"code": "600519", "name": "贵州茅台", "data_caveat": ""}]
    re_mod._generate_reasons(items)

    assert items[0]["reason"] == "LLM 生成的理由"
    assert "⚠️" not in items[0]["reason"]


# ============================================================
# 5. 端到端：920982 的卡片长什么样
# ============================================================
def test_920982_stays_in_pool_but_is_annotated(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """口径落地：920982 仍留在推荐池（分数不变），但必须带上可见标注。

    这正是 `_north_fallback_skipped` 那句注释的意思：
    「让『没有这个兜底』可被观测到，而不是伪装成中性分」。
    """
    # K 线全程取不到（三级降级链对 920xxx 全失败）
    _patch_daily_df(monkeypatch, None)
    monkeypatch.setattr(re_mod, "_score_valuation", lambda s: 70)
    monkeypatch.setattr(re_mod, "_score_earnings", lambda s: 65)
    monkeypatch.setattr(re_mod, "_score_capital", lambda s: 50)
    monkeypatch.setattr(re_mod, "_score_theme", lambda s: 60)

    stock = {"code": "920982", "name": "锦波生物", "rating": "买入"}
    scored = re_mod._calc_composite_score(stock)

    # ① 仍在池子里：分数 > 0，不会因「数据不足」被静默剔除
    assert scored["total_score"] > 0
    # ② 维度分仍是中性分（口径：不重算）
    assert scored["dimension_scores"]["technical"] == 50
    assert scored["dimension_scores"]["risk"] == 60
    # ③ 但完整度是可观测的
    assert scored["data_completeness"]["complete"] is False
    assert scored["data_completeness"]["missing_dimensions"] == ["technical", "risk"]
    assert scored["data_caveat"] == CAVEAT

    # ④ 并且落到用户看到的那句话上
    monkeypatch.setattr(re_mod, "_llm_generate_reasons", lambda items: False)
    monkeypatch.setattr(re_mod, "_rule_reason", lambda item: "研报评级买入")
    re_mod._generate_reasons([scored])

    assert scored["reason"] == f"研报评级买入（{CAVEAT}）"
    assert "⚠️" in scored["reason"]
