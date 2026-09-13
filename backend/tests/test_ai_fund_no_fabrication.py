"""
P0 回归测试：AI 选基不得编造配置占比，也不得编造收益率

背景（两条独立的假数据链）：
  链 1 —— 让 LLM 现编占比：
    旧 prompt 第 4 条明令 LLM「给出建议占比（5只加起来=95%）」，JSON schema
    里带 `"pct":25`；消费侧 `p.get("pct", 19)` 兜底 19%。而同一个文件下方
    本来就有确定性配置引擎（RISK_TEMPLATES + _dynamic_adjust），根本不需要
    LLM 编数字。LLM 每次现编的占比不可复现，也没有任何依据。

  链 2 —— 假收益被当成事实算进「预期收益」：
    `round((r.get("1y", 15) or 15) / 100, 2)` 之类：数据缺失时兜底 15%/5%/-5%；
    更糟的是用了 `or` —— 真实收益率恰为 0 时 `0 or 15` 会变成 15%，把真实的
    「零收益」谎报成「+15%」。个股/混合模式还有纯硬编码的 0.25/0.12/-0.15。
    下游 app.js calcReturns 拿 pct × returns 算出展示给用户的具体金额。

修法（本测试锁死）：
  - 占比改由 _allocate_picks 按 RISK_TEMPLATES 确定性计算，LLM 给的 pct 忽略；
  - 收益率只取候选池真实值（含真实的 0），缺失 → None + returns_reason，
    不许用 `or`、不许兜底数字；假设值必须显式标注 returns_source="assumption"；
  - 前端 calcReturns 缺失项不计入求和，并表达"部分缺失"，由调用点决定不展示金额。

全部离线：mock 掉 LLM gateway 与候选池，不发网络请求、不写盘。
"""
import json
import pathlib

import pytest

import services.portfolio as pf
import services.fund_screen as fs
import infra.llm.gateway as gwmod


_REPO_ROOT = pathlib.Path(pf.__file__).resolve().parents[2]
_PORTFOLIO_SRC = pathlib.Path(pf.__file__).read_text(encoding="utf-8")


# ── 离线夹具 ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_disk_cache(monkeypatch):
    """清内存缓存 + 屏蔽文件缓存读写，保证用例互不污染且不写盘。"""
    pf._ai_fund_cache.clear()
    monkeypatch.setattr(pf, "_load_file_cache", lambda: {})
    monkeypatch.setattr(pf, "_save_file_cache", lambda *a, **k: None)
    yield
    pf._ai_fund_cache.clear()


class _FakeGateway:
    """只实现 _ai_pick_funds 用到的 call_sync。"""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def call_sync(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"content": self.content}


@pytest.fixture
def fake_llm(monkeypatch):
    """安装一个假 gateway（直接替换 LLMGateway._instance 单例）。"""
    def _install(content):
        gw = _FakeGateway(content)
        monkeypatch.setattr(gwmod.LLMGateway, "_instance", gw)
        return gw
    return _install


# 候选池：混合"完整数据 / 真实 0 / 完全缺失"三种形态
CANDIDATES = {
    "stock": [
        {"code": "110020", "name": "沪深300", "score": 88,
         "returns": {"1y": 30, "6m": 10, "3m": -5}},
        {"code": "008114", "name": "红利低波", "score": 80,
         "returns": {"1y": 0, "6m": 4, "3m": -2}},   # 1y 真实为 0（钉 `or` 的 bug）
    ],
    "bond": [
        {"code": "217022", "name": "招商产业债", "score": 70,
         "returns": {"1y": 5, "6m": 2, "3m": 0}},     # 3m 真实为 0
    ],
    "index": [
        {"code": "000216", "name": "华安黄金", "score": 60, "returns": {}},  # 全缺失
    ],
    "qdii": [
        {"code": "050025", "name": "博时标普500", "score": 75,
         "returns": {"1y": 18, "6m": 6, "3m": 3}},
    ],
}


