"""P1-9 置信度不足闸门（confidence gate）回归测试。

核心准则（与 P1-1 / P1-2 同源）：「没数据 / 数据不足」必须可见，绝不能长得
像「判断为中性」或「判断为看多」。之前置信度 35（多空分歧）照样输出
「↗️ 偏多」——把"模型自己承认没看懂"伪装成"判断出来了"，这是比不输出
更严重的假信号。

本文件覆盖：
  1. 低置信度 → 非方向性「数据不足」，且全文不含任何方向词
  2. 高置信度 → 正常输出方向（对照，防回退）
  3. 边界 49 / 50 / 51 —— 确认 50 是分界且边界不抖动
  4. 置信度缺失 / None / NaN / 非数字 → 按**不足**处理（缺失 ≠ 高置信度）
  5. 排序：低置信度高分不得排在「高置信度略低分」之前
  6. 负面控制：源码里不得出现把置信度向上取整 / 抹平的写法

全部离线，不发起任何网络请求。
"""
import math
import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config                      # noqa: E402
from api.signals import (          # noqa: E402
    apply_confidence_gate,
    demote_low_confidence,
    is_confidence_sufficient,
)

# 任何"方向性"字样：出现即视为把数据不足伪装成了判断
DIRECTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "增持", "减持", "建仓", "清仓", "止盈", "止损",
    "看多", "看空", "偏多", "偏空", "做多", "做空", "上涨", "下跌", "走强", "走弱",
    "强势", "弱势", "利好", "利空", "推荐",
)
# 方向性图标/箭头
DIRECTION_ICONS = ("↗", "↘", "↑", "↓", "🟢", "🔴", "📈", "📉")


def _assert_no_direction(text: str, where: str = ""):
    for w in DIRECTION_WORDS:
        assert w not in text, f"{where} 出现方向词「{w}」：{text}"
    for ic in DIRECTION_ICONS:
        assert ic not in text, f"{where} 出现方向性图标「{ic}」：{text}"


def _gated(conf, score=30, direction="up", label="↗️ 偏多"):
    """构造一个"原本会输出看多"的标的，过闸门后返回。"""
    item = {
        "code": "000001",
        "trend_direction": direction,
        "trend_label": label,
        "trend_score": score,
        "trend_confidence": conf,
        "trend_reason": "强动量·低位起势",
    }
    return apply_confidence_gate(item)


# ════════════════════════════════════════════════════════════
# 1. 低置信度 → 「数据不足」，且不含任何方向词
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("conf", [0, 1, 12, 34, 35, 45, 49, 49.9])
def test_low_confidence_outputs_insufficient_data(conf):
    f = _gated(conf)
    assert f["trend_direction"] == config.INSUFFICIENT_DATA_DIRECTION == "unknown"
    assert "数据不足" in f["trend_label"]
    assert f["trend_confidence_sufficient"] is False
    # 原始方向性表述必须被清掉
    assert f["trend_direction"] not in ("up", "down", "flat")
    # 带符号分数不能再当"看多强度"展示，但原始值要保留
    assert f["trend_score"] is None
    assert f["trend_score_raw"] == 30
    # 结论文案（label + reason）里不得出现任何方向词/图标
    _assert_no_direction(f["trend_label"] + f["trend_reason"], f"conf={conf}")


def test_low_confidence_keeps_raw_confidence_number():
    """原始置信度数值必须保留输出 —— 用户可以自己判断，只是不替他翻译成方向。"""
    for conf in (0, 12, 35, 45, 49):
        f = _gated(conf)
        assert f["trend_confidence"] == conf, f"置信度被篡改: {conf} -> {f['trend_confidence']}"
        assert str(conf) in f["trend_reason"]


def test_low_confidence_reason_states_threshold():
    f = _gated(35)
    assert str(config.MIN_CONFIDENCE_FOR_DIRECTION) in f["trend_reason"]


# ════════════════════════════════════════════════════════════
# 2. 高置信度 → 正常输出方向（对照，防回退）
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("conf", [50, 51, 55, 72, 85, 100])
def test_sufficient_confidence_keeps_direction(conf):
    f = _gated(conf)
    assert f["trend_direction"] == "up"
    assert f["trend_label"] == "↗️ 偏多"
    assert f["trend_score"] == 30
    assert f["trend_confidence"] == conf
    assert f["trend_confidence_sufficient"] is True
    assert f["trend_reason"] == "强动量·低位起势"


def test_sufficient_confidence_down_direction_not_changed():
    f = _gated(72, score=-40, direction="down", label="↘️ 偏空")
    assert f["trend_direction"] == "down"
    assert f["trend_label"] == "↘️ 偏空"
    assert f["trend_score"] == -40


# ════════════════════════════════════════════════════════════
# 3. 边界：49 / 50 / 51 —— 50 必须是分界且不抖动
# ════════════════════════════════════════════════════════════

def test_threshold_value_is_fifty():
    assert config.MIN_CONFIDENCE_FOR_DIRECTION == 50


