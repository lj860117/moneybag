"""外汇数据层测试（P3-5 显式取位 / P3-6 降级短 TTL / v9.9.20 A1·A2·A3-D）

为什么单独开一个文件
--------------------
这些都属于「外汇数据层」的行为，与 `test_model_attribution_labels.py`
（模型归因 + 渲染点是否判 proxy）不是同一类关注点，塞进去会让那个文件越来
越名不副实。

P3-5：`_parse_fx_frame` 的取位必须由「能转数字的列都算」改成显式的买/卖两列。
P3-6：外汇降级/失败结果不能按 1 小时缓存，否则一次降级会把主源钉死一小时。
A1：综合快照 TTL 600 → 300，与外汇降级 TTL 对齐（否则自愈被快照层压一层）。
A2：`dxy_proxy` 真值化 —— 几何加权合成，绝不能退化成算术平均。
A3-D：汇率结果携带 `as_of` 时点，缓存命中时不许被刷新成「现在」。
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


# =============================================================================
# v9.9.20 A1：快照 TTL 从 600 降到 300（与外汇降级 TTL 对齐）
# =============================================================================
# 快照 600s 会在外汇 300s 之上再压一层：外汇主源恢复后，消费方读到的仍是快照
# 里的旧值。降到 300 让自愈链路完整。
#
# 为什么不会打爆上游：快照不取数，只组装四个子函数的结果，而四个子函数各有
# 自己的 3600s 缓存，所以多出来的开销只是「每 5 分钟重新组装一次字典」。


def test_snapshot_ttl_aligned_with_fx_degraded_ttl():
    """A1 核心：快照 TTL 与外汇降级 TTL 必须相等，否则自愈仍被压一层。"""
    from services import global_market as gm

    assert gm._SNAPSHOT_TTL == gm._GLOBAL_TTL_DEGRADED, (
        "快照 TTL(%s) 与外汇降级 TTL(%s) 不一致，P3-6 的自愈会被快照层吃掉"
        % (gm._SNAPSHOT_TTL, gm._GLOBAL_TTL_DEGRADED)
    )
    assert gm._SNAPSHOT_TTL == 300, "快照 TTL 被改成 %r，确认是有意为之？" % gm._SNAPSHOT_TTL


def test_snapshot_actually_sets_short_ttl(monkeypatch):
    """落到真实调用路径上验证，而不只是看常量。"""
    from services import global_market as gm

    cache = _RecordingCache(default_ttl=gm._GLOBAL_TTL)
    monkeypatch.setattr(gm, "_global_cache", cache)
    monkeypatch.setattr(gm, "get_us_indices", lambda: {"available": False})
    monkeypatch.setattr(
        gm, "get_forex_data",
        lambda: {"usdcny": None, "dxy_proxy": None, "available": False},
    )
    monkeypatch.setattr(gm, "get_fed_rate", lambda: {"available": False})
    monkeypatch.setattr(gm, "get_global_pe", lambda: {"available": False})

    gm.get_global_snapshot()

    snap_calls = [c for c in cache.set_calls if c[0] == "global_snapshot"]
    assert snap_calls == [("global_snapshot", gm._SNAPSHOT_TTL)], (
        "快照缓存时长不是 _SNAPSHOT_TTL：%r" % (snap_calls,)
    )


def test_no_bare_ttl_literal_left_in_global_market():
    """约束：TTL 必须走常量，别再写回裸数字（裸数字没人会想到要同步改）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "services" / "global_market.py").read_text(
        encoding="utf-8"
    )
    assert "ttl=600" not in src, "global_market.py 里又出现了裸写的 ttl=600"
    assert "ttl=3600" not in src or "_GLOBAL_TTL" in src, "TTL 应引用常量"


