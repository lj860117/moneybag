"""选基评分回撤惩罚 / 规模量纲 修复的故障注入测试（v9.9.27）。

背景（审计 D 级三条缺陷）：
  1. `_compute_quality_score` 用 `abs(r3m)`（近3月跌幅）冒充最大回撤 —— 造数；
  2. 硬过滤把 `issue_amount`（亿份，份额）当成亿元（金额）比 < 5.0 —— 量纲错配；
  3. docstring 写「收益30%+稳定性30%+...」但代码是加法累加、无量纲归一。

本文件的核心是**故障注入**：把修复摘掉后必须转红，不许出现「死测试」：
  · test_quality_score_penalty_comes_from_real_max_drawdown
      → 摘掉 risk 分支（回到不扣分）则最后一条断言转红
  · test_quality_score_skips_penalty_without_risk
      → 把 abs(r3m) 代理加回来则转红
  · test_screen_funds_passes_risk_cache_into_scorer
      → 调用点漏传 risk 则转红
  · test_issue_amount_shares_not_filtered_as_billion_yuan
      → 把 issue_amount 兜底加回来则该基金会消失、断言转红
"""
import pytest

from services import fund_screen as fs
from services import fund_risk_adjusted as fra


# ─────────────────────── 1. 真实回撤驱动评分（故障注入）───────────────────────

# 固定一组收益：r1y=20, r3y=30, r6m=10, r3m=-8（旧代理 abs(-8)=8 > 5 会扣 3）
_BASE_ARGS = (20.0, 30.0, 10.0, -8.0, "0.15%", "20150101", 10.0)


def test_quality_score_penalty_comes_from_real_max_drawdown():
    """有真实 max_drawdown 时，评分必须随回撤幅度变化。

    故障注入：若 _drawdown_penalty 被摘掉 / 恒返回 0，下面三条断言全红。
    """
    no_risk = fs._compute_quality_score(*_BASE_ARGS)
    tiny = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": -5.0})
    mild = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": -6.0})
    mid = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": -15.0})
    deep = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": -45.0})

    assert no_risk == tiny, "回撤 ≤5% 不应扣分"
    assert no_risk - mild == 3.0, "回撤 >5% 应扣 3 分"
    assert no_risk - mid == 6.0, "回撤 >10% 应扣 6 分"
    assert no_risk - deep == 10.0, "回撤 >20% 应扣 10 分"
    # 单调性：回撤越大分越低
    assert no_risk > mild > mid > deep


def test_quality_score_accepts_both_drawdown_sign_conventions():
    """max_drawdown 库内存在负百分比（fund_screen）与正百分比（fund_detail）两种口径，
    只看幅度（abs），两种写法必须等价。"""
    neg = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": -45.0})
    pos = fs._compute_quality_score(*_BASE_ARGS, risk={"max_drawdown": 45.0})
    assert neg == pos


def test_quality_score_uses_calmar_when_max_drawdown_missing():
    """risk 无 max_drawdown 但有真实 calmar_ratio（分母就是真实最大回撤）时使用卡玛。

    说明：风险调整缓存当前契约不含 max_drawdown，若不吃 calmar，
    本修复对线上排序就是空转 —— 这一条守住「闸门必须真的动」。
    """
    no_risk = fs._compute_quality_score(*_BASE_ARGS)
    assert no_risk - fs._compute_quality_score(
        *_BASE_ARGS, risk={"calmar_ratio": -0.4}) == 6.0
    assert no_risk - fs._compute_quality_score(
        *_BASE_ARGS, risk={"calmar_ratio": 0.05}) == 3.0
    # 卡玛健康 → 不扣分
    assert fs._compute_quality_score(*_BASE_ARGS, risk={"calmar_ratio": 1.2}) == no_risk
    # max_drawdown 在场时优先于 calmar（不叠加）
    assert no_risk - fs._compute_quality_score(
        *_BASE_ARGS, risk={"max_drawdown": -45.0, "calmar_ratio": -0.4}) == 10.0


