"""v9.9.19 外汇数据层测试（P3-5 显式取位 / P3-6 降级短 TTL）

为什么单独开一个文件
--------------------
这两条都属于「外汇数据层」的行为，与 `test_model_attribution_labels.py`
（模型归因 + 渲染点是否判 proxy）不是同一类关注点，塞进去会让那个文件越来
越名不副实。

P3-5：`_parse_fx_frame` 的取位必须由「能转数字的列都算」改成显式的买/卖两列。
P3-6：外汇降级/失败结果不能按 1 小时缓存，否则一次降级会把主源钉死一小时。
"""

from __future__ import annotations

import pytest


# =============================================================================
# P3-5：`_parse_fx_frame` 显式取位
# =============================================================================
# akshare 当前版本（实测 akshare/fx/fx_quote.py:42）最后做了
#   temp_df = temp_df[["货币对", "买报价", "卖报价"]]
# 所以帧固定 3 列。旧写法 `cells[1:]` 依赖这个「恰好」；改成 `cells[1:3]`
# 把依赖写死。下面第一条用例就是「akshare 多返回一列」的模拟。


def _frame(columns, row):
    import pandas as pd

    return pd.DataFrame([row], columns=columns)


def test_parse_fx_frame_takes_only_bid_ask_columns():
    """多返回报价列时，中值必须仍是 (买+卖)/2，不能把额外列一起平均。"""
    from services import global_market as gm

    # 模拟 akshare 升版后多出「昨收」「涨跌幅」两列
    df = _frame(
        ["货币对", "买报价", "卖报价", "昨收", "涨跌幅"],
        ["USD/CNY", 6.7110, 6.7120, 6.7000, 0.12],
    )

    mid = gm._parse_fx_frame(df)["USDCNY"]
    assert mid == pytest.approx(6.7115), (
        "中值被额外列污染了：期望 (6.7110+6.7120)/2=6.7115，实际 %r" % mid
    )


def test_parse_fx_frame_extra_columns_would_fail_silently():
    """坐实「旧写法是静默失败」：被污染的值仍落在合法区间内，不会报错。

    这条不是测新行为，是把 P3-5 的动机固化下来 —— 如果有一天有人认为
    「多平均几列无所谓」，请先看这条：污染后的值照样能通过 _sanitize_usdcny。
    """
    from services import global_market as gm

    df = _frame(
        ["货币对", "买报价", "卖报价", "昨收", "涨跌幅"],
        ["USD/CNY", 6.7110, 6.7120, 6.7000, 0.12],
    )
    all_numeric = [6.7110, 6.7120, 6.7000, 0.12]
    polluted = sum(all_numeric) / len(all_numeric)

    low, high = gm._USDCNY_VALID_RANGE
    assert low < polluted < high, (
        "前提变了：污染值 %r 已不在合法区间 %r，这条用例的论据不再成立" % (polluted, (low, high))
    )
    assert gm._sanitize_usdcny(polluted) is not None, "污染值居然被拦下了，说明区间校验够用"
    # 而正确实现不受影响
    assert gm._parse_fx_frame(df)["USDCNY"] == pytest.approx(6.7115)


def test_parse_fx_frame_three_column_frame_unchanged():
    """当前真实帧（3 列）解析结果不变 —— 改 [1:3] 不能破坏正常路径。"""
    from services import global_market as gm

    df = _frame(["货币对", "买报价", "卖报价"], ["USD/CNY", 6.7110, 6.7120])

    assert gm._parse_fx_frame(df)["USDCNY"] == pytest.approx(6.7115)


def test_parse_fx_frame_ignores_trailing_text_column():
    """尾部非数字列仍被跳过（与改动前一致）。"""
    from services import global_market as gm

    df = _frame(["货币对", "买报价", "卖报价", "备注"], ["USD/CNY", 6.7110, 6.7120, "-"])

    assert gm._parse_fx_frame(df)["USDCNY"] == pytest.approx(6.7115)


