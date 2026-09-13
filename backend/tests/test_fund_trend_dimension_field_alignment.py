"""选基「走势预估」8 维引擎的字段名对齐回归测试。

背景（本轮修复的死维度）：``_enrich_trend_forecast`` 里有 3 个维度读的字段名
**全链路无任何写入点**，于是永远得 0 分——挂着 3 个假维度，还把这些 0 分当成
「没有观点」喂给置信度算法：

  维度4 资金流向  读 f["scale"] / f["buy_status"]
                  → 真实字段是 f["scale_billion"] / f["purchase_warning"]
  维度5 择时位置  判 "偏多"/"强势"/"偏空"/"弱势" in timing_label
                  → 真实取值是 "💚 回调买点" / "🔴 短期过热" / "⚪ 正常" 等，永不命中
  维度7 波动率风险 读 f["sharpe"]          → 真实字段是 f["sharpe_ratio"]
  维度8 情绪面    读 f["total_score"]      → 基金对象的真实字段是 f["score"]

本文件的断言就是故障注入：把任一处字段名改回旧名，对应维度立刻回到 0，
``test_*_dimension_is_alive`` 转红。

全部离线：不发起网络请求、不调 LLM、不写盘。
"""
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
_SIGNALS = _BACKEND / "api" / "signals.py"


def _labeled_fund(**overrides) -> dict:
    """一只「所有真实字段都有值」的基金，用于证明维度真的活了。"""
    fund = {
        "code": "110020",
        "name": "沪深300指数基金",
        "returns": {"3m": 2.0, "6m": 5.0, "1y": 8.0},
        "nav_percentile": 55,
        "timing_label": "💚 回调买点",   # _fund_timing_label 的真实取值
        "industry_tag": "宽基",
        "score": 88,                     # fund_screen 的综合评分字段
        "scale_billion": 120.0,          # 真实规模字段（亿元）
    }
    fund.update(overrides)
    return fund


@pytest.fixture(autouse=True)
def _no_qdii_network(monkeypatch):
    """避免申购状态检查真的去打网络（本文件只关心字段名，不关心取数）。"""
    from api import signals
    monkeypatch.setattr(signals, "_check_qdii_purchase_status", lambda funds: None,
                        raising=True)


# ─────────────────────────── 1. 三个死维度必须活过来 ───────────────────────────

def test_scale_billion_dimension_is_alive():
    """维度4：给了 scale_billion，资金流向不得再恒为 0。"""
    from api import signals
    funds = [_labeled_fund()]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["资金流向"]
    assert d["score"] > 0, f"资金流向仍恒为 0（字段名没对上？）：{d}"
    assert d["reason"] != "资金面中性", d


def test_timing_label_dimension_is_alive():
    """维度5：timing_label="💚 回调买点" 必须被识别，不得再恒为 0。"""
    from api import signals
    funds = [_labeled_fund()]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["择时位置"]
    assert d["score"] > 0, f"择时位置仍恒为 0（判的还是「偏多/强势」？）：{d}"


def test_timing_label_overheated_maps_to_negative():
    """维度5 的反向锚点：过热标签必须给负分，证明不是「凡非空即加分」。"""
    from api import signals
    funds = [_labeled_fund(timing_label="🔴 短期过热")]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["择时位置"]
    assert d["score"] < 0, f"过热标签应给负分：{d}"


def test_score_dimension_is_alive():
    """维度8：给了 score，情绪面不得再恒为 0。"""
    from api import signals
    funds = [_labeled_fund()]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["情绪面"]
    assert d["score"] > 0, f"情绪面仍恒为 0（读的还是 total_score？）：{d}"


