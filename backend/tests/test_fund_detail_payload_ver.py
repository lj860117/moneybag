"""
fund_detail.py 缓存「载荷形状版本门」回归测试（v9.9.40）
=====================================================
背景（"改对了但最长 72h 不生效"）：
  v9.9.39 把 `industry_tag` 加进了 fund_detail 的**共享结果**。但
  `fund_detail()` 用 `_get_cached(..., allow_stale=True)` 在 72h 内直接
  早退返回旧载荷；服务器上 682 个 `data/_cache/fund_detail/*.json` 都是
  v9.9.39 之前写的（没有 industry_tag 键）。`cache_warmer._warm_fund_details`
  只是 `GET /api/fund/detail/{code}`，照样命中同一早退，**从不强制刷新**。
  结果：修复对已缓存基金最长 72h 不可见（实测 industry_tag=None）。

修法：给详情缓存加「载荷形状版本门」——
  - 模块常量 `_DETAIL_PAYLOAD_VER`；
  - `_get_cached(..., require_pv=VER)` 按调用点 opt-in：信封里 `pv` 与
    require_pv 不符一律视为 miss（fresh 与 stale 两条路径都拦）；
  - `_set_cached(..., pv=VER)` 把版本写进信封；
  - 版本不符只"视为 miss"，**不删文件**（重算后覆盖即可）。

本文件测什么：
  ① 文件信封无 pv → require_pv 校验下返回 None
  ② 信封 pv == VER → 正常返回
  ③ 过期但 shape 是旧的（allow_stale=True 且 age 在 TTL~72h）→ 仍返回 None
     （本 bug 的核心路径，不能只测 fresh 分支）
  ④ 不传 require_pv → 旧信封照常返回（证明 opt-in 没波及别的调用点）
  ⑤ 内存分支：内存命中时同样校验 pv
  ⑥ _set_cached 把 pv 写进文件信封
  ⑦ 集成：旧形状共享缓存 → fund_detail 视为 miss 并重算（不再早退）
"""
import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKEND_DIR = Path(__file__).parent.parent

STALE_MIN = 259200  # 72h，与 _get_cached 的 stale 上限一致


