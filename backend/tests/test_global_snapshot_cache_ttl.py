#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B6：快照【文件缓存】必须认降级状态（四个写手统一短 TTL）

为什么单独开一个文件
--------------------
P3-6 已经把 `services/global_market.py` 的**内存缓存**降级 TTL 压到
`_GLOBAL_TTL_DEGRADED`（300s），但**文件缓存层**的四个写手完全不看
`degraded`，于是内存层的修复被文件层架空：

  ┌─ 写手 ─────────────────────────────┬─ cron ──┬─ 正常 TTL ─┐
  │ api/global_market.py global_snapshot│ 按需    │ 4h (14400) │
  │ cache_warmer.warm_after_close()     │ 18:10   │ 18h        │
  │ cache_warmer.warm_morning()         │ 08:45   │ 4h         │
  │ cache_warmer.warm_evening()         │ 18:00   │ 12h        │
  └─────────────────────────────────────┴─────────┴────────────┘

线上实证（2026-09-11）：`data/_cache/global_snapshot.json` 里存着 08:45
写入的**离岸 USD/CNH 兜底价**（`degraded=True`、`proxy=true`、
`rate=6.7138`），被 4h TTL 钉到 12:45；而 11:21 直连 `/api/global/forex`
主源已完全正常（akshare 在岸、`degraded=false`）。用户看到的就是过期离岸
价 —— 主源恢复了，读文件缓存的这条路径还在吐旧值。

其中 `warm_evening()` 嫌疑最大：18:00 跑、ttl=12，覆盖到次日 06:00，正好
罩住 01:10 的晨报生成时刻（2026-09-11 那份缺汇率的晨报就是这么来的）。

本文件把「四个写手统一认 `is_snapshot_degraded()`」钉死。

⚠️ 隔离纪律（本文件的两条硬约束，违反会重演历史事故）
------------------------------------------------------
1. **绝不能写生产 `data/`**。历史事故：跑测试直写 `/opt/moneybag/data`，
   攒出十几个 `test_*` 前缀的脏用户文件。所以每条用例都把
   `config.DATA_DIR`（API 侧）和 `cache_warmer.CACHE_DIR`/`DATA_DIR`
   （预热侧）monkeypatch 到 `tmp_path`。
2. **绝不能真发网络请求**。`warm_*()` 里有 HTTP 预热（选基 40 次组合、
   CFO 摘要）和一堆取数调用，一律打桩掉 —— 见 `_hermetic_warmer()`。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
from api import global_market as api_gm  # noqa: E402
from services import global_market as gm  # noqa: E402

_SNAPSHOT_FILE = "global_snapshot.json"
_MARKER = "b6-degraded-ttl"


# =============================================================================
# 夹具：构造快照 + 隔离目录 + 读回缓存文件
# =============================================================================

def _snapshot(forex: dict) -> dict:
    """构造一份带标记的快照，forex 部分由调用方给。

    带标记是为了能断言「文件里这份数据确实是我们这次调用写的」，而不是
    上一轮残留 —— 否则打桩失败时会静默读到旧文件，测试假通过。
    """
    return {"forex": forex, "_test_marker": _MARKER}


#: 正常态：akshare 在岸主源成功。
NORMAL_SNAPSHOT = _snapshot({
    "available": True,
    "degraded": False,
    "usdcny": {"rate": 6.7115, "source": "akshare", "as_of": "截至 09-11 11:21"},
    "dxy_proxy": 99.11,
})

#: 降级态：主源挂了，走 Tushare 离岸 USD/CNH 兜底（2026-09-11 真实形态）。
DEGRADED_SNAPSHOT = _snapshot({
    "available": True,
    "degraded": True,
    "usdcny": {"rate": 6.7138, "source": "tushare", "proxy": True, "as_of": "09-10 收盘"},
    "dxy_proxy": None,
})


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _span_seconds(payload: dict) -> float:
    """`expires_at - cached_at`，单位秒。

    两个写手（api/global_market.py 与 cache_warmer._save_cache）都把
    `cached_at` 写成 ISO 字符串、`expires_at` 写成 epoch 浮点，所以要先
    把前者解析回时间戳再相减。顺带这条也是格式契约：谁把 cached_at 改回
    浮点，这里会立刻 TypeError。
    """
    cached_at = datetime.fromisoformat(payload["cached_at"]).timestamp()
    return payload["expires_at"] - cached_at


# =============================================================================
# 一、is_snapshot_degraded() 判据
# =============================================================================

def test_normal_snapshot_is_not_degraded():
    """正常态（在岸主源成功）不算降级 → 走长 TTL。"""
    assert gm.is_snapshot_degraded(NORMAL_SNAPSHOT) is False


def test_explicit_degraded_flag_is_degraded():
    """判据 1：`degraded=True` 直接判降级。"""
    assert gm.is_snapshot_degraded(DEGRADED_SNAPSHOT) is True


