"""v9.9.20 A3-B：外汇巡检的「非交易时段」识别

背景
----
银行间人民币外汇即期（CFETS）交易时段是**北京时间 9:30 - 次日 3:00**，
周六、周日及法定节假日不开市（来源：中国货币网 chinamoney.org.cn
「人民币外汇即期」产品页，也就是 akshare fx_spot_quote 的上游）。

所以「非交易时段」= 每天 03:00-09:30 的盘面重置空档 + 整个周六周日。
这个区间拿不到在岸报价是预期行为，不该天天告警。

⚠️ 一个必须钉住的事实：**凌晨 01:20 的巡检落在交易时段内**（夜盘开到次日
03:00），不是「非交易时段」。团队最初的判断是后者，与官方时段和实测都不符
——2026-09-11 02:30 的巡检记录是 ✅ USD/CNY=6.712（来源 akshare）。
所以这条规则不能写成「9:30-23:30」，否则凌晨的真故障会被静音。

实测佐证（2026-09-11）：
  08:30（空档内）→ 在岸主源不可用，落到离岸 USD/CNH 兜底 6.7138
  10:58（开盘后）→ 在岸主源正常，USD/CNY=6.7109
  02:30（夜盘内）→ 巡检 ✅ 通过
"""

from __future__ import annotations

from datetime import datetime

import pytest


def _hours():
    from scripts import datasource_health_check as hc

    return hc._is_fx_trading_hours


@pytest.mark.parametrize(
    "when,expected,why",
    [
        # ── 开盘后（日盘）──
        (datetime(2026, 9, 11, 9, 30), True, "周五 09:30 开盘"),
        (datetime(2026, 9, 11, 10, 58), True, "实测此时主源正常"),
        (datetime(2026, 9, 11, 23, 59), True, "夜盘仍在场内"),
        # ── 跨零点夜盘 ──
        (datetime(2026, 9, 12, 0, 30), True, "跨零点，属周五夜盘"),
        (datetime(2026, 9, 11, 1, 20), True, "⚠️ 巡检时点，在夜盘内，不是非交易时段"),
        (datetime(2026, 9, 11, 2, 30), True, "实测 02:30 巡检 ✅ 通过"),
        (datetime(2026, 9, 12, 2, 59), True, "收市前最后一分钟"),
        # ── 每日 03:00-09:30 重置空档 ──
        (datetime(2026, 9, 11, 3, 0), False, "03:00 盘面重置"),
        (datetime(2026, 9, 11, 8, 30), False, "实测此时主源不可用、走离岸兜底"),
        (datetime(2026, 9, 11, 9, 29), False, "开盘前一分钟"),
        # ── 周末 ──
        (datetime(2026, 9, 12, 10, 0), False, "周六休市"),
        (datetime(2026, 9, 13, 10, 0), False, "周日休市"),
        (datetime(2026, 9, 13, 1, 0), False, "周日凌晨：前一交易日是周六，休市"),
        (datetime(2026, 9, 14, 1, 0), False, "周一凌晨：前一交易日是周日，休市"),
        # ── 跨零点归属：周六 01:00 属周五夜盘，应当开市 ──
        (datetime(2026, 9, 12, 1, 0), True, "周六凌晨属周五夜盘，不应被误判休市"),
    ],
)
def test_is_fx_trading_hours(when, expected, why):
    assert _hours()(when) is expected, "%s 判成了 %s（期望 %s）" % (
        why,
        _hours()(when),
        expected,
    )


def test_fx_check_skips_instead_of_alerting_off_hours(monkeypatch):
    """非交易时段拿不到在岸报价 → 判为「跳过」，不进告警列表。"""
    from scripts import datasource_health_check as hc

    monkeypatch.setattr(hc, "_is_fx_trading_hours", lambda *a, **k: False)

    class _FakeGM:
        @staticmethod
        def get_forex_data():
            return {"usdcny": None, "dxy_proxy": None, "available": False}

    import sys
    import types

    fake = types.ModuleType("services.global_market")
    fake.get_forex_data = _FakeGM.get_forex_data
    monkeypatch.setitem(sys.modules, "services.global_market", fake)

    result = hc._check_forex({"name": "外汇(USD/CNY)"})

    assert result["ok"] is True, "非交易时段不该判失败"
    assert result.get("status") == "⏭️", "应标记为跳过而不是 ✅/❌"
    assert "非交易时段" in result["detail"]


def test_fx_check_still_alerts_off_hours_on_type_error(monkeypatch):
    """代码级异常（返回值类型不对）即便在非交易时段也要报 —— 那是 bug 不是行情。"""
    from scripts import datasource_health_check as hc

    monkeypatch.setattr(hc, "_is_fx_trading_hours", lambda *a, **k: False)

    import sys
    import types

    fake = types.ModuleType("services.global_market")
    fake.get_forex_data = lambda: "not-a-dict"
    monkeypatch.setitem(sys.modules, "services.global_market", fake)

    result = hc._check_forex({"name": "外汇(USD/CNY)"})

    assert result["ok"] is False, "返回值类型异常是真故障，非交易时段也不该放过"


def test_fx_check_still_alerts_in_trading_hours(monkeypatch):
    """反向：交易时段缺数据必须照常判失败，不能被「非交易时段」规则吃掉。"""
    from scripts import datasource_health_check as hc

    monkeypatch.setattr(hc, "_is_fx_trading_hours", lambda *a, **k: True)

    import sys
    import types

    fake = types.ModuleType("services.global_market")
    fake.get_forex_data = lambda: {"usdcny": None, "available": False}
    monkeypatch.setitem(sys.modules, "services.global_market", fake)

    result = hc._check_forex({"name": "外汇(USD/CNY)"})

    assert result["ok"] is False, "交易时段缺数据是真故障，必须告警"
    assert result.get("status") != "⏭️"


def test_fx_offshore_proxy_skips_off_hours(monkeypatch):
    """非交易时段落到离岸兜底是预期路径，不该天天误报。"""
    from scripts import datasource_health_check as hc

    monkeypatch.setattr(hc, "_is_fx_trading_hours", lambda *a, **k: False)

    import sys
    import types

    fake = types.ModuleType("services.global_market")
    fake.get_forex_data = lambda: {
        "usdcny": {"rate": 6.7138, "name": "USD/CNH(离岸,代理USD/CNY)",
                   "source": "tushare", "proxy": True, "as_of": "09-10 收盘"},
        "available": True,
    }
    monkeypatch.setitem(sys.modules, "services.global_market", fake)

    result = hc._check_forex({"name": "外汇(USD/CNY)"})

    assert result["ok"] is True
    assert result.get("status") == "⏭️"
    assert "非交易时段" in result["detail"]


def test_fx_offshore_proxy_still_alerts_in_trading_hours(monkeypatch):
    """反向：交易时段主源降级仍是故障，必须告警（2026-09-11 的病根就是没报）。"""
    from scripts import datasource_health_check as hc

    monkeypatch.setattr(hc, "_is_fx_trading_hours", lambda *a, **k: True)

    import sys
    import types

    fake = types.ModuleType("services.global_market")
    fake.get_forex_data = lambda: {
        "usdcny": {"rate": 6.7138, "name": "USD/CNH(离岸,代理USD/CNY)",
                   "source": "tushare", "proxy": True, "as_of": "09-10 收盘"},
        "available": True,
    }
    monkeypatch.setitem(sys.modules, "services.global_market", fake)

    result = hc._check_forex({"name": "外汇(USD/CNY)"})

    assert result["ok"] is False
    assert "主源降级" in result["detail"]