@pytest.fixture
def fd(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    import api.fund_detail as fd
    importlib.reload(fd)
    yield fd


def _write_file_cache(fd, key, payload, age_sec, pv=None):
    """直接写一个缓存文件（模拟线上旧缓存），返回路径。"""
    path = os.path.join(fd._DETAIL_CACHE_DIR, f"{key}.json")
    os.makedirs(fd._DETAIL_CACHE_DIR, exist_ok=True)
    rec = {"v": payload, "t": time.time() - age_sec}
    if pv is not None:
        rec["pv"] = pv
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return path


# ============================================================
# ① 文件信封无 pv → miss
# ============================================================

def test_file_cache_without_pv_is_miss(fd):
    key = "fund_detail_000001"
    _write_file_cache(fd, key, {"code": "000001", "marker": "old"}, age_sec=10)

    got = fd._get_cached(key, allow_stale=True, require_pv=fd._DETAIL_PAYLOAD_VER)
    assert got is None, "无 pv 的旧形状载荷不得被返回"


# ============================================================
# ② 信封 pv == VER → 正常返回
# ============================================================

def test_file_cache_with_matching_pv_returns(fd):
    key = "fund_detail_000002"
    payload = {"code": "000002", "marker": "new"}
    _write_file_cache(fd, key, payload, age_sec=10, pv=fd._DETAIL_PAYLOAD_VER)

    got = fd._get_cached(key, allow_stale=True, require_pv=fd._DETAIL_PAYLOAD_VER)
    assert got == payload


# ============================================================
# ③ 过期但旧 shape → 仍必须 miss（本 bug 核心路径）
# ============================================================

def test_stale_old_shape_cache_is_still_miss(fd):
    """allow_stale=True 且 age 落在 (TTL, 72h) 之间，但载荷形状是旧的 → None。

    这正是生产 bug 的路径：旧缓存已过期，靠 stale-while-revalidate 被返回，
    导致 industry_tag 缺失。加了 pv 门后必须视为 miss。
    """
    key = "fund_detail_000003"
    age = fd._CACHE_TTL + 3600  # 已过期
    assert fd._CACHE_TTL < age < STALE_MIN, "测试前提：age 必须落在 TTL~72h 的 stale 区间"
    _write_file_cache(fd, key, {"code": "000003", "marker": "old"}, age_sec=age)

    got = fd._get_cached(key, allow_stale=True, require_pv=fd._DETAIL_PAYLOAD_VER)
    assert got is None, "stale + 旧形状也必须 miss"


def test_stale_matching_pv_cache_returns(fd):
    """对照：同样 stale，但 pv 匹配 → 允许返回旧数据（stale-while-revalidate 仍生效）。"""
    key = "fund_detail_000003b"
    payload = {"code": "000003b", "marker": "new"}
    _write_file_cache(fd, key, payload, age_sec=fd._CACHE_TTL + 3600,
                      pv=fd._DETAIL_PAYLOAD_VER)
    assert fd._get_cached(key, allow_stale=True,
                          require_pv=fd._DETAIL_PAYLOAD_VER) == payload


# ============================================================
# ④ 不传 require_pv → 旧信封照常返回（opt-in 无副作用）
# ============================================================

def test_without_require_pv_old_shape_is_returned(fd):
    key_fresh = "fund_detail_000004"
    _write_file_cache(fd, key_fresh, {"code": "000004"}, age_sec=10)
    assert fd._get_cached(key_fresh, allow_stale=True) == {"code": "000004"}

    key_stale = "fund_detail_000005"
    _write_file_cache(fd, key_stale, {"code": "000005"}, age_sec=fd._CACHE_TTL + 3600)
    assert fd._get_cached(key_stale, allow_stale=True) == {"code": "000005"}

    # 内存里没有、文件也没有的 key → None（行为不变）
    assert fd._get_cached("fund_detail_不存在", allow_stale=True) is None


# ============================================================
# ⑤ 内存分支：同样校验 pv
# ============================================================

def test_memory_branch_checks_pv(fd):
    key = "fund_detail_000006"
    payload = {"code": "000006"}
    fd._set_cached(key, payload, pv=fd._DETAIL_PAYLOAD_VER)

    # TTL 内、pv 匹配 → 命中
    assert fd._get_cached(key, require_pv=fd._DETAIL_PAYLOAD_VER) == payload
    # pv 不匹配 → miss
    assert fd._get_cached(key, require_pv=fd._DETAIL_PAYLOAD_VER + 1) is None


def test_memory_old_shape_is_miss(fd):
    key = "fund_detail_000007"
    fd._set_cached(key, {"code": "000007"})  # 不写 pv（旧形状）
    assert fd._get_cached(key, require_pv=fd._DETAIL_PAYLOAD_VER) is None
    # 不传 require_pv 时，同一内存条目仍可读（证明只是 opt-in 生效）
    assert fd._get_cached(key) == {"code": "000007"}


# ============================================================
# ⑥ _set_cached 把 pv 写进文件信封
# ============================================================

def test_set_cached_writes_pv_to_file_envelope(fd):
    key = "fund_detail_000008"
    fd._set_cached(key, {"code": "000008"}, pv=fd._DETAIL_PAYLOAD_VER)

    path = os.path.join(fd._DETAIL_CACHE_DIR, f"{key}.json")
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        rec = json.load(f)
    assert rec.get("pv") == fd._DETAIL_PAYLOAD_VER

    # 不传 pv 时信封里不应出现 pv 键（保持旧格式）
    key2 = "fund_detail_000009"
    fd._set_cached(key2, {"code": "000009"})
    with open(os.path.join(fd._DETAIL_CACHE_DIR, f"{key2}.json"), encoding="utf-8") as f:
        rec2 = json.load(f)
    assert "pv" not in rec2


# ============================================================
# ⑦ 集成：旧形状共享缓存 → fund_detail 视为 miss 并重算
# ============================================================

def _install_offline_mocks(monkeypatch, fd, code, name):
    import services.tushare_data as tushare_data
    monkeypatch.setattr(tushare_data, "get_fund_manager", lambda c: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_portfolio", lambda c: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_share", lambda ts_code, days=10: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_extra_info_ak", lambda c: {})
    monkeypatch.setattr(tushare_data, "is_configured", lambda: False)
    monkeypatch.setattr(tushare_data, "get_fund_nav", lambda *a, **k: {})
    monkeypatch.setattr(tushare_data, "_call_tushare", lambda *a, **k: [])

    import services.fund_rank as fund_rank
    monkeypatch.setattr(
        fund_rank, "get_fund_dynamic_info",
        lambda c: {"code": c, "name": name, "nav": 1.234, "returns": {}, "fee": ""},
    )
    monkeypatch.setattr(fund_rank, "_load_fund_rank_data", lambda *a, **k: None)

    try:
        import services.fund_risk_adjusted as fra
        monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", lambda *a, **k: None)
        monkeypatch.setattr(fra, "set_risk_adjusted_cache", lambda *a, **k: None)
    except Exception:
        pass
    try:
        import services.utils as utils
        monkeypatch.setattr(utils, "ak_call", lambda *a, **k: None)
    except Exception:
        pass

    monkeypatch.setattr(fd, "_get_nav_history_cached", lambda *a, **k: [])
    try:
        import api.signals as signals
        monkeypatch.setattr(signals, "_enrich_trend_forecast", lambda *a, **k: None, raising=False)
    except Exception:
        pass


def test_fund_detail_recomputes_when_shared_cache_lacks_pv(fd, monkeypatch):
    """写一个无 pv 的旧形状共享缓存（带哨兵 marker），调用 fund_detail 后
    必须**重算**（结果里不含哨兵），且重算后文件信封带上 pv。"""
    code = "008888"
    name = "华夏中证半导体ETF联接A"
    _install_offline_mocks(monkeypatch, fd, code, name)

    shared_key = f"fund_detail_{code}"
    _write_file_cache(fd, shared_key,
                      {"code": code, "name": name, "nav": 1.234, "marker": "OLD_SHAPE"},
                      age_sec=10)  # fresh 但无 pv

    result = fd.fund_detail(code)  # 不带 userId → else 分支读共享缓存

    assert result.get("marker") != "OLD_SHAPE", (
        "旧形状共享缓存被早退返回 —— pv 门没生效")
    assert result.get("industry_tag"), "重算后应带上 industry_tag（v9.9.39 新字段）"

    # 重算后文件信封应带 pv
    with open(os.path.join(fd._DETAIL_CACHE_DIR, f"{shared_key}.json"), encoding="utf-8") as f:
        rec = json.load(f)
    assert rec.get("pv") == fd._DETAIL_PAYLOAD_VER