def test_offshore_proxy_is_degraded():
    """判据 2：汇率值是离岸 USD/CNH proxy，即使 `degraded` 字段缺失也算降级。

    真实形态：兜底分支只给 `usdcny.proxy=True`，不一定同步置 `degraded`。
    """
    snapshot = _snapshot({
        "available": True,
        "usdcny": {"rate": 6.7138, "proxy": True},
    })
    assert gm.is_snapshot_degraded(snapshot) is True


def test_available_false_without_degraded_flag_is_degraded():
    """判据 3：**最关键的一条** —— 汇率整体拿不到但没有 degraded 标记。

    历史证据：2026-09-08/09/10 三天的 precomputed 快照里 forex 是
    `{"available": False, "usdcny": {}, "degraded": None}` —— 汇率完全拿
    不到却没有 `degraded` 标记（旧版字段结构不同）。只判 1、2 会把它当正常
    数据，用 18h TTL 把一份**空汇率**钉死一整晚，恰好是最坏情况。
    """
    snapshot = _snapshot({"available": False, "degraded": None, "usdcny": {}})
    assert gm.is_snapshot_degraded(snapshot) is True


def test_available_true_is_not_degraded():
    """`available=True` 且没有 proxy/degraded → 正常态，不能被判据 3 误伤。"""
    snapshot = _snapshot({"available": True, "degraded": False, "usdcny": {}})
    assert gm.is_snapshot_degraded(snapshot) is False


def test_missing_forex_key_does_not_raise():
    """`forex` 缺失不炸 —— 判据函数要在任意残缺结构上都安全返回。

    预热写手是在 try/except 里调它的，但抛异常会让整段预热被吞掉、连缓存
    都不写，所以「不炸」本身就是要保的行为。
    """
    assert gm.is_snapshot_degraded({}) is False


def test_forex_none_does_not_raise():
    """"forex": None 同缺失，不能炸。"""
    assert gm.is_snapshot_degraded({"forex": None}) is False


def test_none_snapshot_does_not_raise():
    """整个快照是 None 也不能炸（上游取数整体失败的形态）。"""
    assert gm.is_snapshot_degraded(None) is False


# =============================================================================
# 二、API 侧写手：/api/global/snapshot
# =============================================================================