def test_sharpe_ratio_dimension_is_alive():
    """维度7：波动率风险读的必须是 sharpe_ratio。"""
    from api import signals
    funds = [_labeled_fund(sharpe_ratio=2.0, max_drawdown=-5.0)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["波动率风险"]
    assert d["score"] >= 5, f"夏普优秀 + 低回撤应拿满 5 分：{d}"
    assert "夏普" in d["reason"], d


# ─────────────────── 2. 故障注入：旧字段名必然让维度归零 ───────────────────

def test_old_field_names_are_absent_from_pipeline_fund():
    """旧字段名在真实基金对象上取不到值 —— 这就是「恒为 0」的根因。"""
    fund = _labeled_fund()
    assert fund.get("scale") is None
    assert fund.get("buy_status") is None
    assert fund.get("total_score") is None
    assert fund.get("sharpe") is None
    assert "偏多" not in fund["timing_label"]
    assert "强势" not in fund["timing_label"]


def _fund_trend_forecast_source() -> str:
    """只截取基金版走势预估函数体（股票版另有同形缺陷，不在本次范围）。"""
    src = _SIGNALS.read_text(encoding="utf-8")
    start = src.index("def _enrich_trend_forecast(")
    end = src.index("def _enrich_stock_trend_forecast(")
    assert start < end, "源码结构变化，请更新本测试的切片方式"
    return src[start:end]


@pytest.mark.parametrize("stale", [
    'f.get("scale")',        # 真实字段是 scale_billion
    'f.get("buy_status"',    # 全链路无写入点，已换用 purchase_warning
    'f.get("sharpe")',       # 真实字段是 sharpe_ratio
    'f.get("total_score")',  # 基金对象的真实字段是 score
])
def test_no_stale_field_lookup_in_fund_forecast(stale):
    """源码扫描：基金版走势预估里不许再出现这四个旧字段名。

    这是把「改一处、漏一处」钉死的地方：任何人把字段名改回去，
    上面那几条 *_is_alive 断言转红，本断言也同时转红。
    """
    body = _fund_trend_forecast_source()
    assert stale not in body, f"基金版走势预估仍读旧字段名 {stale}"


def test_qdii_purchase_status_is_computed_before_scoring():
    """维度4 的申购状态必须先于打分算出，否则永远是 None。"""
    body = _fund_trend_forecast_source()
    call_at = body.index("_check_qdii_purchase_status(funds)")
    loop_at = body.index("for f in funds:")
    assert call_at < loop_at, "申购状态检查在打分循环之后 → 维度4 拿不到 purchase_warning"


def test_purchase_warning_actually_feeds_dimension4(monkeypatch):
    """端到端：真的由 _check_qdii_purchase_status 写入的限购标记要能加分。"""
    from api import signals

    def _fake_check(funds):
        for f in funds:
            f["purchase_warning"] = "⚠️ 限购中"

    monkeypatch.setattr(signals, "_check_qdii_purchase_status", _fake_check, raising=True)
    funds = [_labeled_fund(scale_billion=None)]  # 去掉规模，只看申购状态
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["资金流向"]
    assert d["score"] > 0, f"限购标记未进入资金流向：{d}"
    assert "限购" in d["reason"], d


# ──────────── 4. 维度7 的夏普必须由「打分前只读缓存」补齐（全部调用路径） ────────────

def test_sharpe_is_read_from_shared_cache_before_scoring(monkeypatch):
    """不预先塞 sharpe_ratio，只靠共享缓存：维度7 也必须能拿到夏普。

    这是修复「维度7 在所有调用路径上都拿不到夏普」的核心判据 ——
    旧代码依赖调用方顺序，holdings 两条路径与 dca_scheduler 从未注入过。
    """
    from api import signals
    from services import fund_risk_adjusted as fra

    monkeypatch.setattr(
        fra, "get_risk_adjusted_cache",
        lambda code: {"available": True, "sharpe_ratio": 1.9} if code == "110020" else None,
        raising=True)

    funds = [_labeled_fund(scale_billion=None)]
    assert "sharpe_ratio" not in funds[0]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    assert funds[0]["sharpe_ratio"] == 1.9
    d = funds[0]["trend_dimensions"]["波动率风险"]
    assert d["score"] >= 2 and "夏普" in d["reason"], f"缓存里的夏普没进维度7：{d}"


def test_negative_cache_is_not_used_as_sharpe(monkeypatch):
    """负缓存（available=False）不得当成夏普值 —— 不许把「算不出」用成 0/编造值。"""
    from api import signals
    from services import fund_risk_adjusted as fra

    monkeypatch.setattr(fra, "get_risk_adjusted_cache",
                        lambda code: {"available": False, "sharpe_ratio": 9.9}, raising=True)
    funds = [_labeled_fund(scale_billion=None)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    assert "sharpe_ratio" not in funds[0], "负缓存的数字被当成了有效夏普"
    d = funds[0]["trend_dimensions"]["波动率风险"]
    assert d["score"] == 0 and "夏普" not in d["reason"], d


def test_scoring_path_does_not_trigger_warmup(monkeypatch):
    """纯打分路径只许**只读**缓存：不得触发后台预热（那会在测试期污染 data/、
    在生产期于打分链路里发起 Tushare 请求）。"""
    from api import signals
    from services import fund_risk_adjusted as fra

    enqueued = []
    monkeypatch.setattr(fra, "enqueue_risk_adjusted_warmup",
                        lambda m: enqueued.append(m), raising=True)
    monkeypatch.setattr(fra, "get_risk_adjusted_cache", lambda code: None, raising=True)

    signals._enrich_trend_forecast([_labeled_fund()], include_dimensions=True)
    assert enqueued == [], f"打分路径触发了夏普后台预热：{enqueued}"


def test_holdings_paths_inject_sharpe_before_scoring():
    """源码扫描：holdings.py 两条路径都必须先注入 sharpe_ratio 再调走势预估。"""
    src = (_BACKEND / "api" / "holdings.py").read_text(encoding="utf-8")
    assert src.count("_enrich_risk_adjusted(") >= 2, \
        "holdings.py 的两条路径未接入 _enrich_risk_adjusted"
    for marker in ("_enrich_risk_adjusted([result])", "_enrich_risk_adjusted(enriched)"):
        assert marker in src, f"holdings.py 缺少注入点 {marker}"
    # 注入点必须出现在对应的 _enrich_trend_forecast 之前
    for inject, forecast in (("_enrich_risk_adjusted([result])", "_enrich_trend_forecast(_trend_input"),
                             ("_enrich_risk_adjusted(enriched)", "_enrich_trend_forecast(enriched")):
        assert src.index(inject) < src.index(forecast), \
            f"{inject} 出现在 {forecast} 之后 → 维度7 又拿不到夏普了"


# ──────────── 5. 维度8 的口径闸门：非 0~100 的 score 一律不消费 ────────────

HOLDINGS_CALIBER = "holdings_simplified_0_50"


def test_holdings_simplified_score_is_not_consumed():
    """持仓列表路径的简化分（量纲 0~50）带 score_caliber 标记 → 维度8 必须放弃消费。

    若不放弃：score 恒 ≤50 → 该维度恒为 −2，一个恒定偏移冒充信号（静默错值）。
    """
    from api import signals
    funds = [_labeled_fund(score=12, score_caliber=HOLDINGS_CALIBER)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["情绪面"]
    assert d["score"] == 0, f"0~50 口径的简化分被当成 0~100 质量分消费了：{d}"
    assert d.get("skipped") is True, f"缺少可断言的口径跳过标记：{d}"
    assert "不计入" in d["reason"], d


def test_holdings_simplified_score_high_value_also_skipped():
    """反向覆盖：即便简化分很高（40 < 85 不可达）也不许被消费、不许伪造 0~100 分。"""
    from api import signals
    funds = [_labeled_fund(score=50, score_caliber=HOLDINGS_CALIBER)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["情绪面"]
    assert d["score"] == 0 and d.get("skipped") is True, d


@pytest.mark.parametrize("extra", [{}, {"score_caliber": "fund_screen_quality_0_100"}])
def test_fund_screen_caliber_is_still_consumed(extra):
    """反向锚点：0~100 口径（无标记 = 缺省口径，或显式标记）必须照常消费。

    防止把口径闸门做成「焊死」，也证明缺省（历史缓存无标记）向后兼容。
    """
    from api import signals
    funds = [_labeled_fund(score=88, **extra)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["情绪面"]
    assert d["score"] == 3, (extra, d)
    assert "skipped" not in d, (extra, d)


def test_low_fund_screen_score_still_negative():
    """缺省口径下的低分仍按 85/40 阈值给负分 —— 阈值本身没被改动。"""
    from api import signals
    funds = [_labeled_fund(score=20)]
    signals._enrich_trend_forecast(funds, include_dimensions=True)
    d = funds[0]["trend_dimensions"]["情绪面"]
    assert d["score"] == -2 and "skipped" not in d, d


def test_holdings_marks_its_simplified_score_caliber():
    """源码扫描：holdings.py 写简化分时必须同时打口径标记（摘掉标记 → 静默错值回归）。"""
    src = (_BACKEND / "api" / "holdings.py").read_text(encoding="utf-8")
    assert '"score_caliber"' in src, "holdings.py 未标出 score 口径"
    assert HOLDINGS_CALIBER in src, f"holdings.py 未写出口径标记 {HOLDINGS_CALIBER}"
    assert src.index('info["score"] = round(score)') < src.index(HOLDINGS_CALIBER), \
        "口径标记必须紧跟简化分写入"
