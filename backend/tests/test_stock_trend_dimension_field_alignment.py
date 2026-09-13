"""选股「走势预估」8 维引擎的字段名/维度名对齐回归测试（股票版）。

与 ``test_fund_trend_dimension_field_alignment.py`` 同一类缺陷，但发生在股票版
``_enrich_stock_trend_forecast`` 上：

  维度4 资金流向  换手率代理读 s["turnover_rate"]
                  → 股票对象的真实字段是 s["turnover"]
                    （stock_data_provider.py:124/258/539 写入，stock_screen.py:1023 带出）
  维度5 市场环境  判 "偏多"/"强势"/"偏空"/"弱势" in timing_label
                  → _stock_timing_label 真实取值是 "💚 质优低估" / "🟡 动量追高" /
                    "🔴 估值偏贵" 等，永不命中；且该维度喂的是**个股择时标签**而非大盘 β，
                    维度名已改为「择时位置」
  维度8 情绪面    读 s["total_score"]
                  → screen_stocks 产出的真实字段是 s["score"]（stock_screen.py:1025）；
                    total_score 属于 recommend_engine / checklist 那条链路

调用方已核实：唯一生产调用点是 ``_compute_stock_screen``（signals.py），
``screen_stocks`` → ``_enrich_stock_labels`` → ``_enrich_stock_trend_forecast``，
因此 timing_label / score / turnover 在调用点都已存在（本文件用源码断言钉死顺序）。

全部离线：不发起网络请求、不调 LLM、不写盘。
"""
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_SIGNALS = _BACKEND / "api" / "signals.py"

_OLD_MARKET_ENV = "市场" + "环境"   # 拼串，避免本文件自身被维度名扫描工具命中


def _labeled_stock(**overrides) -> dict:
    """一只「所有真实字段都有值」的股票（模拟 screen_stocks + _enrich_stock_labels 的产出）。"""
    s = {
        "code": "600519",
        "name": "贵州茅台",
        "returns": {"20d": 6.0, "60d": 8.0, "250d": 12.0},
        "pe_percentile": 50,
        "catalyst_flags": "",            # 不触发机构加仓/减持分支，走换手率代理
        "industry_tag": "白酒",
        "timing_label": "💚 质优低估",     # _stock_timing_label 的真实取值
        "score": 88,                      # stock_screen.py:1025 的综合评分
        "turnover": 6.0,                  # 真实换手率字段（百分点）
        "amplitude": 8.0,
    }
    s.update(overrides)
    return s


# ─────────────────────────── 1. 三个死判据必须活过来 ───────────────────────────

def test_turnover_dimension_is_alive():
    """维度4 换手率代理：读 turnover 时「放量上涨」必须能判出来。"""
    from api import signals
    stocks = [_labeled_stock()]
    signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
    d = stocks[0]["trend_dimensions"]["资金流向"]
    assert d["score"] > 0, f"换手率代理仍是死代码（读的还是 turnover_rate？）：{d}"
    assert d["reason"] == "放量上涨", d


def test_timing_label_dimension_is_alive_and_renamed():
    """维度5：真实标签必须命中，且维度名必须叫「择时位置」（不再冒名「市场环境」）。"""
    from api import signals
    stocks = [_labeled_stock()]
    signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
    dims = stocks[0]["trend_dimensions"]
    assert "择时位置" in dims, f"维度5 未改名：{list(dims)}"
    assert _OLD_MARKET_ENV not in dims, f"维度5 仍叫「{_OLD_MARKET_ENV}」= 显示与计算两张皮"
    assert dims["择时位置"]["score"] > 0, f"择时位置仍恒为 0：{dims['择时位置']}"


def test_timing_label_overpriced_maps_to_negative():
    """维度5 反向锚点：估值偏贵/动量追高必须给负分，证明不是「凡非空即加分」。"""
    from api import signals
    for label in ("🔴 估值偏贵", "🟡 动量追高"):
        stocks = [_labeled_stock(timing_label=label)]
        signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
        d = stocks[0]["trend_dimensions"]["择时位置"]
        assert d["score"] < 0, f"{label} 应给负分：{d}"


def test_score_dimension_is_alive():
    """维度8：给了 score，情绪面不得再恒为 0。"""
    from api import signals
    stocks = [_labeled_stock()]
    signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
    d = stocks[0]["trend_dimensions"]["情绪面"]
    assert d["score"] > 0, f"情绪面仍恒为 0（读的还是 total_score？）：{d}"


def test_low_score_maps_to_negative():
    """维度8 反向锚点：低分必须给负分。"""
    from api import signals
    stocks = [_labeled_stock(score=20)]
    signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
    d = stocks[0]["trend_dimensions"]["情绪面"]
    assert d["score"] < 0, d