@pytest.fixture
def candidate_pool(monkeypatch):
    def _screen(fund_type="stock", top_n=10):
        return {"funds": [dict(f) for f in CANDIDATES.get(fund_type, [])]}
    monkeypatch.setattr(fs, "screen_funds", _screen)
    return CANDIDATES


def _llm_json(items):
    return json.dumps(items, ensure_ascii=False)


# LLM 故意在 JSON 里塞回 pct（含越界的 99），全部必须被忽略
LLM_PICKS_WITH_PCT = [
    {"code": "110020", "name": "沪深300", "reason": "低估值反弹", "category": "stock", "pct": 25},
    {"code": "008114", "name": "红利低波", "reason": "防御属性", "category": "stock", "pct": 25},
    {"code": "217022", "name": "招商产业债", "reason": "平滑波动", "category": "bond", "pct": 20},
    {"code": "050025", "name": "博时标普500", "reason": "海外分散", "category": "qdii", "pct": 15},
    {"code": "000216", "name": "华安黄金", "reason": "避险对冲", "category": "index", "pct": 99},
]

RISK_PROFILES = ["保守型", "稳健型", "平衡型", "积极型", "激进型"]


# ── 1. 占比：LLM 现编的数字必须失效，占比由模板确定性给出 ───────────────

def test_llm_pct_is_ignored_and_total_is_100(fake_llm, candidate_pool):
    fake_llm(_llm_json(LLM_PICKS_WITH_PCT))
    result = pf._ai_pick_funds("稳健型", 30, 50)
    assert result is not None

    total = sum(x["pct"] for x in result)
    assert total == 100, f"占比之和必须精确 = 100，实际 {total}"

    funds = [x for x in result if x["code"] != "余额宝"]
    cash = [x for x in result if x["code"] == "余额宝"]
    assert len(funds) == 5
    assert len(cash) == 1
    assert sum(x["pct"] for x in funds) == 95
    assert cash[0]["pct"] == 5

    # LLM 瞎编的 25/25/20/15/99 若被采纳，合计会是 184 —— 必须不存在
    assert not any(x["pct"] in (25, 99) for x in funds)
    assert all(x["pct_source"] == "deterministic_template" for x in result)
    assert not any(x.get("pct_source") == "llm" for x in result)


def test_prompt_no_longer_asks_for_pct(fake_llm, candidate_pool):
    gw = fake_llm(_llm_json(LLM_PICKS_WITH_PCT))
    pf._ai_pick_funds("稳健型", 30, 50)
    prompt = gw.calls[0]
    assert '"pct"' not in prompt, "prompt 的 JSON schema 里不得再出现 pct"
    assert "不要输出任何占比" in prompt
    assert "5只加起来=95%" not in prompt


@pytest.mark.parametrize("profile", RISK_PROFILES + ["进取型"])
def test_every_valid_profile_sums_to_100(profile):
    picks = [
        {"code": "A", "pick_name": "A", "fullName": "A", "category": "stock", "score": 1, "returns": {}},
        {"code": "B", "pick_name": "B", "fullName": "B", "category": "stock", "score": 1, "returns": {}},
        {"code": "C", "pick_name": "C", "fullName": "C", "category": "bond", "score": 1, "returns": {}},
        {"code": "D", "pick_name": "D", "fullName": "D", "category": "bond", "score": 1, "returns": {}},
        {"code": "E", "pick_name": "E", "fullName": "E", "category": "qdii", "score": 1, "returns": {}},
    ]
    result = pf._allocate_picks(picks, profile)
    assert sum(x["pct"] for x in result) == 100, f"{profile} 合计 ≠ 100"
    assert result[-1]["code"] == "余额宝" and result[-1]["pct"] == 5


@pytest.mark.parametrize("profile", [None, "", "火星型", "balanced", 123])
def test_invalid_profile_falls_back_without_crash(profile):
    picks = [
        {"code": "A", "pick_name": "A", "fullName": "A", "category": "stock", "score": 1, "returns": {}},
        {"code": "B", "pick_name": "B", "fullName": "B", "category": "bond", "score": 1, "returns": {}},
    ]
    result = pf._allocate_picks(picks, profile)
    assert sum(x["pct"] for x in result) == 100