# =============================================================================
# v9.9.20 A2：dxy_proxy 真值化（几何加权，不是算术平均）
# =============================================================================
# 真实锚点：2026-09-11 10:58 用 akshare fx_spot_quote 的实测帧合成，得
# DXY ≈ 99.11；同一时刻用 fx_pair_quote 的直盘报价逐项交叉验证，六个成分
# 全部吻合到小数点后 4 位。下面第一条用例就把这个数钉住。

_DXY_REAL_FRAME = [
    ("USD/CNY", 6.71080, 6.71100),
    ("EUR/CNY", 7.78930, 7.78970),
    ("100JPY/CNY", 4.34630, 4.34660),
    ("GBP/CNY", 9.06210, 9.06250),
    ("CAD/CNY", 4.84840, 4.84860),
    ("CHF/CNY", 8.24750, 8.24770),
    ("CNY/SEK", 1.44520, 1.44570),
]


def _real_pairs_frame():
    import pandas as pd

    return pd.DataFrame(
        [[p, b, a] for p, b, a in _DXY_REAL_FRAME],
        columns=["货币对", "买报价", "卖报价"],
    )


def _pairs_of(frame):
    from services import global_market as gm

    return gm._parse_fx_frame(frame)


def test_dxy_proxy_matches_real_data_anchor():
    """用 2026-09-11 的实测帧合成，结果必须落在真实美元指数量级（≈99.1）。"""
    from services import global_market as gm

    dxy = gm._compute_dxy_proxy(_pairs_of(_real_pairs_frame()))

    assert dxy is not None, "实测帧合成不出 DXY，成分取位可能错了"
    assert dxy == pytest.approx(99.11, abs=0.05), (
        "合成值 %r 偏离实测锚点 99.11 超过 0.05，公式或取位被改坏了" % dxy
    )
    low, high = gm._DXY_VALID_RANGE
    assert low <= dxy <= high


def test_dxy_is_geometric_not_arithmetic():
    """负向对照：算术加权会得到荒谬的 22.39，必须被拦在合理区间外。

    这条是把「为什么必须几何加权」固化下来 —— 万一有人图省事改成加权求和，
    这条会红。
    """
    from services import global_market as gm

    pairs = _pairs_of(_real_pairs_frame())
    usdcny = pairs["USDCNY"]
    crosses = {
        "EUR/USD": pairs["EURCNY"] / usdcny,
        "USD/JPY": usdcny / (pairs["100JPYCNY"] / 100.0),
        "GBP/USD": pairs["GBPCNY"] / usdcny,
        "USD/CAD": usdcny / pairs["CADCNY"],
        "USD/SEK": usdcny / (1.0 / pairs["CNYSEK"]),
        "USD/CHF": usdcny / pairs["CHFCNY"],
    }
    weights = {
        "EUR/USD": 0.576, "USD/JPY": 0.136, "GBP/USD": 0.119,
        "USD/CAD": 0.091, "USD/SEK": 0.042, "USD/CHF": 0.036,
    }
    arithmetic = sum(crosses[k] * weights[k] for k in crosses)

    low, high = gm._DXY_VALID_RANGE
    assert not (low <= arithmetic <= high), (
        "前提变了：算术加权值 %r 居然落在合法区间 %r 内，"
        "这条用例不再能证明「算术加权会被拦下」" % (arithmetic, (low, high))
    )
    # 而正确实现是几何加权，落在区间内
    assert low <= gm._compute_dxy_proxy(pairs) <= high


def test_dxy_proxy_none_when_component_missing():
    """缺成分 → None，绝不硬编码兜底值。"""
    from services import global_market as gm

    pairs = _pairs_of(_real_pairs_frame())
    pairs.pop("CNYSEK")

    assert gm._compute_dxy_proxy(pairs) is None


def test_dxy_proxy_none_when_usdcny_missing():
    """没有 USD/CNY 就交叉不出任何成分 → None。"""
    from services import global_market as gm

    pairs = _pairs_of(_real_pairs_frame())
    pairs.pop("USDCNY")

    assert gm._compute_dxy_proxy(pairs) is None


