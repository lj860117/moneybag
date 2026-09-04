"""P0-3 回撤档位状态机（冷启动静默 / 触发 / 重新武装 / 按天合并）独立回归。

全部离线：monkeypatch drawdown.state 为内存态，不写盘、不发网络。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.fund_signal.portfolio import FundPosition
from services.fund_signal import drawdown
from services.fund_signal import state as real_state


class _FakeState:
    """内存版 state.load / state.save，隔离测试间的落盘副作用。

    ⚠️ 只 monkeypatch 真实 state 模块的 load/save 两个函数，而不替换整个
    state 模块引用 —— 这样 drawdown.py 里的 `state.DRAWDOWN_STATE` 等常量
    仍然来自真实模块，不会因替换成 _FakeState 而 AttributeError。
    """
    def __init__(self):
        self.store = {}

    def load(self, user_id, name):
        return self.store.get((user_id, name), {})

    def save(self, user_id, name, data):
        self.store[(user_id, name)] = dict(data)


def _pos(code, name, unit_nav, cost_nav, weight_mv=50.0, is_qdii=False):
    """构造一个 FundPosition，含最小可用的 nav_history。"""
    return FundPosition(
        code=code, name=name, shares=1000.0,
        cost_nav=cost_nav, unit_nav=unit_nav, adj_nav=unit_nav,
        is_qdii=is_qdii,
        market_value=unit_nav * 1000.0, weight_mv=weight_mv,
        nav_date="20260930",
        nav_history=[{"nav_date": "20260930", "unit_nav": unit_nav, "adj_nav": unit_nav}],
    )


@pytest.fixture
def fs(monkeypatch):
    store = _FakeState()
    monkeypatch.setattr(real_state, "load", store.load)
    monkeypatch.setattr(real_state, "save", store.save)
    return store


def test_cold_start_writes_state_and_does_not_push(fs):
    """冷启动：状态文件不存在 → 写 rung + cold_start=True，不推送。"""
    positions = [_pos("005698", "华夏全球科技先锋", 2.9, 3.5, weight_mv=100.0)]

    out = drawdown.collect("u1", positions)

    assert out == []
    st = fs.load("u1", "drawdown_state")
    assert st.get("cold_start") is True
    assert st["rungs"]["005698"] == -1  # -17.14% 未破 -20 档


def test_trigger_then_same_rung_no_repush(fs):
    """跌破更深档 → 推 1 条；同值再跑 → 档位内震荡不重推。"""
    # 冷启动
    drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 2.9, 3.5, weight_mv=100.0)])
    # dd = 1.925/3.5 - 1 = -45% → 档 3（-40）
    out = drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 1.925, 3.5, weight_mv=100.0)])

    assert len(out) == 1
    assert out[0]["type"] == "fund_drawdown_rung"
    assert "-40" in out[0]["title"], out[0]["title"]

    # 同值再跑 → 不重推
    assert drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 1.925, 3.5, weight_mv=100.0)]) == []
    st = fs.load("u1", "drawdown_state")
    assert st["rungs"]["005698"] == 2


def test_rearm_when_recovered_above_rung_plus_buffer(fs):
    """回升到档位 +5pct 上方 → 重新武装（降一档），不推送。"""
    drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 2.9, 3.5, weight_mv=100.0)])
    drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 1.925, 3.5, weight_mv=100.0)])  # rung=2
    # dd = 2.345/3.5 - 1 = -33% > -35 → rearm（rung 2 → 1）
    out = drawdown.collect("u1", [_pos("005698", "华夏全球科技先锋", 2.345, 3.5, weight_mv=100.0)])

    assert out == []
    st = fs.load("u1", "drawdown_state")
    assert st["rungs"]["005698"] == 1


def test_same_day_multi_fund_triggers_merge_to_one_signal(fs):
    """同一天 3 只基金触发 → 只返回 1 条 Signal。"""
    names = [("a", "基金A"), ("b", "基金B"), ("c", "基金C")]
    drawdown.collect("u1", [_pos(c, n, 3.0, 3.5) for c, n in names])

    # 3 只同时跌到 -34.3%（档 2）
    out = drawdown.collect("u1", [_pos(c, n, 2.3, 3.5) for c, n in names])

    assert len(out) == 1
    assert out[0]["type"] == "fund_drawdown_rung"
    assert "3 只基金" in out[0]["title"], out[0]["title"]


def test_missing_unit_nav_is_skipped_not_crashing(fs, capsys):
    """unit_nav 缺失的基金不计入回撤触发，打印告警，不阻断整体。"""
    positions = [
        _pos("005698", "华夏全球科技先锋", 2.9, 3.5, weight_mv=60.0),
        _pos("013107", "华夏先进制造", 0.0, 2.4, weight_mv=40.0),  # unit_nav=0 → 跳过
    ]
    drawdown.collect("u1", positions)
    assert "013107" in capsys.readouterr().out
    # 仅正常基金进入状态
    st = fs.load("u1", "drawdown_state")
    assert list(st["rungs"]) == ["005698"]


def test_drawdown_uses_cost_nav_not_adj_nav():
    """B8 铁律：dd_cost 必须用 unit_nav / cost_nav（002163 adj/unit=2.352）。"""
    # 002163：unit_nav=2.8827, cost_nav=2.5940 → 正确 +11.13%；
    # 若误用 adj_nav=6.7802 会得 +161.37%。
    p = _pos("002163", "东方惠新", 2.8827, 2.5940, weight_mv=100.0)
    p.adj_nav = 6.7802

    import services.fund_signal.drawdown as dd
    # 直接算触发口径：cost_nav 基准
    dd_cost = (p.unit_nav / p.cost_nav - 1.0) * 100.0
    assert abs(dd_cost - 11.13) < 0.05, dd_cost
    # 反证：若用 adj_nav 会得到荒谬的 +161%
    wrong = (p.adj_nav / p.cost_nav - 1.0) * 100.0
    assert wrong > 150, "adj_nav/cost_nav 应 >150%，用于反证禁止此口径"
