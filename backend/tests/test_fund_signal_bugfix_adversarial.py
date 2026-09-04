"""QA 对抗性验证：预算守门失效修复（match() 基金信号特判分支）是否真的堵死原 bug。

与工程师的 test_fund_signal_e2e.py 里两条 happy-path 用例的区别：
本文件专门打**边界 / 反例 / 防御分支**，用独立视角证明：
  1. 原 bug 链路（budget.gate 100→40 → match 重算回 100 → 推送）被彻底堵死；
  2. 特判分支对四类基金信号的**全部** relevance 取值形态都安全；
  3. 特判分支不影响持股 / 混合账户的存量信号行为（零回归）。

全部离线：monkeypatch 持仓加载器 + 注入信号，不发起任何网络请求、不写盘。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import services.signal_scout as signal_scout
import services.fund_signal as fund_signal
from services.fund_signal import budget
from services.fund_signal import state as real_state


# ============================================================
# fixtures / helpers
# ============================================================

FUND_TYPES = sorted(signal_scout._FUND_SIGNAL_TYPES)


class _FakeState:
    """内存版 state.load / state.save，隔离 push_log 落盘副作用。"""

    def __init__(self):
        self.store = {}

    def load(self, user_id, name):
        return self.store.get((user_id, name), {})

    def save(self, user_id, name, data):
        self.store[(user_id, name)] = dict(data)


@pytest.fixture
def ss(monkeypatch):
    """洗白 signal_scout 进程级缓存 + 禁写 _save_matched。"""
    signal_scout._signal_cache.clear()
    signal_scout._name_cache.clear()
    signal_scout._name_map_attempt_ts = 0.0
    signal_scout._enrich_cache.clear()

    writes = []
    monkeypatch.setattr(
        signal_scout, "_save_matched",
        lambda user_id, signals: writes.append(user_id),
        raising=True,
    )
    signal_scout._qa_writes = writes

    yield signal_scout

    signal_scout._signal_cache.clear()
    signal_scout._name_cache.clear()
    signal_scout._name_map_attempt_ts = 0.0
    signal_scout._enrich_cache.clear()
    if hasattr(signal_scout, "_qa_writes"):
        del signal_scout._qa_writes


@pytest.fixture
def fs(monkeypatch):
    store = _FakeState()
    monkeypatch.setattr(real_state, "load", store.load)
    monkeypatch.setattr(real_state, "save", store.save)
    return store


def _set_holdings(monkeypatch, stocks=(), funds=()):
    import services.stock_monitor as stock_monitor
    import services.fund_monitor as fund_monitor

    monkeypatch.setattr(
        stock_monitor, "load_stock_holdings",
        lambda uid: [{"code": c, "name": n} for c, n in stocks], raising=True,
    )
    monkeypatch.setattr(
        fund_monitor, "load_fund_holdings",
        lambda uid: [{"code": c, "name": n} for c, n in funds], raising=True,
    )


def _fund_sig(sig_type, relevance, related_holding="华夏先进制造",
              codes=("013107",), level="warning"):
    """构造一条基金信号，默认 codes 命中持仓基金 013107、related_holding 非空。"""
    return {
        "type": sig_type,
        "title": f"信号 {sig_type}",
        "content": "正文",
        "codes": list(codes),
        "source": "fund_signal",
        "time": "2026-09-04 08:00:00",
        "level": level,
        "tags": [],
        "relevance": relevance,
        "related_holding": related_holding,
    }


def _inject(monkeypatch, sig):
    """纯基金账户 + 注入单条信号，直击 match() 特判分支。"""
    _set_holdings(monkeypatch, funds=[("013107", "华夏先进制造")])
    monkeypatch.setattr(
        fund_signal, "build_signal_pool", lambda uid, sc, fc: [sig]
    )


def _unlock_sig(code6="301563", name="云汉芯城"):
    return {
        "type": "unlock",
        "title": f"解禁预警: {name}({code6}) 解禁7.00%",
        "content": f"解禁日 2026-09-30",
        "codes": [code6],
        "source": "Tushare",
        "time": "20260930",
        "level": "danger",
        "tags": ["解禁"],
    }


# ============================================================
# 1. 原 bug 链路是否真的堵死（完整链：budget.gate → match → _should_push）
# ============================================================

def test_original_bug_chain_is_blocked_end_to_end(monkeypatch, fs):
    """对抗性复现原 bug：用【真实 budget.gate】把信号砍到 40，再喂 match()。

    工程师的用例是手写 relevance=40 注入；这里走真实 budget.gate 的砍额度
    路径（第 3 条起被砍），确保不是"测试构造了一个刚好 40 的值"这种巧合。
    """
    # 先烧掉 4 个月额度，使接下来任何信号都超预算被砍到 40。
    fs.save("u1", "push_log", {
        "2026-09": ["2026-09-01T08:00:00", "2026-09-02T08:00:00",
                    "2026-09-03T08:00:00", "2026-09-04T08:00:00"],
    })

    raw = _fund_sig("fund_drawdown_rung", 100)
    gated = budget.gate("u1", [raw], now=__import__("datetime").datetime(2026, 9, 5, 8, 0, 0))
    assert gated[0]["relevance"] == 40, "前置条件失败：budget.gate 应把信号砍到 40"

    # 让 match() 拿到这条 relevance=40 且 codes 命中基金代码 013107 的信号。
    _inject(monkeypatch, gated[0])

    matched = signal_scout.match("u1")

    assert len(matched) == 1, matched
    assert matched[0]["relevance"] == 40, f"relevance 被重算回 100: {matched}"
    assert signal_scout._should_push(matched[0]) is False, "超预算信号被推送了"


def test_original_bug_chain_codes_hit_fund_code_not_recomputed(monkeypatch):
    """精确复现原 bug 的触发点：codes 命中基金代码时不得重算 100。

    这是原 bug 的「充分必要条件」——特判分支存在的唯一意义就是挡住
    下面这行重算逻辑。用 codes=["013107"]（真实基金代码）而非空 codes。
    """
    sig = _fund_sig("fund_xray_concentration", 40, codes=("013107",))
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")

    assert matched[0]["relevance"] == 40, matched[0]


# ============================================================
# 2. 边界 1：relevance = 40 / 49 / 50 / 100 的推送行为
# ============================================================

@pytest.mark.parametrize("sig_type", FUND_TYPES)
@pytest.mark.parametrize("relevance,should_push", [
    (40, False),   # 预算砍后的「仅前端」：不推
    (49, False),   # 低于推送阈值：不推（49 非 budget.gate 合法输出，但防御性验证）
    (50, True),    # 恰好等于阈值：>=50 推（50 也非合法输出，见结论）
    (100, True),   # 推送级：推
])
def test_fund_signal_relevance_boundary_push_behavior(ss, monkeypatch, sig_type, relevance, should_push):
    sig = _fund_sig(sig_type, relevance)
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")

    assert len(matched) == 1, matched
    assert matched[0]["relevance"] == relevance, matched[0]
    assert signal_scout._should_push(matched[0]) is should_push


# ============================================================
# 3. 边界 2：codes 装股票代码时，特判分支是否仍安全
# ============================================================

def test_fund_signal_with_stock_codes_ignores_code_matching(monkeypatch):
    """反例：基金信号的 codes 里装的是【股票】代码（渲染理论上不会这样，
    但要验证特判分支不看 codes，因此绝不会把股票代码匹配成相关持仓）。
    """
    # 用户【持有】该股票 + 该基金，制造最大串号风险。
    _set_holdings(
        monkeypatch,
        stocks=[("301563", "云汉芯城")],
        funds=[("013107", "华夏先进制造")],
    )
    sig = _fund_sig("fund_drawdown_rung", 40, codes=("301563",))  # 股票代码
    monkeypatch.setattr(fund_signal, "build_signal_pool", lambda uid, sc, fc: [sig])

    matched = signal_scout.match("u1")

    assert matched[0]["relevance"] == 40, "股票代码命中被重算成 100 了"
    assert signal_scout._should_push(matched[0]) is False


def test_fund_signal_stock_code_overlap_002163_not_recomputed(monkeypatch):
    """002163 重叠代码反例：既持股票又持基金，基金信号 codes=002163（基金码），
    特判分支必须沿用自身 relevance 而非命中股票持仓重算 100。
    """
    _set_holdings(
        monkeypatch,
        stocks=[("002163", "海南发展")],
        funds=[("002163", "东方惠新灵活配置混合C")],
    )
    sig = _fund_sig("fund_manager_change", 40, codes=("002163",),
                    related_holding="东方惠新灵活配置混合C")
    monkeypatch.setattr(fund_signal, "build_signal_pool", lambda uid, sc, fc: [sig])

    matched = signal_scout.match("u1")

    assert matched[0]["relevance"] == 40, matched[0]
    assert matched[0]["related_holding"] == "东方惠新灵活配置混合C"


# ============================================================
# 4. 边界 3：related_holding 空串 / 缺失
# ============================================================

def test_fund_signal_empty_related_holding_no_dangling_arrow(monkeypatch):
    """drawdown 多基金时 related_holding=''，deliver 不得出现悬空箭头。"""
    sig = _fund_sig("fund_drawdown_rung", 100, related_holding="")
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")
    text = signal_scout.deliver("u1", matched)["text"]

    assert matched[0]["related_holding"] == ""
    assert " → " not in text, f"出现悬空箭头: {text!r}"


def test_fund_signal_missing_related_holding_key_no_keyerror(monkeypatch):
    """历史 JSON / 外部信号缺 related_holding key 时不得 KeyError。"""
    sig = _fund_sig("fund_drawdown_rung", 100)
    del sig["related_holding"]
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")

    assert matched[0]["related_holding"] == "", matched[0]
    assert " → " not in signal_scout.deliver("u1", matched)["text"]


# ============================================================
# 5. 边界 4：relevance=0 / 缺失 → 防御分支不得漏进 matched
# ============================================================

@pytest.mark.parametrize("mutate", [
    lambda s: s.update({"relevance": 0}),          # 显式 0
    lambda s: s.pop("relevance"),                  # 缺失 key
])
def test_fund_signal_zero_relevance_is_dropped_not_recomputed(monkeypatch, mutate):
    """防御分支：relevance=0 的基金信号（异常态）必须被丢弃，
    绝不能落入下方「codes 命中 → 重算 100」的公共逻辑被误推。
    """
    sig = _fund_sig("fund_drawdown_rung", 100, codes=("013107",))
    mutate(sig)
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")

    # 关键：codes=["013107"] 命中了基金持仓，若走公共逻辑会被重算成 100。
    assert matched == [], f"relevance=0 的基金信号不应进入 matched: {matched}"


def test_fund_signal_none_relevance_is_dropped_not_crash(monkeypatch):
    """对抗性边界 4：relevance=None 的基金信号应被丢弃而非让 match() 崩溃。

    match() 特判分支用 _safe_float 吞脏值：None → 0.0 → 不进 matched。
    """
    sig = _fund_sig("fund_drawdown_rung", 100, codes=("013107",))
    sig["relevance"] = None
    _inject(monkeypatch, sig)

    matched = signal_scout.match("u1")

    assert matched == [], f"relevance=None 的基金信号不应进入 matched: {matched}"


# ============================================================
# 6. 边界 5：持股 / 混合账户 —— 四类 type 永不出现，特判分支零影响
# ============================================================

def test_stock_account_unlock_signal_behavior_unchanged(monkeypatch):
    """回归：持股账户的 unlock 信号仍走公共逻辑，命中股票持仓 → 100 → 推。"""
    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")])
    monkeypatch.setattr(signal_scout, "collect", lambda: [_unlock_sig("301563", "云汉芯城")])

    matched = signal_scout.match("u_stock")

    assert matched[0]["relevance"] == 100
    assert matched[0]["related_holding"] == "云汉芯城"
    assert signal_scout._should_push(matched[0]) is True


def test_mixed_account_fund_signal_type_still_uses_own_relevance(monkeypatch):
    """混合账户下，即便注入一条基金信号 type（异常但防御性验证），
    特判分支仍应沿用其自身 relevance —— 分支按【type】判定而非按账户类型，
    这正是不该受账户类型影响的自洽行为。
    """
    _set_holdings(
        monkeypatch,
        stocks=[("301563", "云汉芯城")],
        funds=[("013107", "华夏先进制造")],
    )
    sig = _fund_sig("fund_drawdown_rung", 40, codes=("013107",))
    monkeypatch.setattr(fund_signal, "build_signal_pool", lambda uid, sc, fc: [sig])

    matched = signal_scout.match("u_mixed")

    assert matched[0]["relevance"] == 40, matched[0]


def test_build_signal_pool_stock_account_never_emits_fund_types(monkeypatch):
    """确认：持股账户走 collect()，天然不含四类基金信号 type。"""
    unlock = _unlock_sig("301563", "云汉芯城")
    monkeypatch.setattr(signal_scout, "collect", lambda: [unlock])

    pool = fund_signal.build_signal_pool("u1", {"301563": "云汉芯城"}, {})

    assert all(s.get("type") not in signal_scout._FUND_SIGNAL_TYPES for s in pool)
    assert unlock in pool


# ============================================================
# 7. 全四类信号特判分支一致性（relevance 保真）
# ============================================================

@pytest.mark.parametrize("sig_type", FUND_TYPES)
def test_all_four_types_keep_their_relevance(monkeypatch, sig_type):
    """四类基金信号全部走特判分支，relevance 100 与 40 都不被篡改。"""
    for relevance in (40, 100):
        sig = _fund_sig(sig_type, relevance)
        _inject(monkeypatch, sig)

        matched = signal_scout.match("u1")

        assert len(matched) == 1, matched
        assert matched[0]["relevance"] == relevance, (sig_type, relevance)
        assert matched[0]["type"] == sig_type


# ============================================================
# 8. 加固 2：_should_push danger 分支必须排除基金 type（防预算守门被绕过）
# ============================================================

def test_should_push_danger_fund_signal_not_bypass_budget():
    """基金信号即便 level=="danger" 且 relevance=40（被砍预算），也不得
    借 _should_push 的 danger 分支绕过 relevance 校验照推。"""
    sig = {"type": "fund_drawdown_rung", "level": "danger", "relevance": 40}
    assert signal_scout._should_push(sig) is False


def test_should_push_danger_non_fund_signal_still_allowed():
    """对照组：宏观类 danger 信号（非基金 type、非个股事件类）仍照常放行，
    防止加固 2 误伤存量「全市场 danger 放行」语义。"""
    sig = {"type": "macro", "level": "danger", "relevance": 0}
    assert signal_scout._should_push(sig) is True