# =============================================================================
# P3-6：降级/失败结果用短 TTL
# =============================================================================
# 生产证据（2026-09-11）：08:30 落到离岸兜底后被缓存 3600s，锁到 09:30 ——
# 而 09:30 正是在岸开盘、本可恢复成在岸价的时刻（10:20 实测主源正常）。


class _RecordingCache:
    """替身缓存：记录每次 set 的 (key, ttl)，其余行为委托给真 MemoryCache。

    用真 MemoryCache 做父类而不是纯桩，是为了连「ttl 是否真的被传递下去」
    一起覆盖（MemoryCache.set 的 ttl 是 keyword-only，写错不会报 TypeError）。
    """

    def __init__(self, *args, **kwargs) -> None:
        from infra.cache import MemoryCache

        self._real = MemoryCache(*args, **kwargs)
        self.set_calls: list[tuple[str, int]] = []

    def get(self, key):
        return self._real.get(key)

    def set(self, key, value, *, ttl=0):
        self.set_calls.append((key, ttl))
        return self._real.set(key, value, ttl=ttl)


def _run_forex(monkeypatch, *, fx_frame=None, tushare_payload=None):
    """把外汇两个数据源打桩后跑一次 get_forex_data，返回 (结果, 记录型缓存)。"""
    from infra.data_source.macro import indicators
    from services import global_market as gm
    from services import tushare_fallback

    cache = _RecordingCache(default_ttl=gm._GLOBAL_TTL)
    monkeypatch.setattr(gm, "_global_cache", cache)
    monkeypatch.setattr(indicators, "get_fx_spot_quote", lambda: fx_frame)

    class _FakeTushare:
        @staticmethod
        def instance():
            return _FakeTushare()

        def get_forex_data(self):
            return tushare_payload

    monkeypatch.setattr(tushare_fallback, "TusharePrimary", _FakeTushare)

    return gm.get_forex_data(), cache


def test_forex_success_uses_full_ttl(monkeypatch):
    """在岸主源成功 → 仍缓存 1 小时，行为不变。"""
    from services import global_market as gm

    result, cache = _run_forex(
        monkeypatch,
        fx_frame=_frame(["货币对", "买报价", "卖报价"], ["USD/CNY", 6.7110, 6.7120]),
    )

    assert result["available"] is True
    assert result["usdcny"]["source"] == "akshare"
    assert "proxy" not in result["usdcny"]
    assert cache.set_calls == [("forex", gm._GLOBAL_TTL)]


def test_forex_offshore_proxy_uses_short_ttl(monkeypatch):
    """P3-6 核心：离岸 CNH 兜底只能短缓存，否则主源恢复后被钉死一小时。"""
    from services import global_market as gm

    result, cache = _run_forex(
        monkeypatch,
        fx_frame=None,
        tushare_payload={"usdcnh": 6.7138, "date": "20260910"},
    )

    assert result["available"] is True
    assert result["usdcny"]["proxy"] is True
    assert cache.set_calls == [("forex", gm._GLOBAL_TTL_DEGRADED)]
    assert gm._GLOBAL_TTL_DEGRADED < gm._GLOBAL_TTL


def test_forex_total_failure_uses_short_ttl(monkeypatch):
    """全部数据源不可用 → 同样只短缓存（但仍要缓存，不是不缓存）。"""
    from services import global_market as gm

    result, cache = _run_forex(monkeypatch, fx_frame=None, tushare_payload=None)

    assert result["available"] is False
    assert result["degraded"] is True
    assert cache.set_calls == [("forex", gm._GLOBAL_TTL_DEGRADED)]
    # 约束：不要改成「失败不缓存」，那会在上游故障时放大请求压力
    assert gm._GLOBAL_TTL_DEGRADED > 0


def test_success_ttl_constant_not_weakened():
    """约束：_GLOBAL_TTL 只该影响成功路径，不得被顺手调小。"""
    from services import global_market as gm

    assert gm._GLOBAL_TTL == 3600, "成功路径的缓存时长被改了，确认是有意为之？"