def test_boundary_49_50_51():
    assert is_confidence_sufficient(49) is False
    assert is_confidence_sufficient(50) is True
    assert is_confidence_sufficient(51) is True

    assert _gated(49)["trend_direction"] == "unknown"
    assert _gated(50)["trend_direction"] == "up"
    assert _gated(51)["trend_direction"] == "up"


def test_boundary_is_exactly_the_constant():
    """分界点必须等于常量本身，不能是写死的 50 与常量脱钩。"""
    t = config.MIN_CONFIDENCE_FOR_DIRECTION
    assert is_confidence_sufficient(t - 1) is False
    assert is_confidence_sufficient(t) is True
    assert is_confidence_sufficient(t + 1) is True


def test_threshold_move_is_respected(monkeypatch):
    """把常量整体挪走时，分界点跟着挪（证明代码真的读常量，不是写死的 50）。"""
    monkeypatch.setattr(config, "MIN_CONFIDENCE_FOR_DIRECTION", 80, raising=True)
    assert is_confidence_sufficient(55) is False
    assert is_confidence_sufficient(85) is True
    # signals 里引用的是 config 的属性，monkeypatch 后同样生效
    f = _gated(55)
    assert f["trend_direction"] == "unknown"
    f2 = _gated(85)
    assert f2["trend_direction"] == "up"


# ════════════════════════════════════════════════════════════
# 4. 缺失 / None / NaN / 非数字 → 按不足处理（缺失 ≠ 高置信度）
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", [None, float("nan"), "N/A", "", "unknown", {}, [], object()])
def test_missing_or_invalid_confidence_is_insufficient(bad):
    assert is_confidence_sufficient(bad) is False, f"{bad!r} 被当成了高置信度"
    f = _gated(bad)
    assert f["trend_direction"] == "unknown"
    assert f["trend_confidence_sufficient"] is False
    _assert_no_direction(f["trend_label"] + f["trend_reason"], f"conf={bad!r}")


def test_absent_confidence_key_is_insufficient():
    """字典里根本没有 trend_confidence 这个键 —— 同样按不足处理。"""
    item = {"code": "000002", "trend_direction": "up", "trend_label": "↗️ 偏多", "trend_score": 40}
    apply_confidence_gate(item)
    assert item["trend_direction"] == "unknown"
    assert item["trend_confidence_sufficient"] is False
    assert "未知" in item["trend_reason"]
    _assert_no_direction(item["trend_label"] + item["trend_reason"])


def test_numeric_string_confidence_is_parsed_not_dropped():
    """字符串数字要能被解析，而不是一律判为缺失。"""
    assert is_confidence_sufficient("72") is True
    assert is_confidence_sufficient("35") is False


# ════════════════════════════════════════════════════════════
# 5. 排序：低置信度高分不得排在「高置信度略低分」之前
# ════════════════════════════════════════════════════════════

def test_low_confidence_high_score_is_demoted():
    items = [
        {"code": "A", "score": 95, "trend_confidence": 35, "trend_score": 60},   # 分高但没看懂
        {"code": "B", "score": 88, "trend_confidence": 85, "trend_score": 40},   # 分略低但看得懂
    ]
    demote_low_confidence(items)
    assert [x["code"] for x in items] == ["B", "A"], \
        "低置信度标的靠高分占了推荐首位"


def test_demote_is_stable_within_group():
    """分区是稳定的：同组内保持原有顺序，不能借机乱序。"""
    items = [
        {"code": "h1", "trend_confidence": 85},
        {"code": "h2", "trend_confidence": 55},
        {"code": "l1", "trend_confidence": 35},
        {"code": "l2", "trend_confidence": None},
        {"code": "h3", "trend_confidence": 72},
        {"code": "l3", "trend_confidence": 49},
    ]
    demote_low_confidence(items)
    assert [x["code"] for x in items] == ["h1", "h2", "h3", "l1", "l2", "l3"]


def test_demote_handles_empty_and_missing_key():
    assert demote_low_confidence([]) == []
    items = [{"code": "x"}]          # 完全没算过置信度
    demote_low_confidence(items)
    assert [x["code"] for x in items] == ["x"]


def test_top1_is_not_low_confidence_in_realistic_ranking():
    """复现报告场景：Top1 挂着"数据不够"却因为分数高排第一。"""
    items = [
        {"code": "TOP_HIGH_SCORE", "score": 99, "trend_confidence": 45},
        {"code": "MID", "score": 90, "trend_confidence": 72},
        {"code": "LOW", "score": 70, "trend_confidence": 85},
    ]
    demote_low_confidence(items)
    top = items[0]
    assert is_confidence_sufficient(top["trend_confidence"]), \
        f"推荐首位 {top['code']} 置信度只有 {top['trend_confidence']}"


# ════════════════════════════════════════════════════════════
# 6. 负面控制：不得出现把置信度向上取整 / 抹平的写法
# ════════════════════════════════════════════════════════════