def test_same_category_equal_split(monkeypatch):
    """同一 category 内必须等分（用能整除的模板把边界钉死）。"""
    monkeypatch.setitem(pf.RISK_TEMPLATES, "TEST_EQ", {"stock": 0.60, "bond": 0.40, "cash": 0.0})
    picks = [
        {"code": "S1", "pick_name": "S1", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "S2", "pick_name": "S2", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "S3", "pick_name": "S3", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "B1", "pick_name": "B1", "fullName": "", "category": "bond", "score": 0, "returns": {}},
        {"code": "B2", "pick_name": "B2", "fullName": "", "category": "bond", "score": 0, "returns": {}},
    ]
    result = pf._allocate_picks(picks, "TEST_EQ")
    by_code = {x["code"]: x["pct"] for x in result}
    # 95% 按 60:40 拆成 stock=57 / bond=38，整除后应逐只相等
    assert [by_code["S1"], by_code["S2"], by_code["S3"]] == [19, 19, 19]
    assert [by_code["B1"], by_code["B2"]] == [19, 19]


def test_remainder_goes_to_last_member_and_never_drifts():
    """不能整除时，余数补到最后一只，类内合计精确等于目标值。"""
    picks = [
        {"code": "S1", "pick_name": "S1", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "S2", "pick_name": "S2", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "S3", "pick_name": "S3", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "S4", "pick_name": "S4", "fullName": "", "category": "stock", "score": 0, "returns": {}},
        {"code": "B1", "pick_name": "B1", "fullName": "", "category": "bond", "score": 0, "returns": {}},
    ]
    result = pf._allocate_picks(picks, "稳健型")
    by_code = {x["code"]: x["pct"] for x in result}
    stock_total = sum(by_code[c] for c in ("S1", "S2", "S3", "S4"))
    # 稳健型：stock 目标 = 59（95 的 50/80）
    assert stock_total == 59
    assert by_code["S4"] >= by_code["S1"], "余数必须补到最后一只"
    assert sum(x["pct"] for x in result) == 100


# ── 2. 收益率：真实数据优先（含真实 0），缺失 → None + 原因 ─────────────

def test_candidate_returns_real_zero_is_not_replaced():
    """负面控制：真实收益率 0 必须保持 0，绝不能被 `or` 吞成 15%。"""
    ret, src, reason = pf._candidate_returns({"1y": 0, "6m": 0, "3m": 0})
    assert ret == {"good": 0.0, "mid": 0.0, "bad": 0.0}
    assert src == "candidate_pool" and reason == ""


def test_candidate_returns_missing_is_none_with_reason():
    ret, src, reason = pf._candidate_returns({})
    assert ret == {"good": None, "mid": None, "bad": None}
    assert src == "partial"
    assert reason and "近1年" in reason and "近6月" in reason and "近3月" in reason
    # 非数值（如 "N/A"）同样按缺失处理，不得被当成数字
    ret2, src2, _ = pf._candidate_returns({"1y": "N/A", "6m": 4, "3m": None})
    assert ret2["good"] is None and ret2["mid"] == 0.04 and ret2["bad"] is None
    assert src2 == "partial"


def test_ai_pick_returns_traceable_to_candidate_pool(fake_llm, candidate_pool):
    fake_llm(_llm_json(LLM_PICKS_WITH_PCT))
    result = pf._ai_pick_funds("稳健型", 30, 50)
    by_code = {x["code"]: x for x in result}

    hs300 = by_code["110020"]
    assert hs300["returns"] == {"good": 0.30, "mid": 0.10, "bad": -0.05}
    assert hs300["returns_source"] == "candidate_pool"
    assert hs300["returns_reason"] == ""

    # 真实 1y == 0 → good 必须是 0.0，不是 0.15
    hl = by_code["008114"]
    assert hl["returns"]["good"] == 0.0
    assert hl["returns"]["good"] != 0.15

    # 候选池完全没数据 → None + reason，且 source 标为 partial
    gold = by_code["000216"]
    assert gold["returns"] == {"good": None, "mid": None, "bad": None}
    assert gold["returns_source"] == "partial"
    assert gold["returns_reason"]

    # 货币基金是假设值，必须显式标注来源
    cash = by_code["余额宝"]
    assert cash["returns_source"] == "assumption"
    assert cash["returns_reason"] == pf._CASH_FUND_RETURNS_REASON


