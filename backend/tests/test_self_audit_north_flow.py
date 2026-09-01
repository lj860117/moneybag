"""
_check_north（北向资金探针校验）守护测试
============================================================================
背景：2026-08-30 凌晨2点的周度自检（`data/audit/latest.json`）捕获到一条
真实矛盾：北向资金 `net_flow_20d=-759.8`（20日净流出759.8亿）但
`trend="大幅流入"`——数据源返回的 `net_flow_5d=+100.34`（5日净流入，
幅度很小）与 `net_flow_20d` 方向相反，且 `trend` 用了"大幅"这种强措辞
却对应一个远低于阈值的短窗口幅度。

规则探针（`_check_north`）当时的代码版本还没加固完成（加固 commit 写入
时间比这次自检运行时间晚了约10小时），所以规则层面判定为 pass，靠 LLM
审计层（`llm_audit`）兜底发现了这个矛盾，前端 banner 展示的
"存在北向资金逻辑矛盾"就是这次事件的产物。

用户看到 banner 后追问时排查发现：加固后的 `_check_north`（本文件测试
的对象）用同一份历史数据重放，能正确返回 `ok=False`（命中"趋势措辞与
数值不符"规则）——修复代码本身没问题，只是没赶上那次自检的运行时刻，
纯粹是时序问题。但排查过程中发现 `_check_north` 从未有任何单元测试
覆盖，属于"修复了但没有回归测试锁定"——这个模式在本项目反复出现，
本次补上。

这个文件测什么：
  - 用 2026-08-30 那次真实历史矛盾数据重放，回归锁定"这个具体bug必须
    被拦住"（不能靠人工重放验证一次就算完，必须变成自动化测试）。
  - `_check_north` 涵盖的全部规则分支：净流入不可得的合法降级 / 方向
    与符号矛盾 / 措辞强度与数值不符 / 跨窗口符号冲突 / 成交额区间校验 /
    净流入不可得时残留伪数据。
  - 正常数据（无矛盾）不应被误报——防止"为了堵死一个bug把正常场景也
    堵死"这种矫枉过正。

不测什么：
  - `run_data_probes()`/`run_weekly_audit()` 完整自检流程本身（那需要
    真实网络调用+LLM，不适合作为快速单测，这里只测 `_check_north` 这
    个纯函数）。
  - LLM 审计层的判断逻辑（那是独立于规则探针的另一层防护，职责不同）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from use_cases.self_audit import _check_north


def test_regression_2026_08_30_sign_conflict_bug_is_caught():
    """回归锁定 2026-08-30 真实事故：net_flow_5d=+100.34(5日净流入)与
    net_flow_20d=-759.8(20日净流出)方向相反，且 trend="大幅流入"这个
    强措辞对应的实际幅度(100.34亿)远低于"大幅"应有的量级——必须被拦住。

    这份数据是从 data/audit/latest.json（2026-08-30 02:05 那次自检的
    真实历史记录）里原样摘录的，不是构造的假数据。
    """
    val = {
        "net_flow_today": -67.87,
        "net_flow_5d": 100.34,
        "net_flow_20d": -759.8,
        "trend": "大幅流入",
        "available": True,
        "source": "tushare",
        "net_flow_available": True,
        "data_date": "20260828",
    }
    ok, msg = _check_north(val)
    assert ok is False, "2026-08-30 的真实历史矛盾数据必须被拦住"
    assert "措辞" in msg or "矛盾" in msg or "方向" in msg


def test_net_flow_unavailable_is_legitimate_degradation():
    """净流入不可得（net_flow_available=False）是数据源口径变更导致的
    合法降级，不是故障——2024-08-19 起交易所停止披露北向日频净买入。
    这是 2026-08-31 之后真实生产数据的实际状态（已通过真实探针验证）。
    """
    val = {
        "net_flow_available": False,
        "trend": "数据不可得",
        "unavailable_reason": "2024-08-19 起沪深交易所停止披露北向日频净买入",
        "turnover_today": 3259.57,
        "available": True,
    }
    ok, msg = _check_north(val)
    assert ok is True
    assert "合法降级" in msg


def test_net_flow_unavailable_but_trend_has_direction_word_is_caught():
    """净流入不可得时，trend 却仍然标注了流入/流出方向词——疑似用
    成交额噪声编造资金流向，必须被拦住（不能让"数据缺失"悄悄变成
    "编造一个方向"）。
    """
    val = {
        "net_flow_available": False,
        "trend": "小幅流入",
        "turnover_today": 3000.0,
        "available": True,
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "编造" in msg or "方向" in msg


def test_net_flow_unavailable_but_stale_numbers_remain_is_caught():
    """净流入不可得时，net_flow_5d/net_flow_20d 等字段却残留非空数值
    ——疑似"假数据换皮"（数据源已经降级，但字段没有被正确清空）。
    """
    val = {
        "net_flow_available": False,
        "trend": "数据不可得",
        "net_flow_5d": 50.0,
        "available": True,
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "残留" in msg


def test_direction_word_conflicts_with_sign_is_caught():
    """net_flow_5d 为正（净买入）但 trend 标注为"流出"——方向与符号
    必须一致，这是最基础的矛盾检测。"""
    val = {
        "net_flow_available": True,
        "net_flow_5d": 300.0,
        "trend": "资金流出",
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "矛盾" in msg


def test_cross_window_sign_conflict_with_large_magnitude_is_caught():
    """5日与20日方向相反，且较大一侧幅度达到阈值(500亿)——疑似把当日
    成交额当累计值做差分，或窗口标注错乱。用一组独立构造的数据
    （不同于历史bug的具体数值）验证这条规则本身的边界条件。"""
    val = {
        "net_flow_available": True,
        "net_flow_5d": 200.0,
        "net_flow_20d": -600.0,
        "trend": "净流入",
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "方向相反" in msg or "差分" in msg


def test_cross_window_sign_conflict_below_threshold_is_not_flagged():
    """5日与20日方向相反，但幅度都很小（低于500亿阈值）——这种情况
    不应被拦住，避免对噪声级别的窗口差异过度敏感（阈值存在的意义）。"""
    val = {
        "net_flow_available": True,
        "net_flow_5d": 50.0,
        "net_flow_20d": -80.0,
        "trend": "净流入",
    }
    ok, msg = _check_north(val)
    assert ok is True


def test_strong_wording_without_matching_magnitude_is_caught():
    """trend 用"显著"这种强措辞，但对应幅度(150亿)低于300亿阈值——
    措辞强度与数值不符。"""
    val = {
        "net_flow_available": True,
        "net_flow_5d": 150.0,
        "trend": "资金显著流入",
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "措辞" in msg


def test_strong_wording_with_matching_magnitude_is_not_flagged():
    """trend 用"大幅"这种强措辞，且对应幅度(800亿)确实达到量级——
    不应被误报（防止矫枉过正）。"""
    val = {
        "net_flow_available": True,
        "net_flow_5d": 800.0,
        "trend": "大幅流入",
    }
    ok, msg = _check_north(val)
    assert ok is True


def test_turnover_out_of_reasonable_range_is_caught():
    """成交额超出合理区间（1000~6000亿）——疑似单位错误或数据源口径
    变更。"""
    val = {
        "net_flow_available": False,
        "trend": "数据不可得",
        "turnover_today": 15000.0,
        "available": True,
    }
    ok, msg = _check_north(val)
    assert ok is False
    assert "合理区间" in msg


def test_turnover_within_reasonable_range_is_not_flagged():
    """成交额在合理区间内——不应被误报（这是 2026-08-31 真实生产数据
    的实际量级，3259.57亿）。"""
    val = {
        "net_flow_available": False,
        "trend": "数据不可得",
        "turnover_today": 3259.57,
        "turnover_avg_5d": 2823.96,
        "turnover_avg_20d": 3001.41,
        "available": True,
    }
    ok, msg = _check_north(val)
    assert ok is True


def test_non_dict_value_is_caught():
    """探针返回值不是 dict（比如上游异常导致返回了字符串/None）——
    必须被拦住，不能让类型错误悄悄通过。"""
    ok, msg = _check_north("unexpected string")
    assert ok is False
    assert "非 dict" in msg

    ok2, msg2 = _check_north(None)
    assert ok2 is False