def test_no_upward_rounding_of_confidence_in_source():
    """源码扫描：全文件里，任何提到置信度的行都不许用 max()/ceil() 兜底取值。

    只禁"向上托底"这一类（max / ceil / `or 50` / `or 100`）。不用 `round(`
    做全文件特征：文件里另有 `round(abs(timing_score - 50)/50, 2)` 这类
    大盘时机的置信度**计算**（精度取整，方向是把数值算出来，不是往上抹平），
    误伤它没有意义；针对 round 的检查放在下面 test_gate_source_has_no_*
    里，只扫闸门自身的源码。
    """
    src = (BACKEND_DIR / "api" / "signals.py").read_text(encoding="utf-8")
    bad_lines = []
    for i, line in enumerate(src.splitlines(), 1):
        low = line.lower()
        if "conf" not in low:
            continue
        if re.search(r"\bmax\s*\(|\bceil\s*\(|or\s+50\b|or\s+100\b|\bor\s+1\.0\b", low):
            bad_lines.append(f"{i}: {line.strip()}")
    assert not bad_lines, "发现把置信度向上抹平的写法:\n" + "\n".join(bad_lines)


def test_gate_source_has_no_rounding_or_floor():
    """闸门自身源码里连 round 都不许有 —— 置信度进来什么样出去就什么样。"""
    import inspect
    from api import signals
    parts = [
        inspect.getsource(signals.is_confidence_sufficient),
        inspect.getsource(signals.apply_confidence_gate),
        inspect.getsource(signals.demote_low_confidence),
    ]
    for p in parts:
        assert "round(" not in p
        assert "max(" not in p
        assert "ceil(" not in p
        assert " or 50" not in p
        assert " or 100" not in p


def test_confidence_is_never_inflated_by_gate():
    """行为层面：任何低于阈值的置信度过闸门后数值必须逐字不变。"""
    for conf in (0, 1, 20, 35, 44, 45, 48, 49, 49.5, 49.99):
        f = _gated(conf)
        assert f["trend_confidence"] == conf
        assert f["trend_confidence"] < config.MIN_CONFIDENCE_FOR_DIRECTION
        assert f["trend_confidence_sufficient"] is False


def test_gate_does_not_touch_other_fields():
    f = _gated(35)
    # 除方向性三件套外，其余字段不得被顺手改掉
    assert f["code"] == "000001"
    assert f["trend_reason"] != "强动量·低位起势"   # 唯一被改写的非方向字段
    assert set(f) >= {
        "code", "trend_direction", "trend_label", "trend_score",
        "trend_confidence", "trend_reason", "trend_score_raw",
        "trend_confidence_sufficient",
    }


def test_gate_is_idempotent():
    """重复施加闸门不得把「数据不足」再翻转回方向。"""
    f = _gated(35)
    apply_confidence_gate(f)
    apply_confidence_gate(f)
    assert f["trend_direction"] == "unknown"
    assert f["trend_confidence"] == 35


# ════════════════════════════════════════════════════════════
# 7. 端到端：真实富化函数确实接了闸门
# ════════════════════════════════════════════════════════════

def _sample_funds():
    return [{
        "code": "110020", "name": "沪深300",
        "returns": {"3m": 12.0, "6m": 20.0, "1y": 30.0},
        "nav_percentile": 60,
        "timing_label": "中性", "industry_tag": "宽基",
        "total_score": 80, "max_drawdown": -15.0, "sharpe": 1.2,
    }]


def test_enrich_trend_forecast_wires_gate(monkeypatch):
    """_enrich_trend_forecast 必须产出自查标记（证明闸门真的接上了）。"""
    from api import signals
    monkeypatch.setattr(signals, "_check_qdii_purchase_status", lambda funds: None, raising=True)
    funds = _sample_funds()
    signals._enrich_trend_forecast(funds)
    f = funds[0]
    assert "trend_confidence_sufficient" in f
    conf = f.get("trend_confidence")
    if is_confidence_sufficient(conf):
        assert f["trend_direction"] in ("up", "down", "flat")
    else:
        assert f["trend_direction"] == "unknown"
        _assert_no_direction(f["trend_label"] + f["trend_reason"])


def test_enrich_trend_forecast_all_insufficient_when_threshold_raised(monkeypatch):
    """把阈值抬到 100：全量标的都必须降级为「数据不足」且无方向词。"""
    from api import signals
    monkeypatch.setattr(signals, "_check_qdii_purchase_status", lambda funds: None, raising=True)
    monkeypatch.setattr(config, "MIN_CONFIDENCE_FOR_DIRECTION", 100, raising=True)
    funds = _sample_funds()
    signals._enrich_trend_forecast(funds)
    f = funds[0]
    assert f["trend_direction"] == "unknown"
    assert f["trend_confidence_sufficient"] is False
    assert f["trend_score"] is None
    _assert_no_direction(f["trend_label"] + f["trend_reason"])


def test_enrich_stock_trend_forecast_wires_gate(monkeypatch):
    from api import signals
    stocks = [{
        "code": "600519", "name": "贵州茅台",
        "returns": {"20d": 8.0}, "timing_label": "中性",
        "industry": "白酒", "total_score": 80,
    }]
    signals._enrich_stock_trend_forecast(stocks)
    s = stocks[0]
    assert "trend_confidence_sufficient" in s
    if not is_confidence_sufficient(s.get("trend_confidence")):
        assert s["trend_direction"] == "unknown"
        _assert_no_direction(s["trend_label"] + s["trend_reason"])