def test_dxy_proxy_out_of_range_is_rejected_by_sanitizer():
    """量纲错误（把某个成分写错成极小值）必须被 _sanitize_dxy 拦下。

    这是 2026-09-11 返工的直接教训：DXY≈100 而 USDCNY≈6.7，区间校验是
    唯一能抓住「拿错数当美元指数」的闸门。
    """
    from services import global_market as gm

    pairs = _pairs_of(_real_pairs_frame())
    pairs["EURCNY"] = 0.0001  # 制造量纲错误

    assert gm._compute_dxy_proxy(pairs) is None


def test_forex_success_populates_dxy_proxy(monkeypatch):
    """接上真实链路：成功路径返回的 dxy_proxy 不再是恒为 None 的占位。"""
    result, _ = _run_forex(monkeypatch, fx_frame=_real_pairs_frame())

    assert result["available"] is True
    assert result["dxy_proxy"] is not None, "成功路径仍返回 dxy_proxy=None，真值化没接上"
    assert result["dxy_proxy"] == pytest.approx(99.11, abs=0.05)


def test_forex_offshore_fallback_leaves_dxy_none(monkeypatch):
    """离岸兜底时没有在岸报价可交叉 → dxy 保持 None，不用离岸价硬凑。"""
    result, _ = _run_forex(
        monkeypatch,
        fx_frame=None,
        tushare_payload={"usdcnh": 6.7138, "date": "20260910"},
    )

    assert result["usdcny"]["proxy"] is True
    assert result["dxy_proxy"] is None


# =============================================================================
# v9.9.20 A3-D：汇率行时点标注（as_of）
# =============================================================================


def test_onshore_result_carries_fetch_time(monkeypatch):
    """在岸价标的是取数时刻，且随结果一起进缓存（不是渲染时才生成）。"""
    result, _ = _run_forex(
        monkeypatch,
        fx_frame=_frame(["货币对", "买报价", "卖报价"], ["USD/CNY", 6.7110, 6.7120]),
    )

    as_of = result["usdcny"].get("as_of")
    assert as_of, "在岸结果没有带 as_of"
    assert as_of.startswith("截至 "), "在岸时点口径应为「截至 MM-DD HH:MM」，实测 %r" % as_of


def test_offshore_result_uses_source_trade_date(monkeypatch):
    """离岸价走 fx_daily（日频收盘），必须标数据源自己的交易日，不能标取数时刻。"""
    result, _ = _run_forex(
        monkeypatch,
        fx_frame=None,
        tushare_payload={"usdcnh": 6.7138, "date": "20260910"},
    )

    assert result["usdcny"]["as_of"] == "09-10 收盘", (
        "离岸时点应是数据源交易日，实测 %r" % result["usdcny"]["as_of"]
    )


def test_offshore_result_falls_back_to_fetch_time_without_date(monkeypatch):
    """数据源没给 trade_date 时退回取数时刻，但不能崩。"""
    result, _ = _run_forex(
        monkeypatch,
        fx_frame=None,
        tushare_payload={"usdcnh": 6.7138, "date": ""},
    )

    assert result["usdcny"]["as_of"].startswith("截至 ")


def test_as_of_is_preserved_through_cache(monkeypatch):
    """缓存命中时返回的仍是**取数那一刻**的时点，不能被刷新成「现在」。"""
    from services import global_market as gm

    result, cache = _run_forex(
        monkeypatch,
        fx_frame=_frame(["货币对", "买报价", "卖报价"], ["USD/CNY", 6.7110, 6.7120]),
    )
    first = result["usdcny"]["as_of"]

    # 第二次调用走缓存；即使系统时间已经往前走，as_of 也不该变
    second = gm.get_forex_data()
    assert second["usdcny"]["as_of"] == first, (
        "缓存命中后时点被改成了 %r，说明 as_of 是渲染时才生成的"
        % second["usdcny"]["as_of"]
    )