# ── 3. 个股/混合模式：绝不为预期收益编数字 ─────────────────────────────

@pytest.fixture
def _offline_market(monkeypatch):
    monkeypatch.setattr(pf, "get_valuation_percentile", lambda: {"percentile": 50})
    monkeypatch.setattr(pf, "get_fear_greed_index", lambda: {"score": 50})
    yield


@pytest.fixture
def _fake_stock_engine(monkeypatch):
    import services.stock_screen as ssmod
    stocks = [
        {"code": "sh600000", "name": "浦发银行", "totalScore": 88},
        {"code": "sz000001", "name": "平安银行", "totalScore": 85},
        {"code": "sh601318", "name": "中国平安", "totalScore": 82},
        {"code": "sz000002", "name": "万科A", "totalScore": 80},
        {"code": "sh600519", "name": "贵州茅台", "totalScore": 79},
        {"code": "sz300750", "name": "宁德时代", "totalScore": 77},
    ]
    monkeypatch.setattr(ssmod, "screen_stocks", lambda top_n=6: {"stocks": stocks[:top_n]})
    yield


def test_stock_mode_returns_is_none_with_reason(_offline_market, _fake_stock_engine):
    out = pf.get_recommend_allocations("稳健型", with_ai=False, preference="stock")
    stocks = [a for a in out["allocations"] if a.get("assetType") == "stock"]
    assert stocks, "应拿到选股引擎结果"
    for a in stocks:
        assert a["returns"] is None
        assert a["returns_reason"], "缺失预期收益必须给出原因"
        assert a.get("returns_source") is None  # 不允许伪装成来自某个数据源


def test_mixed_mode_stock_part_returns_is_none(_offline_market, _fake_stock_engine):
    out = pf.get_recommend_allocations("稳健型", with_ai=False, preference="mixed")
    stocks = [a for a in out["allocations"] if a.get("assetType") == "stock"]
    assert stocks
    for a in stocks:
        assert a["returns"] is None
        assert a["returns_reason"]


# ── 4. 负面控制：源码里不得再出现任何兜底/硬编码数字 ────────────────────

@pytest.mark.parametrize("bad", [
    "or 15", "or 5", "or -5",
    'get("pct", 19)',
    "or 12", "or 4", "or -4",
    '"good": 0.25', '"good": 0.22',
    '"pct":25',
])
def test_no_fabrication_tokens_in_source(bad):
    assert bad not in _PORTFOLIO_SRC, f"portfolio.py 仍含兜底/硬编码: {bad!r}"


def test_classic_config_returns_are_labelled_as_assumption():
    for f in pf.RECOMMENDED_FUNDS:
        assert f["returns_source"] == "assumption"
        assert f["returns_reason"] == pf._CLASSIC_FUND_RETURNS_REASON


# ── 5. 前端：缺失不得渲染成 0 或具体金额 ───────────────────────────────

def test_frontend_calcReturns_reports_missing():
    src = (_REPO_ROOT / "app.js").read_text(encoding="utf-8")
    assert "function calcReturns" in src
    seg = src.split("function calcReturns", 1)[1][:400]
    assert "missing" in seg and "complete:missing===0" in seg
    # 旧写法：直接 x.returns[sc] 取值求和（缺失即 NaN/报错，或把 undefined 当数字）
    assert "x.returns[sc]" not in seg


def test_frontend_quiz_does_not_show_amount_when_missing():
    src = (_REPO_ROOT / "pages" / "quiz.js").read_text(encoding="utf-8")
    assert "projComplete" in src
    assert "部分基金收益数据缺失，预期收益不可计算" in src
    # 有缺失时不得绘制预测曲线
    assert "if(projComplete)setTimeout(()=>drawProjChart" in src