def test_quality_score_skips_penalty_without_risk():
    """risk 取不到 → 跳过回撤惩罚，绝不回退到 abs(r3m) 假代理。

    故障注入：若把 `if r3m is not None and r1y > 0 and r3m < -5:` 那段代理加回来，
    r3m=-25 会扣 10 分 → 下面 equal 断言立刻转红。
    """
    args = (20.0, 30.0, 10.0, -25.0, "0.15%", "20150101", 10.0)
    skipped = fs._compute_quality_score(*args)
    with_real = fs._compute_quality_score(*args, risk={"max_drawdown": -60.0})

    # 无数据 → 完全不扣（不是「用 r3m 扣 10 分」）
    assert skipped == fs._compute_quality_score(*args, risk=None)
    # 有真实数据 → 扣满 10 分，两者必须不同
    assert skipped - with_real == 10.0


def test_quality_score_no_fabrication_when_risk_unusable():
    """risk 存在但没有可用真实字段（空/None/非数/负缓存）→ 一律不扣分、不造数。"""
    base = fs._compute_quality_score(*_BASE_ARGS)
    for bad in (
        None,
        {},
        {"available": False},
        {"max_drawdown": None, "calmar_ratio": None},
        {"max_drawdown": "abc", "calmar_ratio": "——"},
        {"sharpe_ratio": 1.5},          # 有别的字段但没有回撤/卡玛 → 不猜
    ):
        assert fs._compute_quality_score(*_BASE_ARGS, risk=bad) == base, f"risk={bad!r}"


# ─────────────────────── 2. 调用点必须真的传 risk（故障注入）───────────────────────

class _Row:
    """极简 DataFrame-Series 替身：支持 .index / .get(k, default) / .iloc[i]。"""

    def __init__(self, data):
        self._d = dict(data)
        self.index = list(self._d.keys())
        self.iloc = list(self._d.values())

    def get(self, key, default=None):
        return self._d.get(key, default)


def _row(code_name, scale_yuan=None, note="-"):  # noqa: ARG001
    """构造一行榜单数据。scale_yuan 为 None 时不带「规模」列（走位置推断分支）。"""
    data = {
        "序号": "1",
        "简称": code_name,
        "单位净值": 1.5,
        "近1月": 2.0,
        "近3月": -8.0,
        "近6月": 10.0,
        "近1年": 20.0,
        "近2年": note,          # 位置 7：非数字 → 位置推断拿不到规模
        "近3年": 30.0,
        "今年来": 5.0,
        "手续费": "0.15%",
    }
    if scale_yuan is not None:
        data["规模"] = scale_yuan
    return _Row(data)


def _patch_screen_io(monkeypatch, rows, ts_map, ra_get, captured=None):
    """把 screen_funds 的 I/O 全部替换成内存实现（不写 data/、不发网络）。"""
    monkeypatch.setattr(fs, "_load_fund_rank_data", lambda: rows)
    monkeypatch.setattr(fs, "_load_ts_rank_map", lambda: ts_map)
    monkeypatch.setattr(fs, "_file_cache_get", lambda key: None)
    monkeypatch.setattr(fs, "_file_cache_set", lambda key, value: None)
    monkeypatch.setattr(fs, "enrich_fund_metrics", lambda funds, limit=20: None)
    monkeypatch.setattr(fra, "get_risk_adjusted_cache", ra_get)
    fs._fund_screen_cache.clear()
    fs._scale_cache.clear()

    if captured is not None:
        real = fs._compute_quality_score

        def spy(*args, **kwargs):
            captured.append(kwargs.get("risk", "<missing>"))
            return real(*args, **kwargs)

        monkeypatch.setattr(fs, "_compute_quality_score", spy)


def test_screen_funds_passes_risk_cache_into_scorer(monkeypatch):
    """调用点必须把风险缓存结果传进评分器，否则排序仍用不到真实回撤。

    故障注入：把 `risk=risk` 参数从调用点删掉 → captured 为 "<missing>" → 转红。
    """
    captured = []
    ra_metric = {"available": True, "max_drawdown": -45.0}
    _patch_screen_io(
        monkeypatch,
        rows={"000001": _row("测试稳健混合A", scale_yuan=30.0)},
        ts_map={"000001": {"list_date": "20150101", "issue_amount": 30.0}},
        ra_get=lambda code: ra_metric if code == "000001" else None,
        captured=captured,
    )

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=1)

    assert captured == [ra_metric], f"调用点未传 risk：{captured!r}"
    funds = result["funds"]
    assert len(funds) == 1
    # 真实回撤 -45% → 扣 10 分，分数必须体现出来
    expected = fs._compute_quality_score(
        20.0, 30.0, 10.0, -8.0, "0.15%", "20150101", 30.0, risk=ra_metric)
    assert funds[0]["score"] == round(expected, 2)