@pytest.fixture
def api_data_dir(tmp_path, monkeypatch):
    """把 API 写手的落盘目录指到 tmp_path，并打桩掉真实取数。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _api_cache_file(api_data_dir: Path) -> Path:
    return api_data_dir / "_cache" / _SNAPSHOT_FILE


def _stub_snapshot(monkeypatch, snapshot: dict) -> None:
    """替换 api 模块内的 get_global_snapshot，返回深拷贝避免常量被改写。

    API 命中文件缓存时会给返回的 dict 加 `from_cache=True`，用深拷贝可以
    保证模块级常量不被污染、用例之间互不干扰。
    """
    monkeypatch.setattr(
        api_gm, "get_global_snapshot", lambda: copy.deepcopy(snapshot)
    )


def test_api_normal_snapshot_uses_4h_ttl(api_data_dir, monkeypatch):
    """正常态：文件缓存仍是 4h，行为不变（不能因为修降级把正常路径改短）。"""
    _stub_snapshot(monkeypatch, NORMAL_SNAPSHOT)

    api_gm.global_snapshot()

    payload = _read_json(_api_cache_file(api_data_dir))
    assert payload["ttl"] == 14400, "正常态 TTL 被改了：%r" % payload["ttl"]
    assert payload["degraded"] is False
    assert _span_seconds(payload) == pytest.approx(14400, abs=2)


def test_api_degraded_snapshot_uses_short_ttl(api_data_dir, monkeypatch):
    """B6 核心：降级态只缓存 300s，主源一恢复就能很快自愈。"""
    _stub_snapshot(monkeypatch, DEGRADED_SNAPSHOT)

    api_gm.global_snapshot()

    payload = _read_json(_api_cache_file(api_data_dir))
    assert payload["ttl"] == gm._GLOBAL_TTL_DEGRADED, (
        "降级态 TTL 应为 _GLOBAL_TTL_DEGRADED(%s)，实测 %r"
        % (gm._GLOBAL_TTL_DEGRADED, payload["ttl"])
    )
    assert payload["degraded"] is True
    assert _span_seconds(payload) == pytest.approx(gm._GLOBAL_TTL_DEGRADED, abs=2)
    # 必须显著短于原来的 4h，否则等于没修
    assert payload["ttl"] < 14400 / 10


def test_api_payload_carries_degraded_and_ttl_fields(api_data_dir, monkeypatch):
    """payload 必须带 degraded/ttl 两个字段 —— 排查时 cat 一眼就能看出
    这份缓存是降级短命的，不用再去 data.forex 里翻 degraded。"""
    _stub_snapshot(monkeypatch, DEGRADED_SNAPSHOT)

    api_gm.global_snapshot()

    payload = _read_json(_api_cache_file(api_data_dir))
    assert "degraded" in payload, "payload 缺 degraded 字段：%r" % sorted(payload)
    assert "ttl" in payload, "payload 缺 ttl 字段：%r" % sorted(payload)
    # 数据本体不能被这两个字段挤掉
    assert payload["data"]["_test_marker"] == _MARKER


def test_api_degraded_cache_expires_then_recomputes(api_data_dir, monkeypatch):
    """降级缓存过期后必须重新走真实计算，并把 TTL 恢复成 4h。

    这是整条自愈链路的闭环：降级 → 短缓存 → 过期 → 重算 → 正常长缓存。
    缺了最后一步，主源恢复后 TTL 会一直是 300s（功能对，但白白多打上游）。
    """
    _stub_snapshot(monkeypatch, DEGRADED_SNAPSHOT)
    api_gm.global_snapshot()

    cache_file = _api_cache_file(api_data_dir)
    assert _read_json(cache_file)["ttl"] == gm._GLOBAL_TTL_DEGRADED

    # 未过期：仍应命中文件缓存（from_cache=True）
    _stub_snapshot(monkeypatch, NORMAL_SNAPSHOT)
    assert api_gm.global_snapshot().get("from_cache") is True, (
        "降级缓存还没过期就不该重算"
    )

    # 把 expires_at 拨到过去，模拟 300s 已过
    payload = _read_json(cache_file)
    payload["expires_at"] = time.time() - 1
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = api_gm.global_snapshot()
    assert "from_cache" not in result, "缓存已过期，应重新计算而非继续命中"

    fresh = _read_json(cache_file)
    assert fresh["data"]["_test_marker"] == _MARKER
    assert fresh["degraded"] is False, "主源已恢复，degraded 应回到 False"
    assert fresh["ttl"] == 14400, "恢复后 TTL 应回到 4h，实测 %r" % fresh["ttl"]


# =============================================================================
# 三、cache_warmer 侧三个写手
# =============================================================================
# warm_*() 是整段整段的业务流程（选股 30-40s、选基 HTTP 40 次组合、CFO
# 摘要 HTTP……），不可能在测试里真跑。这里用「惰性 import 投毒」把它们全部
# 短路掉：把除 services.global_market 之外所有被 warm_* 惰性 import 的模块
# 在 sys.modules 里置成 None，于是 `from services.X import Y` 立刻抛
# ImportError，被各步骤自己的 try/except 吞掉 —— 既不断网也不慢。
#
# 相比「逐个函数打桩」，这个写法的好处是**对未来新增步骤免疫**：以后谁在
# warm_* 里加一段重活，只要它走 `from services.X import` 就自动被短路，
# 不用回头改测试。代价是要保证 global_market 不在投毒名单里 —— 下面那条
# 断言 + 标记校验就是保险丝。

_POISONED_MODULES = (
    "services.signal_scout", "services.stock_screen", "services.market_panorama",
    "services.data_layer", "services.market_data", "services.regime_engine",
    "services.signal", "services.alt_data", "services.stock_monitor",
    "services.fund_monitor", "services.ds_enhance", "services.tushare_data",
    "services.fund_screen", "services.news_data", "services.policy_data",
    "services.precomputed_cache", "services.factor_data",
    "services.sector_rotation", "services.broker_research",
    "services.macro_data", "services.technical", "services.market_factors",
    "services.factor_ic", "services.persistence",
    "api.signals", "api.shared_helpers",
)


def _hermetic_warmer(monkeypatch, tmp_path, snapshot: dict):
    """把 cache_warmer 改造成「只跑 global_snapshot 一步」的离线版本。

    Returns:
        (cache_warmer 模块, 缓存目录 Path)
    """
    import requests
    from scripts import cache_warmer as cw

    assert "services.global_market" not in _POISONED_MODULES, (
        "global_market 被误投毒了，三个写手会一起短路、测试假通过"
    )

    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cw, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cw, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cw, "_is_trading_day", lambda: True)
    monkeypatch.setattr(
        gm, "get_global_snapshot", lambda: copy.deepcopy(snapshot)
    )

    for name in _POISONED_MODULES:
        monkeypatch.setitem(sys.modules, name, None)

    def _no_http(*args, **kwargs):
        raise RuntimeError("测试环境禁止真实 HTTP 请求")

    monkeypatch.setattr(requests, "get", _no_http)
    return cw, cache_dir


def _warmer_ttl_hours(cache_dir: Path) -> dict:
    """读回预热写下的 global_snapshot.json，并校验它确实来自本轮调用。"""
    payload = _read_json(cache_dir / _SNAPSHOT_FILE)
    assert payload["data"]["_test_marker"] == _MARKER, (
        "缓存文件不是本轮写的（marker 不符），打桩可能被短路了"
    )
    return payload


@pytest.mark.parametrize(
    "warmer,expected_hours",
    [("warm_after_close", 18), ("warm_morning", 4), ("warm_evening", 12)],
)
def test_warmer_normal_snapshot_keeps_long_ttl(
    monkeypatch, tmp_path, warmer, expected_hours
):
    """正常态：三个写手各自的 TTL 保持不变（18h / 4h / 12h）。

    别顺手把正常路径也改成短 TTL —— 那会让预热白做，用户打开照旧慢。
    """
    cw, cache_dir = _hermetic_warmer(monkeypatch, tmp_path, NORMAL_SNAPSHOT)

    getattr(cw, warmer)()

    payload = _warmer_ttl_hours(cache_dir)
    assert payload["ttl_hours"] == pytest.approx(expected_hours), (
        "%s 正常态 TTL 应为 %sh，实测 %r" % (warmer, expected_hours, payload["ttl_hours"])
    )
    assert _span_seconds(payload) == pytest.approx(expected_hours * 3600, abs=2)


@pytest.mark.parametrize(
    "warmer", ["warm_after_close", "warm_morning", "warm_evening"]
)
def test_warmer_degraded_snapshot_uses_short_ttl(monkeypatch, tmp_path, warmer):
    """B6 核心：三个预热写手在降级态一律改用 300s（0.0833h）。

    2026-09-11 的故障就是这三处长 TTL 造成的：08:45 的离岸兜底价被钉到
    12:45，而 11:21 主源已恢复。
    """
    cw, cache_dir = _hermetic_warmer(monkeypatch, tmp_path, DEGRADED_SNAPSHOT)

    getattr(cw, warmer)()

    payload = _warmer_ttl_hours(cache_dir)
    expected_hours = gm._GLOBAL_TTL_DEGRADED / 3600
    assert payload["ttl_hours"] == pytest.approx(expected_hours), (
        "%s 降级态 TTL 应为 %sh(300s)，实测 %r"
        % (warmer, expected_hours, payload["ttl_hours"])
    )
    assert _span_seconds(payload) == pytest.approx(gm._GLOBAL_TTL_DEGRADED, abs=2)
    # 0.0833h ≈ 5 分钟：短到不至于钉死，长到不至于放大上游压力
    assert payload["ttl_hours"] < 0.1


def test_evening_warmer_is_the_most_dangerous_one(monkeypatch, tmp_path):
    """把 warm_evening 的「嫌疑最大」固化成可执行的事实。

    warm_evening 的 cron 是 18:00，比 warm_after_close 的 18:10 还早，
    ttl=12 会把降价值一直钉到次日 06:00 —— 正好罩住 01:10 的晨报生成时刻。
    这条用例存在的意义是：以后有人想给 evening 开长 TTL 的后门，会先读到
    这段注释和这条红。
    """
    cw, cache_dir = _hermetic_warmer(monkeypatch, tmp_path, DEGRADED_SNAPSHOT)

    cw.warm_evening()

    payload = _warmer_ttl_hours(cache_dir)
    expires_at = payload["expires_at"]
    # 12h 的话到期时间会落在 6 小时之后；降级 TTL 必须远远不到
    assert expires_at - time.time() < 600, (
        "warm_evening 降级缓存的有效期超过 10 分钟，会罩住晨报生成时刻"
    )


# =============================================================================
# 四、约束：四个写手必须都在
# =============================================================================
# 这类修复最常见的退化方式是「新写手照抄老代码」，所以加一条源码级护栏。

def test_no_writer_left_behind():
    """cache_warmer 里必须有且仅有 3 处认降级状态的 global_snapshot 写入。

    只有 2 处 = 有人漏改（历史上就是 warm_evening 被漏掉）；
    变 4 处 = 新增了写手，请确认它也是同一个判据。
    """
    src = (
        Path(__file__).resolve().parents[1] / "scripts" / "cache_warmer.py"
    ).read_text(encoding="utf-8")

    hits = src.count("is_snapshot_degraded(global_data)")
    assert hits == 3, (
        "cache_warmer 里认降级状态的 global_snapshot 写入应为 3 处，实测 %d 处" % hits
    )


def test_degraded_ttl_constant_not_weakened():
    """约束：降级 TTL 常量本身不能被顺手调大（调大等于把修复废掉）。"""
    assert gm._GLOBAL_TTL_DEGRADED == 300, (
        "_GLOBAL_TTL_DEGRADED 被改成 %r，确认是有意为之？" % gm._GLOBAL_TTL_DEGRADED
    )
    assert gm._GLOBAL_TTL_DEGRADED < 3600, "降级 TTL 不该接近正常 TTL"