# ─────────────────── 2. 故障注入：旧字段名在真实对象上取不到值 ───────────────────

def test_old_field_names_are_absent_from_pipeline_stock():
    """旧字段名在 screen_stocks 产出的股票对象上取不到值 —— 恒为 0 的根因。"""
    s = _labeled_stock()
    assert s.get("turnover_rate") is None
    assert s.get("total_score") is None
    assert "偏多" not in s["timing_label"]
    assert "强势" not in s["timing_label"]


def _stock_forecast_source() -> str:
    src = _SIGNALS.read_text(encoding="utf-8")
    start = src.index("def _enrich_stock_trend_forecast(")
    end = src.index("def _get_market_timing_summary(")
    assert start < end, "源码结构变化，请更新本测试的切片方式"
    return src[start:end]


@pytest.mark.parametrize("stale", [
    's.get("turnover_rate")',   # 真实字段是 turnover
    's.get("total_score")',     # 真实字段是 score
    'dims["' + _OLD_MARKET_ENV + '"]',   # 已改名「择时位置」
])
def test_no_stale_field_lookup_in_stock_forecast(stale):
    """源码扫描：股票版走势预估里不许再出现这些旧字段名/旧维度名。

    任何人改回旧写法，上面那几条 *_is_alive 断言转红，本断言也同时转红。
    """
    body = _stock_forecast_source()
    assert stale not in body, f"股票版走势预估仍使用旧写法 {stale}"


def test_dead_timing_predicates_are_gone():
    """源码扫描：判「偏多/强势/偏空/弱势」的旧谓词必须消失（注释里的说明不算）。

    只扫**可执行代码行**（去掉整行注释），避免把解释性注释误判成残留。
    """
    body = _stock_forecast_source()
    code_lines = [ln for ln in body.split("\n") if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    for dead in ('"偏多"', '"强势"', '"偏空"', '"弱势"'):
        assert dead not in code, f"仍存在永不命中的旧谓词 {dead}"


# ───────────────────── 3. 维度名与权重表必须逐字一致 ─────────────────────

def test_dimension_keys_match_weight_table():
    """8 个维度 key 必须与 investor_dna.DEFAULT_WEIGHTS 的 key 完全一致。

    前端按 key 渲染标签、权重表按 key 记权重；两边不一致就会出现
    「权重表写着 A、引擎算的是 B」——正是本次要修的「显示与计算两张皮」。
    """
    from api import signals
    from services import investor_dna

    stocks = [_labeled_stock()]
    signals._enrich_stock_trend_forecast(stocks, include_dimensions=True)
    engine_keys = set(stocks[0]["trend_dimensions"])

    funds = [{
        "code": "110020", "name": "沪深300",
        "returns": {"3m": 2.0, "6m": 5.0, "1y": 8.0},
        "nav_percentile": 55, "timing_label": "⚪ 正常", "industry_tag": "宽基",
        "score": 88, "scale_billion": 120.0,
    }]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    fund_keys = set(funds[0]["trend_dimensions"])

    weight_keys = set(investor_dna.DEFAULT_WEIGHTS)
    assert fund_keys == weight_keys, (
        f"基金引擎维度名与权重表不一致：引擎多 {fund_keys - weight_keys}，"
        f"权重表多 {weight_keys - fund_keys}")
    assert stocks[0]["trend_dimensions"].keys() == funds[0]["trend_dimensions"].keys(), \
        "股票版与基金版维度名/顺序不一致"
    assert sum(investor_dna.DEFAULT_WEIGHTS.values()) == 100


def test_frontend_renders_dimension_keys_generically():
    """前端必须按 key 通用渲染维度名 —— 否则改名会变成「后端改了、前端还在显示旧名」。

    `_components.js` 用 Object.entries(tDims) 直接渲染 key，所以后端改 key
    即自动同步标签，无需再维护第二份名字表。本断言把这个前提钉住。
    """
    js = (_BACKEND.parent / "pages" / "_components.js").read_text(encoding="utf-8")
    assert "Object.entries(tDims)" in js, "前端不再按 key 通用渲染维度名"
    # 不得存在第二份维度名表（一旦出现，改名就会两边不一致）
    for name in ("动量趋势", "技术面信号", "估值水位", "资金流向", "赛道热度",
                 "波动率风险", "情绪面", _OLD_MARKET_ENV, "择时位置"):
        assert f"'{name}'" not in js and f'"{name}"' not in js, \
            f"_components.js 里出现了硬编码维度名 {name}，改名将无法自动同步"