def test_screen_funds_scores_without_risk_cache(monkeypatch):
    """冷启动（缓存未命中）→ risk=None，跳过惩罚但基金照常入榜、不抛异常。"""
    captured = []
    _patch_screen_io(
        monkeypatch,
        rows={"000001": _row("测试稳健混合A", scale_yuan=30.0)},
        ts_map={"000001": {"list_date": "20150101", "issue_amount": 30.0}},
        ra_get=lambda code: None,
        captured=captured,
    )

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=1)

    assert captured == [None]
    assert len(result["funds"]) == 1
    assert result["funds"][0]["score"] == round(
        fs._compute_quality_score(
            20.0, 30.0, 10.0, -8.0, "0.15%", "20150101", 30.0, risk=None), 2)


def test_screen_funds_survives_risk_cache_import_failure(monkeypatch):
    """风险缓存模块 import 失败时，绝不能让 risk 未定义把整批基金静默丢掉。

    故障注入：删掉调用点里的 `risk = None` 初始化 → `risk` 未定义 → NameError
    被外层 `except Exception: continue` 吞掉 → 每只基金都被丢弃、榜单变空 → 转红。
    （这正是本次开发中一度出现、随后自查修复的写法，故永久加锁。）
    """
    import sys

    _patch_screen_io(
        monkeypatch,
        rows={"000001": _row("测试稳健混合A", scale_yuan=30.0)},
        ts_map={"000001": {"list_date": "20150101", "issue_amount": 30.0}},
        ra_get=lambda code: None,
    )
    # sys.modules 置 None → screen_funds 内部 `from services.fund_risk_adjusted import ...` 抛 ImportError
    monkeypatch.setitem(sys.modules, "services.fund_risk_adjusted", None)

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=1)

    assert len(result["funds"]) == 1, "缓存模块不可用不该丢掉基金"
    assert result["funds"][0]["score"] == round(
        fs._compute_quality_score(
            20.0, 30.0, 10.0, -8.0, "0.15%", "20150101", 30.0, risk=None), 2)


# ─────────────── 5. 显式契约字段优先于 calmar（2026-09-13 补入 max_drawdown）───────────────

def test_explicit_max_drawdown_wins_over_calmar():
    """risk 同时带 max_drawdown 与 calmar_ratio 时，必须用**显式回撤字段**。

    故障注入：把取数顺序写成「先看 calmar」→ 两组断言分别得到 −6 / 0，立刻转红。
    构造的两个 risk 字典里两个字段故意给出**不同档位**的结论。
    """
    # 显式回撤 6% → −3；卡玛 −0.5 → 若走卡玛会是 −6
    assert _iso(r3m=-25.0, risk={"max_drawdown": -6.0, "calmar_ratio": -0.5}) == -3.0
    # 显式回撤 25% → −10；卡玛 1.2 → 若走卡玛会是 0（健康）
    assert _iso(r3m=-25.0, risk={"max_drawdown": -25.0, "calmar_ratio": 1.2}) == -10.0
    # 显式字段存在但值非法 → 才退到卡玛
    assert _iso(r3m=-25.0,
                risk={"max_drawdown": "abc", "calmar_ratio": -0.5}) == -6.0
    # 显式回撤 = 0（真实"无回撤"）是有效值 → 不惩罚，也不落到卡玛
    assert _iso(r3m=-25.0,
                risk={"max_drawdown": 0.0, "calmar_ratio": -0.5}) == 0.0


