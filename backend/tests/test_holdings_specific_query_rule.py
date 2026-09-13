"""「我持有 X 吗」必须由规则引擎按真实持仓记录确定性作答（查得到/查不到都说清楚）。

## 复现的线上缺陷

一个有持仓的用户在生产 `/api/chat` 问「我持有贵州茅台吗？」得到的是：

    🤔 这个问题我需要更多上下文才能精准回答。
    你可以试试这些问法：…

而同一个用户没有持仓时问同类问题，却能正常答「当前你的钱袋子系统中没有持仓/资产记录」。
—— **有持仓时反而无人应答，而上下文（用户自己的持仓）明明就在 `portfolio_ctx` 里**。

根因：`_rule_based_reply_structured` 把「我持有 X 吗」这类**事实查询**当成"需要 LLM
精准回答"的问题（注释原文：`→ 交给LLM精准回答`），直接 fall through。AI 一降级，
`_rule_based_reply` 的兜底就给出"缺少上下文"这个**假理由**（上下文并不缺）。
把事实查询交给 LLM 还有个附带风险：LLM 只能看到上下文、容易把"记录里没有"说成
"中性/观察"之类的模糊话术，正是本项目要杜绝的伪装。

## 本文件的测试设计（刻意如此，别改回去）

1. **测试用的 `portfolio_ctx` 由真实构造函数 `_build_portfolio_context()` 生成**，
   不是手写的假格式。两种真实格式都覆盖：
     - 格式①（前端传 p.holdings）：`  - 贵州茅台(600519)：¥80,000，目标占比 20%`
     - 格式②（后端自拉持仓）    ：`  - 股票：贵州茅台(600519) 100股 成本¥1700`
   构造函数中会真实拉行情/新闻的小节全部用 monkeypatch 打成空实现（本文件不联网）。

2. **反空转**：不只断言"回答里有『持有』"，还断言回答**不等于兜底话术**——否则
   闸门空转（新分支没命中、只是兜底文案恰好改过）测试也会显绿。

3. **三态都要测**：持有 / 不持有 / 无法确定（记录空或格式不认识时必须说原因，不许猜）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from api import shared_helpers as sh
from api.shared_helpers import (
    _build_portfolio_context,
    _rule_based_reply,
    _rule_based_reply_structured,
    _specific_holding_reply,
)

_GENERAL_QUESTION = "asdfghjkl 随便聊聊"  # 任何分支都不该命中的句子


# ============================================================
# 把 _build_portfolio_context 里会真实取数的小节打成空实现（离线）
# ============================================================

def _stub_offline_deps(monkeypatch):
    """把这些"真实取数"入口打成空实现。

    一律 `raising=False`：这些函数在被测代码里是 `try/except` 包裹的延迟导入，
    名字对不上（模块改名/函数不存在）时本来就会静默跳过，测试不该因此报错；
    这里的目标只是**不让任何一次真实网络请求发生**。
    """
    import contextlib
    import importlib

    def _patch(mod_name, attr, value):
        with contextlib.suppress(Exception):
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, attr, value, raising=False)

    monkeypatch.setattr(sh, "get_valuation_percentile",
                        lambda *a, **k: {"percentile": 50, "index": "沪深300", "level": "合理"})
    monkeypatch.setattr(sh, "get_fear_greed_index", lambda *a, **k: {"score": 50})

    _patch("services.risk", "generate_risk_actions", lambda *a, **k: [])
    _patch("services.portfolio", "get_allocation_advice", lambda *a, **k: None)
    _patch("services.holding_intelligence", "build_holding_context", lambda *a, **k: "")
    _patch("services.macro_v8", "check_holding_management_change", lambda *a, **k: [])
    _patch("services.unified_networth", "calc_unified_networth", lambda *a, **k: {})
    _patch("services.fund_monitor", "load_fund_holdings", lambda *a, **k: [])
    _patch("services.stock_monitor", "load_stock_holdings", lambda *a, **k: [])
    for _name in ("get_earning_forecast", "get_top_list", "get_top_inst",
                  "get_macro_pmi", "get_macro_cpi", "get_index_dailybasic",
                  "get_share_repurchase", "get_holder_number", "get_hsgt_top10"):
        _patch("services.tushare_data", _name, lambda *a, **k: [])
    _patch("domain.services.user_preference_service", "get_profile", lambda *a, **k: None)
    _patch("domain.services.user_preference_service", "get_pending_insights", lambda *a, **k: [])
    _patch("domain.services.user_preference_service", "get_ironies", lambda *a, **k: [])


@pytest.fixture
def ctx_frontend(monkeypatch):
    """格式①：前端传了 portfolio（p.holdings），持仓=贵州茅台/600519。"""
    _stub_offline_deps(monkeypatch)
    p = SimpleNamespace(
        holdings=[SimpleNamespace(name="贵州茅台", code="600519",
                                  amount=80000, targetPct=20)],
        amount=100000,
    )
    return _build_portfolio_context(p, user_id="pytest_holdings_frontend")


@pytest.fixture
def ctx_backend(monkeypatch):
    """格式②：后端自拉真实持仓（p=None）。"""
    _stub_offline_deps(monkeypatch)
    import services.stock_monitor as sm
    monkeypatch.setattr(sm, "load_stock_holdings", lambda *a, **k: [
        {"name": "贵州茅台", "code": "600519", "shares": 100, "costPrice": 1700},
    ])
    return _build_portfolio_context(None, user_id="pytest_holdings_backend")


@pytest.fixture
def ctx_empty(monkeypatch):
    """真实构造函数在"该用户没有任何持仓/资产"时产出的上下文。"""
    _stub_offline_deps(monkeypatch)
    return _build_portfolio_context(None, user_id="pytest_holdings_empty")


def _fallback_text() -> str:
    """现取兜底话术（不硬编码，避免文案改了测试还绿）。"""
    return _rule_based_reply(_GENERAL_QUESTION, "", "")


# ============================================================
# 前置：确认测试用的上下文确实是"真实构造函数"的产物
# ============================================================

def test_fixtures_use_real_builder_formats(ctx_frontend, ctx_backend, ctx_empty):
    assert "【持仓明细】" in ctx_frontend
    assert "  - 贵州茅台(600519)：¥80,000，目标占比 20%" in ctx_frontend, \
        "格式①变了：请同步更新 _HOLDING_ROW_RE 与解析测试"
    assert "【持仓明细】（后端真实数据）" in ctx_backend
    assert "  - 股票：贵州茅台(600519) 100股 成本¥1700" in ctx_backend, \
        "格式②变了：请同步更新 _HOLDING_ROW_RE 与解析测试"
    assert "没有任何持仓" in ctx_empty


# ============================================================
# 1. 持有 → 明确回答"持有"
# ============================================================

def test_asks_about_held_stock_answers_yes(ctx_frontend):
    reply = _rule_based_reply("我持有贵州茅台吗？", "", ctx_frontend)
    assert "持有" in reply
    assert "贵州茅台" in reply and "600519" in reply
    assert "¥80,000" in reply, "记录里已有的成本/金额应当带上"
    # 反空转：不是兜底话术
    assert reply != _fallback_text()

    r = _rule_based_reply_structured("我持有贵州茅台吗？", "", ctx_frontend)
    assert r is not None and r["intent"] == "holdings_query" and r["deterministic"] is True


def test_asks_by_code_answers_yes(ctx_backend):
    reply = _rule_based_reply("我持有600519吗？", "", ctx_backend)
    assert "持有" in reply and "600519" in reply
    assert "100股" in reply, "格式②的份额/成本应带上"
    assert reply != _fallback_text()


def test_short_name_still_matches(ctx_frontend):
    """简称「茅台」应命中「贵州茅台」——不能因为用户没写全名就说"没持有"。"""
    reply = _rule_based_reply("我持有茅台吗？", "", ctx_frontend)
    assert "持有" in reply and "贵州茅台" in reply
    assert "没有记录到" not in reply


# ============================================================
# 2. 不持有 → 明确说"没有记录"（不是"中性/观察"，也不是"无法确定"）
# ============================================================

def test_asks_about_unheld_stock_answers_no(ctx_frontend):
    reply = _rule_based_reply("我持有宁德时代吗？", "", ctx_frontend)
    assert "没有记录到" in reply
    assert "宁德时代" in reply
    assert "无法从记录中确定" not in reply, "查得到明细就不该说无法确定"
    assert "中性" not in reply and "观察" not in reply
    assert reply != _fallback_text()


# ============================================================
# 3. 兜底话术不许再假称"缺少上下文"
# ============================================================

def test_fallback_no_longer_blames_missing_context():
    fallback = _fallback_text()
    assert "需要更多上下文" not in fallback, \
        "上下文就在手里，这句话是假理由"
    assert "更多上下文" not in fallback
    assert "AI 暂时不可用" in fallback, "要说真实原因：AI 降级、只能给有限回答"
    # 引导与免责声明保留
    assert "可以试试这些" in fallback
    assert "不构成投资建议" in fallback


# ============================================================
# 4. 持仓记录确为空 → 仍走既有的"没有持仓/资产记录"分支
# ============================================================

def test_empty_portfolio_still_says_no_records(ctx_empty):
    reply = _rule_based_reply("我持有贵州茅台吗？", "", ctx_empty)
    assert "没有持仓/资产记录" in reply
    assert reply != _fallback_text()
    # 空记录不归新分支答（避免两套文案）——结构化层返回 None，交给既有兜底分支
    assert _rule_based_reply_structured("我持有贵州茅台吗？", "", ctx_empty) is None


# ============================================================
# 5. 无法确定 → 显式说明原因，绝不猜
# ============================================================

@pytest.mark.parametrize("ctx,reason_kw", [
    ("", "上下文为空"),                                        # 压根没拿到记录
    ("  \n ", "上下文为空"),                                   # 只有空白
    ("【持仓明细】\n  这是一行无法解析的记录\n", "没有可解析"),    # 有明细小节但行不匹配
    ("【用户画像】风险类型：稳健型，总投入：¥10,000\n", "格式与预期不符"),  # 没有明细小节
])
def test_unparseable_context_says_unknown_with_reason(ctx, reason_kw):
    reply = _rule_based_reply("我持有贵州茅台吗？", "", ctx)
    assert "无法从记录中确定" in reply
    assert reason_kw in reply, f"必须给出真实原因（{reason_kw}）"
    assert "持有。" not in reply, "解析不了就不许下'持有'的结论"
    assert reply != _fallback_text()


def test_generic_word_is_not_treated_as_a_target():
    """「我持有股票吗」问的是分类不是标的，不能当成具体标的作答。"""
    assert _specific_holding_reply("我持有股票吗？", "【持仓明细】\n  - 股票：贵州茅台(600519) 100股 成本¥1700") is None


# ============================================================
# 6. 反空转：确认新分支真的被走到（而不是兜底恰好"看起来对"）
# ============================================================

def test_new_branch_is_actually_hit(ctx_frontend, ctx_empty):
    fallback = _fallback_text()
    held = _specific_holding_reply("我持有贵州茅台吗？", ctx_frontend)
    not_held = _specific_holding_reply("我持有宁德时代吗？", ctx_frontend)
    assert held is not None and not_held is not None, "新分支未命中 —— 测试会空转"
    assert held != fallback and not_held != fallback
    assert held != not_held, "持有与不持有的回答必须能区分开"
    # 空记录刻意不由新分支作答
    assert _specific_holding_reply("我持有贵州茅台吗？", ctx_empty) is None
