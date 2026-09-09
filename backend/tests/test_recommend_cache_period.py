"""
推荐预计算缓存带 period 维度 — 回归测试
========================================

背景（2026-09-09）：`/api/recommend/stocks` 的预计算缓存用了一个 period
无关的 key ``recommendations``，于是：

  - 无论请求 `?period=short|medium|long`，读到的都是同一份缓存；
  - 三个持有周期返回完全相同的列表（`recommend_engine` 里接好的
    `active_weights` 周期权重在 HTTP 层被缓存拦腰截断，对前端不可见）。

修复：`precomputed_cache.recommend_cache_key(period)` 把缓存键做成 period
维度 —— medium 沿用 legacy 键 ``recommendations``（night_worker 03:00 预计算
仍写这个键，保持预计算命中），short/long 用 ``recommendations_short`` /
``recommendations_long``。

本文件锁定三件事：
 1. **键映射写死**：short/medium/long 各归各键，非法值兜底到 medium；
 2. **三键互不串味**：short/long 绝不回退到中线（legacy）那份缓存；
 3. **三键 TTL 一致**：短线/长线不会悄悄掉到默认 2h，和中线同为 12h。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

import api.misc as misc
import config as config_mod
from services import precomputed_cache as pc
from services import recommend_engine as re_mod


# ============================================================
# 1. 键映射写死
# ============================================================
def test_recommend_cache_key_short_and_long_have_period_suffix() -> None:
    """短线/长线必须用各自的键，不能和 medium 混用。"""
    assert pc.recommend_cache_key("short") == "recommendations_short"
    assert pc.recommend_cache_key("long") == "recommendations_long"


def test_recommend_cache_key_medium_uses_legacy_key() -> None:
    """medium 沿用 legacy 键，兼容 night_worker 03:00 预计算（不迁移夜班脚本）。"""
    assert pc.recommend_cache_key("medium") == "recommendations"


@pytest.mark.parametrize("bad", [None, "", "day", "MEDIUM", "短线"])
def test_recommend_cache_key_unknown_falls_back_to_medium(bad) -> None:
    """非法 period 一律兜底到 medium 键，口径与 recommend_engine 一致。"""
    assert pc.recommend_cache_key(bad) == "recommendations"


def test_recommend_cache_key_is_distinct_per_period() -> None:
    """三个周期必须映射到三个不同的键 —— 否则周期选择又是空的。"""
    keys = {pc.recommend_cache_key(p) for p in ("short", "medium", "long")}
    assert keys == {"recommendations_short", "recommendations", "recommendations_long"}
    assert len(keys) == 3


# ============================================================
# 2. 三键互不串味（这是本次缺陷的核心：period 无关会串味）
# ============================================================
def test_period_keys_are_stored_and_loaded_separately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """三份数据写进三个键，读回来必须各归各，不能交叉污染。"""
    monkeypatch.setattr(pc, "PRECOMPUTED_DIR", tmp_path)

    pc.save_precomputed("recommendations", {"recommendations": [{"code": "MED"}]})
    pc.save_precomputed("recommendations_short", {"recommendations": [{"code": "SHRT"}]})
    pc.save_precomputed("recommendations_long", {"recommendations": [{"code": "LONG"}]})

    def first_code(key: str) -> str:
        data = pc.get_precomputed(key)
        assert data is not None, f"{key} 缓存读不到"
        return data["recommendations"][0]["code"]

    assert first_code("recommendations") == "MED"
    assert first_code("recommendations_short") == "SHRT"
    assert first_code("recommendations_long") == "LONG"


def test_short_and_long_miss_do_not_fall_back_to_medium(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """只有中线缓存时，短线/长线必须读不到（绝不能回退到中线那份）。

    这是修复前 bug 的直接反例：修复前 short/long 会命中 period 无关的
    ``recommendations``，返回中线的列表。
    """
    monkeypatch.setattr(pc, "PRECOMPUTED_DIR", tmp_path)
    pc.save_precomputed("recommendations", {"recommendations": [{"code": "MED"}]})

    assert pc.get_precomputed("recommendations") is not None
    assert pc.get_precomputed("recommendations_short") is None
    assert pc.get_precomputed("recommendations_long") is None


def test_endpoint_key_resolution_matches_stored_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """用 `recommend_cache_key(period)` 定位缓存，三个周期读到各自的数据。

    直接模拟 `/api/recommend/stocks` 的读取路径（get_precomputed 用
    recommend_cache_key 的结果作 key），证明 period 维度真正落在缓存键上。
    """
    monkeypatch.setattr(pc, "PRECOMPUTED_DIR", tmp_path)
    payloads = {
        "short": {"recommendations": [{"code": "SHRT"}]},
        "medium": {"recommendations": [{"code": "MED"}]},
        "long": {"recommendations": [{"code": "LONG"}]},
    }
    for period, payload in payloads.items():
        pc.save_precomputed(pc.recommend_cache_key(period), payload)

    for period, payload in payloads.items():
        got = pc.get_precomputed(pc.recommend_cache_key(period))
        assert got is not None
        assert got["recommendations"][0]["code"] == payload["recommendations"][0]["code"]


# ============================================================
# 3. TTL 一致
# ============================================================
def test_recommendation_period_keys_share_12h_ttl() -> None:
    """短线/长线要和 legacy 中线一样是 12h，不许掉到 `get` 的默认 2h。"""
    assert pc._PRECOMPUTED_TTL.get("recommendations") == 43200
    assert pc._PRECOMPUTED_TTL.get("recommendations_short") == 43200
    assert pc._PRECOMPUTED_TTL.get("recommendations_long") == 43200


# ============================================================
# 4. HTTP 层接线：`/api/recommend/stocks` 读/写都用 period 键
# ============================================================
@pytest.mark.parametrize("period,expected_key", [
    ("short", "recommendations_short"),
    ("medium", "recommendations"),
    ("long", "recommendations_long"),
])
def test_endpoint_reads_period_keyed_cache(monkeypatch, period: str, expected_key: str) -> None:
    """锁住读取路径：接口按 period 取缓存键，不再写死 `recommendations`。

    变异检测：若有人把接口里的 `recommend_cache_key(period)` 改回字面量
    `"recommendations"`，short/long 这条用例立刻挂（期望键不符）。
    """
    seen: list = []
    fake = {"recommendations": [{"code": "X"}]}
    monkeypatch.setattr(pc, "get_precomputed", lambda key: (seen.append(key), fake)[1])
    monkeypatch.setattr(misc, "_trigger_recommend_update", lambda *a, **k: None)

    asyncio.run(misc.api_recommend_stocks(userId="", topN=10, pool="hot", period=period))

    assert seen == [expected_key]


@pytest.mark.parametrize("period,expected_key", [
    ("short", "recommendations_short"),
    ("medium", "recommendations"),
    ("long", "recommendations_long"),
])
def test_background_update_saves_to_period_key(
    monkeypatch, period: str, expected_key: str
) -> None:
    """锁住写入路径：后台线程把结果写进 period 键，不是共用一个 key。"""
    saved: list = []

    def _fake_get_stock_recommendations(userId, topN, pool, period):
        return {"recommendations": [{"code": period}], "period": period}

    monkeypatch.setattr(re_mod, "get_stock_recommendations", _fake_get_stock_recommendations)
    monkeypatch.setattr(pc, "save_precomputed", lambda key, data: saved.append(key))

    # 把后台线程同步化，避免依赖时序
    class _SyncThread:
        def __init__(self, target=None, **kwargs):
            self._target = target

        def start(self):
            self._target()

        def join(self):
            pass

    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(misc, "_recommend_computing", False)

    misc._trigger_recommend_update("u", 10, "hot", period)

    assert saved == [expected_key]


# ============================================================
# 5. file_cache 兜底分支也必须 period 分键（QA 实测漏修点）
# ============================================================
def _make_file_cache_dir(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _write_file_cache(cache_dir: Path, period: str, code: str, mtime: float) -> None:
    fp = cache_dir / f"recommend_rec__hot_10_{period}.json"
    fp.write_text(json.dumps({"recommendations": [{"code": code}], "period": period}))
    os.utime(fp, (mtime, mtime))


def test_file_cache_fallback_is_period_scoped(monkeypatch, tmp_path: Path) -> None:
    """file_cache 兜底 glob 必须锁 period：short 文件 mtime 最新时，medium 请求
    不能命中 short 那份（修复前 glob 全量按 mtime 取最新会串味）。"""
    cache_dir = _make_file_cache_dir(tmp_path)
    # mtime 顺序：short 最新 > long 次之 > medium 最旧。修复前的 glob 会把
    # medium/long 请求都导向 mtime 最新的 short。
    _write_file_cache(cache_dir, "short", "SHRT", mtime=300.0)
    _write_file_cache(cache_dir, "long", "LONG", mtime=200.0)
    _write_file_cache(cache_dir, "medium", "MED", mtime=100.0)

    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pc, "get_precomputed", lambda key: None)  # precomputed 全 miss
    monkeypatch.setattr(misc, "_trigger_recommend_update", lambda *a, **k: None)

    def code_for(period: str) -> str:
        r = asyncio.run(misc.api_recommend_stocks(
            userId="", topN=10, pool="hot", period=period))
        return r["recommendations"][0]["code"]

    assert code_for("short") == "SHRT"
    assert code_for("medium") == "MED"
    assert code_for("long") == "LONG"


def test_file_cache_fallback_triggers_background_refresh(
    monkeypatch, tmp_path: Path
) -> None:
    """命中 file_cache 兜底也要触发后台刷新，让 period 键的 precomputed 重算。

    否则 precomputed 被作废后，只要 file_cache 还在（4h TTL），precomputed
    永远不会重新生成，接口会一直吃这份历史缓存。
    """
    cache_dir = _make_file_cache_dir(tmp_path)
    _write_file_cache(cache_dir, "medium", "MED", mtime=100.0)

    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pc, "get_precomputed", lambda key: None)
    calls: list = []
    monkeypatch.setattr(misc, "_trigger_recommend_update", lambda *a: calls.append(a))

    asyncio.run(misc.api_recommend_stocks(userId="u1", topN=10, pool="hot", period="medium"))

    assert calls == [("u1", 10, "hot", "medium")]