def test_contract_max_drawdown_is_percentage_and_reaches_scorer(monkeypatch):
    """端到端锁链：契约字段 → 评分器，且单位必须是**百分比**。

    用 monkeypatch 造净值序列 1.0 → 1.5 → 1.2（回撤 20%），不联网。
    若契约字段误写成比例 0.2（而不是 20.0），_drawdown_penalty 会返回 0.0
    （0.2 落在 >5 以下），本条断言立刻转红 —— 这就是量纲锁。
    """
    import services.tushare_data as ts

    navs = [
        {"nav_date": "20240101", "unit_nav": 1.0, "adj_nav": 1.0},
        {"nav_date": "20240102", "unit_nav": 1.5, "adj_nav": 1.5},
        {"nav_date": "20240103", "unit_nav": 1.2, "adj_nav": 1.2},
    ]
    monkeypatch.setattr(ts, "is_configured", lambda: True)
    monkeypatch.setattr(ts, "get_fund_nav", lambda code, days=60: {"navs": navs})
    monkeypatch.setattr(ts, "get_index_daily", lambda *a, **k: [
        {"trade_date": "20240101", "close": 3000.0},
        {"trade_date": "20240102", "close": 3010.0},
        {"trade_date": "20240103", "close": 2990.0},
    ])

    out = fra.compute_risk_adjusted_metrics("000001", fund_type="股票型")

    # 峰值 1.5 → 谷 1.2 = 20% → 百分比 20.0（不是比例 0.2，也不是负号 20.0 之外的写法）
    assert out["max_drawdown"] == pytest.approx(20.0)
    # 评分器吃同一个字段：20.0 落在 >10 档 → −6（不是 >20 档的 −10）
    assert fs._drawdown_penalty({"max_drawdown": out["max_drawdown"]}) == -6.0
    # 契约骨架必须始终含该键（即使 early return 也不缺字段）
    skeleton = fra.compute_risk_adjusted_metrics("000002", fund_type="债券型")
    assert "max_drawdown" in skeleton and skeleton["max_drawdown"] is None


def test_contract_max_drawdown_none_when_unavailable(monkeypatch):
    """净值拿不到 → 契约字段必须是 None（不是 0、不是占位值）。"""
    import services.tushare_data as ts

    monkeypatch.setattr(ts, "is_configured", lambda: True)
    monkeypatch.setattr(ts, "get_fund_nav", lambda code, days=60: {"navs": []})
    monkeypatch.setattr(ts, "get_index_daily", lambda *a, **k: [])

    out = fra.compute_risk_adjusted_metrics("000001", fund_type="股票型")

    assert out["max_drawdown"] is None
    # None 不能变成惩罚：取不到就是取不到
    assert fs._drawdown_penalty(out) == 0.0


# ─────────────────── 4. docstring 各项上限必须与代码一致（防文档说谎）───────────────────

def _iso(**kw):
    """逐项隔离测量：只打开指定的一项，其余置空/关闭。"""
    a = {"r1y": 0.0, "r3y": None, "r6m": None, "r3m": None,
         "fee": "0.5%", "list_date": None, "issue_amount": None, "risk": None}
    a.update(kw)
    return fs._compute_quality_score(
        a["r1y"], a["r3y"], a["r6m"], a["r3m"], a["fee"],
        a["list_date"], a["issue_amount"], risk=a["risk"])


def test_docstring_bounds_match_code():
    """_compute_quality_score docstring 里写的每一项上限，必须能在代码里复现。

    故障注入：改任何一项系数/档位而与 docstring 不一致 → 本条转红。
    测 clamp 时用「固定 spread」的配置，避免周期离散惩罚污染单项读数。
    """
    # 收益项 r1y：≤50 → ×0.20（上限 10.0）；>50 → +0.04/点，上游已剔除 >80
    # （periods 只有 r1y 一个 → 不进惩罚块，读数是纯项值）
    assert _iso(r1y=50.0) == 10.0
    assert _iso(r1y=80.0) == 11.2
    # 收益项 r3y：clamp(r3y/3, −20, 40) × 0.12 → +4.8 / −2.4
    assert _iso(r1y=50.0, r3y=120.0) == 14.8      # 10.0 + 4.8
    assert _iso(r1y=50.0, r3y=999.0) == 14.8      # 上限 clamp 到 40
    assert _iso(r1y=50.0, r3y=-60.0) == 7.6       # 10.0 − 2.4
    assert _iso(r1y=50.0, r3y=-999.0) == 7.6      # 下限 clamp 到 −20

    # 收益项 r6m：clamp(r6m, −20, 40) × 0.08 → +3.2 / −1.6
    # 固定配置：spread 恒为 100（>60 → −6），r1y=80 → 热罚 −5，fee 0.3% → +2
    #   11.2(r1y) + 3.2(r6m) − 5 − 6 + 2 = 5.4
    _fix = {"r1y": 80.0, "r3m": -20.0, "r6m": 40.0, "fee": "0.3%"}
    assert _iso(**_fix) == pytest.approx(5.4)
    assert _iso(**{**_fix, "r6m": 999.0}) == pytest.approx(5.4)   # 上限 clamp 到 40
    #   r6m = −20 → −1.6：11.2 − 1.6 − 5 − 6 + 2 = 0.6
    assert _iso(**{**_fix, "r6m": -20.0}) == pytest.approx(0.6)
    assert _iso(**{**_fix, "r6m": -999.0}) == pytest.approx(0.6)  # 下限 clamp 到 −20

    # 回撤惩罚档位表（docstring: 20/10/5 → −10/−6/−3）
    assert [_iso(r3m=-25.0, risk={"max_drawdown": d})
            for d in (-5.0, -6.0, -11.0, -21.0)] == [0.0, -3.0, -6.0, -10.0]
    # 卡玛档位表（docstring: <0 → −6；<0.2 → −3）
    assert [_iso(r3m=-25.0, risk={"calmar_ratio": c})
            for c in (-0.5, 0.0, 0.19, 0.2)] == [-6.0, -3.0, -3.0, 0.0]


def test_docstring_no_percent_weights_anymore():
    """历史 docstring 谎称「收益30%+稳定性30%+...」，代码其实无量纲归一、不是加权求和。

    这里把「不是加权求和」这个事实钉住，防止有人再把伪权重写回去。
    """
    doc = fs._compute_quality_score.__doc__ or ""
    assert "启发式" in doc
    assert "不是加权求和" in doc
    assert "量纲归一" in doc
    assert "A)收益30%" not in doc and "收益(30%)" not in doc


# ─────────────────────── 3. 规模量纲：亿份 ≠ 亿元（故障注入）───────────────────────

def test_issue_amount_shares_not_filtered_as_billion_yuan(monkeypatch):
    """过滤4 只按「2.0 亿份」把关；不得再拿 issue_amount 去比「5.0 亿元」。

    该基金份额 3.0 亿份（≥2.0 亿份，通过过滤4），榜单无规模列、_scale_cache 也无值
    → 亿元口径拿不到 → 本条不过滤。故障注入：把 `scale_for_filter = float(issue_amount)`
    兜底加回来，3.0 < 5.0 → 该基金被误杀，断言转红。
    """
    _patch_screen_io(
        monkeypatch,
        rows={"000002": _row("测试小份额混合C", scale_yuan=None)},
        ts_map={"000002": {"list_date": "20150101", "issue_amount": 3.0}},
        ra_get=lambda code: None,
    )

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=5)

    codes = [f["code"] for f in result["funds"]]
    assert codes == ["000002"], "3.0 亿份（份额）不是 3 亿份<5 亿元，不该被踢出"


def test_scale_cache_hit_still_filters_small_in_yuan(monkeypatch):
    """亿元口径的过滤没被削弱：_scale_cache 里有 3.0 亿元真实规模 → 仍必须剔除。"""
    _patch_screen_io(
        monkeypatch,
        rows={"000003": _row("测试微盘混合D", scale_yuan=None)},
        ts_map={"000003": {"list_date": "20150101", "issue_amount": 80.0}},
        ra_get=lambda code: None,
    )
    fs._scale_cache["000003"] = (3.0, __import__("time").time())  # 3.0 亿元

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=5)

    assert result["funds"] == []
    assert result["excluded_count"] == 1


def test_issue_amount_below_two_yi_shares_still_filtered(monkeypatch):
    """过滤4 的份额口径阈值（2.0 亿份）保持有效，没有被这次改动放松。"""
    _patch_screen_io(
        monkeypatch,
        rows={"000004": _row("测试迷你混合E", scale_yuan=50.0)},
        ts_map={"000004": {"list_date": "20150101", "issue_amount": 1.5}},
        ra_get=lambda code: None,
    )

    result = fs.screen_funds(fund_type="all", sort_by="score", top_n=5)

    assert result["funds"] == []
